import asyncio
import base64
import json
import nest_asyncio
import urllib.parse
import re
import random
import os
import sys
from playwright.async_api import async_playwright

nest_asyncio.apply()

# ==============================================================================
# ⚙️ ১. কনফিগারেশন এবং ক্যাটাগরি তালিকা
# ==============================================================================
MAIN_SITE_URL = os.environ.get("MAIN_SITE_URL", "https://cinefreak.net/").rstrip("/") + "/"
SCAN_MODE = os.environ.get("SCAN_MODE", "AUTO").strip()

STATE_FILE = "history/tracker_state.json"
TARGET_LIMIT_MOVIES = 10

CATEGORIES_LIST = [
    "Bangla Movies",
    "English Movies",
    "Hindi Movies",
    "Hindi Dubbed Movies",
    "Bangla Dubbed",
    "Tamil Movies",
    "Malayalam Movies",
    "Kannada Movies",
    "Telugu Movies"
]

CATEGORIES_MAP = {
    "Bangla Movies": {
        "slug": "bangla-movies",
        "dir": "categories/Bangla_Movies",
        "file": "categories/Bangla_Movies/bangla_movies.txt"
    },
    "English Movies": {
        "slug": "english-movies",
        "dir": "categories/English_Movies",
        "file": "categories/English_Movies/english_movies.txt"
    },
    "Hindi Movies": {
        "slug": "hindi-movies",
        "dir": "categories/Hindi_Movies",
        "file": "categories/Hindi_Movies/hindi_movies.txt"
    },
    "Hindi Dubbed Movies": {
        "slug": "hindi-dubbed-movies",
        "dir": "categories/Dubbed/Hindi_Dubbed",
        "file": "categories/Dubbed/Hindi_Dubbed/hindi_dubbed_movies.txt"
    },
    "Bangla Dubbed": {
        "slug": "bangla-dubbed-movies",
        "dir": "categories/Dubbed/Bangla_Dubbed",
        "file": "categories/Dubbed/Bangla_Dubbed/bangla_dubbed_movies.txt"
    },
    "Tamil Movies": {
        "slug": "tamil-movies",
        "dir": "categories/South_Indian/Tamil",
        "file": "categories/South_Indian/Tamil/tamil_movies.txt"
    },
    "Malayalam Movies": {
        "slug": "malayalam-movies",
        "dir": "categories/South_Indian/Malayalam",
        "file": "categories/South_Indian/Malayalam/malayalam_movies.txt"
    },
    "Kannada Movies": {
        "slug": "kannada-movies",
        "dir": "categories/South_Indian/Kannada",
        "file": "categories/South_Indian/Kannada/kannada_movies.txt"
    },
    "Telugu Movies": {
        "slug": "telugu-movies",
        "dir": "categories/South_Indian/Telugu",
        "file": "categories/South_Indian/Telugu/telugu_movies.txt"
    }
}

LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-setuid-sandbox"
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
]

AD_AND_ANALYTICS_DOMAINS = [
    "google-analytics", "analytics", "doubleclick", "popads", "popcash", 
    "bet365", "1xbet", "adsterra", "exoclick", "propellerads", "monetag", "clickadu"
]

# ==============================================================================
# 🔄 ২. স্টেট ট্র্যাকার
# ==============================================================================
def load_tracker_state():
    os.makedirs("history", exist_ok=True)
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"current_category_index": 0, "run_count": 1}

def save_tracker_state(state):
    os.makedirs("history", exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

# ==============================================================================
# 🎯 ৩. স্মার্ট রেজুলেশন ও ফিল্টার (DS4K Bug & 720p Leak Fix)
# ==============================================================================
def detect_resolution_from_stream_url(stream_url):
    clean_path = urllib.parse.unquote(stream_url.split('?')[0]).upper()
    filename = clean_path.split('/')[-1]
    
    is_hevc = any(h in filename for h in ["HEVC", "H265", "H.265"])
    
    is_4k = "2160P" in filename or ("4K" in filename and "DS4K" not in filename)
    is_1080p = "1080P" in filename or "FHD" in filename
    
    if is_4k:
        return "4K 2160P HEVC" if is_hevc else "4K 2160P"
    elif is_1080p:
        return "HEVC 1080P" if is_hevc else "HD 1080P"
    
    return "HEVC 1080P" if is_hevc else "HD 1080P"

def is_genuine_direct_stream_url(url):
    u_lower = url.lower()
    
    if any(junk in u_lower for junk in ["yagaverse.net", "google-analytics", "cinecloud.site", "neodrive.site", "ping.gif", "jwpltx", "collect?", "facebook", "twitter"]):
        return False

    clean_path = u_lower.split('?')[0]
    filename = clean_path.split('/')[-1]
        
    if any(low in filename for low in ["720p", "480p", "360p"]):
        return False

    is_media = "r2.dev" in clean_path or filename.endswith(".mkv") or filename.endswith(".mp4") or filename.endswith(".m3u8")
    if not is_media or not (u_lower.startswith("http://") or u_lower.startswith("https://")):
        return False
        
    return True

# ==============================================================================
# 🎬 ৪. সমান্তরাল পাইপলাইন প্রসেসর
# ==============================================================================
async def process_movie_parallel_pipeline(browser, movie_url, movie_idx, default_category_name):
    movie_captured_data = []
    movie_title = "Movie Post"
    movie_categories = default_category_name

    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={"width": 1280, "height": 720}
    )
    
    try:
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
        page = await context.new_page()

        print(f"🎬 [MOVIE {movie_idx}/{TARGET_LIMIT_MOVIES}] Opening Movie Page: {movie_url}", flush=True)
        await page.goto(movie_url, timeout=40000, wait_until="domcontentloaded")

        raw_title = await page.title()
        movie_title = raw_title.split(" - ")[0].split(" Full Movie")[0].replace("Watch ", "").strip()

        movie_categories = await page.evaluate(f"""
            () => {{
                let catElements = document.querySelectorAll('a[rel="category tag"], .post-categories a, .cat-links a, .entry-meta a[href*="/category/"]');
                if (catElements.length > 0) {{
                    let cats = Array.from(catElements).map(e => e.innerText.trim()).filter(c => c.length > 0);
                    return [...new Set(cats)].join(', ');
                }}
                return "{default_category_name}";
            }}
        """)

        watch_online_locators = page.locator("a:has-text('Watch Online')")
        btn_count = await watch_online_locators.count()
        if btn_count > 0:
            for i in range(btn_count):
                try:
                    href = await watch_online_locators.nth(i).get_attribute("href")
                    if not href or href == "#" or "javascript" in href.lower():
                        await watch_online_locators.nth(i).click(timeout=1500)
                except Exception:
                    pass

        all_buttons = await page.evaluate("""
            () => {
                let matches = [];
                let anchors = Array.from(document.querySelectorAll('a[href*="generate.php"]'));
                anchors.forEach(a => {
                    let text = a.innerText.trim();
                    let blockText = "";
                    let current = a;
                    for (let i = 0; i < 4; i++) {
                        if (current.parentElement) {
                            current = current.parentElement;
                            let t = current.innerText || "";
                            if (/(1080p|2160p|4k|hevc|hq)/i.test(t)) {
                                blockText = t;
                                break;
                            }
                        }
                    }
                    if (!blockText && a.parentElement) blockText = a.parentElement.innerText;
                    matches.push({ button_text: text, parent_text: blockText, url: a.href });
                });
                return matches;
            }
        """)

        target_buttons = []
        for btn in all_buttons:
            combined_txt = f"{btn['button_text']} {btn['parent_text']}".lower()
            if any(low in combined_txt for low in ["720p", "480p", "360p"]):
                if not any(high in combined_txt for high in ["1080p", "2160p", "4k"]):
                    continue
            if any(high in combined_txt for high in ["1080p", "2160p", "4k", "hevc", "hq"]):
                target_buttons.append(btn)

        if not target_buttons:
            target_buttons = [b for b in all_buttons if "generate.php" in b["url"]]

        if not target_buttons:
            print(f"❌ [MOVIE {movie_idx}/{TARGET_LIMIT_MOVIES}] No 1080p or 4K resolution available.", flush=True)
            return movie_url, movie_title, movie_categories, []

        print(f"✅ [MOVIE {movie_idx}/{TARGET_LIMIT_MOVIES}] Found {len(target_buttons)} 1080p+ option(s). Processing...", flush=True)

        for idx, btn_info in enumerate(target_buttons, 1):
            target_gateway_url = btn_info["url"]
            current_stream_urls = set()

            def handle_response(response):
                try:
                    raw_url = response.url
                    decoded_url = urllib.parse.unquote(raw_url)
                    
                    parsed_qs = urllib.parse.parse_qs(urllib.parse.urlparse(decoded_url).query)
                    for param in ['id', 'mu', 'link', 'url', 'file']:
                        if param in parsed_qs:
                            val = parsed_qs[param][0]
                            decoded_val = urllib.parse.unquote(val)
                            if is_genuine_direct_stream_url(decoded_val):
                                current_stream_urls.add(decoded_val)
                                return
                            try:
                                padded = val + "=" * (-len(val) % 4)
                                decoded_str = base64.b64decode(padded).decode("utf-8")
                                if is_genuine_direct_stream_url(decoded_str):
                                    current_stream_urls.add(decoded_str)
                                    return
                            except Exception:
                                pass

                    if is_genuine_direct_stream_url(decoded_url):
                        current_stream_urls.add(decoded_url)
                except Exception:
                    pass

            context.on("response", handle_response)

            sub_page = await context.new_page()
            try:
                await sub_page.goto(target_gateway_url, timeout=30000, wait_until="domcontentloaded")
                
                verify_btn = sub_page.locator("#btn-text")
                await verify_btn.wait_for(state="visible", timeout=12000)
                await verify_btn.click()
                await sub_page.wait_for_timeout(1500)

                get_link_btn = sub_page.locator("#btn-text")
                await get_link_btn.wait_for(state="visible", timeout=12000)

                player_page = None
                for _ in range(4):
                    try:
                        await get_link_btn.click(timeout=2000)
                    except Exception:
                        pass
                    await sub_page.wait_for_timeout(1500)
                    
                    for p_tab in context.pages:
                        if p_tab != sub_page and p_tab != page:
                            if "cinecloud" in p_tab.url or "neodrive" in p_tab.url:
                                player_page = p_tab
                                break
                            else:
                                await p_tab.close()
                    if player_page:
                        break

                if not player_page:
                    player_page = sub_page

                await player_page.bring_to_front()
                try:
                    await player_page.mouse.click(640, 360)
                    await player_page.locator("video, .jw-display-icon-container, svg").first.click(timeout=3000)
                except Exception:
                    pass

                for _ in range(12):
                    if current_stream_urls:
                        break
                    await asyncio.sleep(0.5)

                if current_stream_urls:
                    for stream_url in current_stream_urls:
                        if not any(item['link'] == stream_url for item in movie_captured_data):
                            exact_res_label = detect_resolution_from_stream_url(stream_url)
                            movie_captured_data.append({
                                "resolution": exact_res_label,
                                "link": stream_url
                            })
                            print(f"   ✅ Captured [{exact_res_label}]: {stream_url[:65]}...", flush=True)
                
            except Exception:
                pass
            finally:
                await sub_page.close()

    finally:
        await context.close()

    return movie_url, movie_title, movie_categories, movie_captured_data

# ==============================================================================
# 🎯 ৫. মেইন কন্ট্রোলার (Clean Output Fix)
# ==============================================================================
async def main():
    state = load_tracker_state()
    cat_index = state.get("current_category_index", 0)
    run_count = state.get("run_count", 1)

    target_category_name = None
    is_auto_mode = False

    if SCAN_MODE in ["AUTO", "ALL"]:
        is_auto_mode = True
        target_category_name = CATEGORIES_LIST[cat_index]
        print(f"🔄 [ROTATIONAL SYSTEM] Running Active Category: '{target_category_name}' (Run {run_count}/3)", flush=True)
    elif SCAN_MODE == "FORCE_NEXT":
        is_auto_mode = True
        cat_index = (cat_index + 1) % len(CATEGORIES_LIST)
        run_count = 1
        target_category_name = CATEGORIES_LIST[cat_index]
        print(f"⏩ [FORCE NEXT] Switched to Next Category: '{target_category_name}' (Run 1/3)", flush=True)
    else:
        for cat_name in CATEGORIES_LIST:
            if cat_name.lower().replace(" ", "").replace("-", "") == SCAN_MODE.lower().replace(" ", "").replace("-", ""):
                target_category_name = cat_name
                break
        if not target_category_name:
            target_category_name = CATEGORIES_LIST[0]
        print(f"🎯 [MANUAL OVERRIDE] Target Category: '{target_category_name}'", flush=True)

    config = CATEGORIES_MAP[target_category_name]
    cat_slug = config["slug"]
    cat_dir = config["dir"]
    output_filename = config["file"]
    history_filename = os.path.join(cat_dir, "history.txt")

    os.makedirs(cat_dir, exist_ok=True)

    scraped_history = set()
    if os.path.exists(history_filename):
        with open(history_filename, "r", encoding="utf-8") as f:
            scraped_history = set(line.strip().rstrip('/') for line in f if line.strip())

    sem = asyncio.Semaphore(3)

    async def safe_process(browser_instance, movie_url, movie_idx, cat_name):
        async with sem:
            return await process_movie_parallel_pipeline(browser_instance, movie_url, movie_idx, cat_name)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=LAUNCH_ARGS)
        
        try:
            print(f"\n==================================================", flush=True)
            print(f"📂 Navigating to Category: {target_category_name} ({cat_slug})", flush=True)
            print(f"==================================================", flush=True)

            page_main = await browser.new_page()

            async def route_interceptor(route):
                url = route.request.url.lower()
                if any(ad in url for ad in AD_AND_ANALYTICS_DOMAINS) or url.endswith((".png", ".jpg", ".jpeg", ".woff2")):
                    await route.abort()
                else:
                    await route.continue_()

            await page_main.route("**/*", route_interceptor)

            category_url = f"{MAIN_SITE_URL}{cat_slug}/"
            await page_main.goto(category_url, timeout=35000, wait_until="domcontentloaded")

            new_movie_urls = []
            current_page_num = 1
            MAX_PAGE_SAFETY_LIMIT = 4

            while len(new_movie_urls) < TARGET_LIMIT_MOVIES and current_page_num <= MAX_PAGE_SAFETY_LIMIT:
                print(f"📄 Scanning Category Page {current_page_num}...", flush=True)
                
                links = await page_main.evaluate("""
                    () => {
                        let postAnchors = Array.from(document.querySelectorAll('article a, .post-card a, .type-post a, .entry-title a, h2 a, h3 a'));
                        return postAnchors.map(a => a.href).filter(href => href && href.startsWith('http'));
                    }
                """)

                if not links:
                    raw_links = await page_main.eval_on_selector_all("a", "elements => elements.map(e => e.href)")
                    links = [l for l in raw_links if l and ("full-movie-download" in l or l.endswith("-download/"))]

                for link in links:
                    link_clean = link.rstrip('/')
                    if ("-download" in link_clean or "full-movie" in link_clean or "web-dl" in link_clean):
                        if not any(junk in link_clean for junk in ["/category/", "/page/", "/tag/", "/genre/", "/author/", "/search/"]):
                            if link_clean not in scraped_history and link_clean not in new_movie_urls and f"{link_clean}/" not in scraped_history:
                                new_movie_urls.append(link)
                                if len(new_movie_urls) >= TARGET_LIMIT_MOVIES:
                                    break
                
                if len(new_movie_urls) >= TARGET_LIMIT_MOVIES:
                    break

                current_page_num += 1
                if current_page_num <= MAX_PAGE_SAFETY_LIMIT:
                    try:
                        next_page_url = f"{MAIN_SITE_URL}{cat_slug}/page/{current_page_num}/"
                        await page_main.goto(next_page_url, timeout=25000, wait_until="domcontentloaded")
                    except Exception:
                        break

            await page_main.close()

            if not new_movie_urls:
                print(f"ℹ️ No new unique movies found for category: {target_category_name}", flush=True)
            else:
                print(f"🚀 Found {len(new_movie_urls)} unique movies in [{target_category_name}]. Starting extraction...\n", flush=True)

                tasks = [safe_process(browser, movie_url, idx, target_category_name) for idx, movie_url in enumerate(new_movie_urls, 1)]
                parallel_results = await asyncio.gather(*tasks)

                # 📝 [FIX 1]: স্ক্যান হওয়া সবকটি মুভি ইউআরএল history.txt ফাইলে সেভ রাখা (যাতে একই মুভি বারবার স্ক্যান না হয়)
                with open(history_filename, "a", encoding="utf-8") as h_file:
                    for movie_url, title, categories, res_list in parallel_results:
                        h_file.write(f"{movie_url}\n")

                # 📝 [FIX 2]: শুধুমাত্র যেসব মুভির আসল স্ট্রিম লিংক পাওয়া যাবে, সেগুলোই আউটপুট টেক্সট ফাইলে সেভ করা
                with open(output_filename, "a", encoding="utf-8") as f:
                    valid_movie_count = 1
                    for movie_url, title, categories, res_list in parallel_results:
                        if res_list:
                            year_match = re.search(r'\b(20\d{2}|19\d{2})\b', title)
                            year = year_match.group(1) if year_match else "N/A"
                            clean_name = title.split(f"({year})")[0].split(year)[0].strip() if year_match else title
                            clean_name = re.sub(r'[\s\-\[\]\(\)]+$', '', clean_name).strip()
                            
                            f.write(f"Movie-{valid_movie_count}\n")
                            f.write(f"Movie name: {clean_name}\n")
                            f.write(f"Movie Category: {categories}\n")
                            f.write(f"Movie year: {year}\n\n")
                            
                            res_idx = 1
                            for item in res_list:
                                f.write(f"RESOLUTION {res_idx}: {item['resolution']}\n")
                                f.write(f"STREAM Link {res_idx}: {item['link']}\n\n")
                                res_idx += 1
                            
                            f.write("=" * 80 + "\n\n")
                            valid_movie_count += 1

                print(f"✅ Saved clean results for [{target_category_name}] to: '{output_filename}'", flush=True)

        finally:
            await browser.close()

    if is_auto_mode:
        if run_count >= 3:
            next_index = (cat_index + 1) % len(CATEGORIES_LIST)
            state["current_category_index"] = next_index
            state["run_count"] = 1
            print(f"🔄 [STATE UPDATE] Completed 3/3 runs for '{target_category_name}'. Rotated to next category: '{CATEGORIES_LIST[next_index]}'", flush=True)
        else:
            state["current_category_index"] = cat_index
            state["run_count"] = run_count + 1
            print(f"🔄 [STATE UPDATE] Completed run {run_count}/3 for '{target_category_name}'. Next scheduled run will be {run_count + 1}/3.", flush=True)
        
        save_tracker_state(state)

    print("\n🎉 Scraping Completed Successfully!", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
