import asyncio
import base64
import nest_asyncio
import urllib.parse
import re
import random
import os
import sys
from playwright.async_api import async_playwright

# Google Colab ও Asyncio সাপোর্ট
nest_asyncio.apply()

# ==============================================================================
# ⚙️ ১. কনফিগারেশন এরিয়া (Environment Variables & Category Map)
# ==============================================================================
MAIN_SITE_URL = os.environ.get("MAIN_SITE_URL", "https://cinefreak.net/").rstrip("/") + "/"
SCAN_MODE = os.environ.get("SCAN_MODE", "ALL").strip()

HISTORY_FILE = "history/scraped_history.txt"
TARGET_LIMIT_MOVIES = 10  # প্রতি ক্যাটাগরিতে সর্বোচ্চ ১০টি ইউনিক মুভি

CATEGORIES_MAP = {
    "Animation": {
        "slug": "animation-movies",
        "file": "categories/Animation/animation_movies.txt"
    },
    "Bangla Movies": {
        "slug": "bangla-movies",
        "file": "categories/Bangla_Movies/bangla_movies.txt"
    },
    "English Movies": {
        "slug": "english-movies",
        "file": "categories/English_Movies/english_movies.txt"
    },
    "Hindi Movies": {
        "slug": "hindi-movies",
        "file": "categories/Hindi_Movies/hindi_movies.txt"
    },
    "Hindi Dubbed Movies": {
        "slug": "hindi-dubbed-movies",
        "file": "categories/Dubbed/Hindi_Dubbed/hindi_dubbed_movies.txt"
    },
    "Bangla Dubbed": {
        "slug": "bangla-dubbed-movies",
        "file": "categories/Dubbed/Bangla_Dubbed/bangla_dubbed_movies.txt"
    },
    "Tamil Movies": {
        "slug": "tamil-movies",
        "file": "categories/South_Indian/Tamil/tamil_movies.txt"
    },
    "Malayalam Movies": {
        "slug": "malayalam-movies",
        "file": "categories/South_Indian/Malayalam/malayalam_movies.txt"
    },
    "Kannada Movies": {
        "slug": "kannada-movies",
        "file": "categories/South_Indian/Kannada/kannada_movies.txt"
    },
    "Telugu Movies": {
        "slug": "telugu-movies",
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
# 🎯 ২. হেল্পার ফাংশনসমূহ (আপনার অরিজিনাল কোড)
# ==============================================================================
def clean_resolution_label(text):
    """টেক্সট থেকে রেজুলেশনের সঠিক লেবেল বের করার ফাংশন"""
    text_upper = text.upper()
    
    res_match = re.search(r'((?:HEVC|HQ|FHD|HD|WEB-DL|4K)?\s*(?:1080P|2160P|4K)\s*(?:HEVC)?)', text_upper)
    if res_match:
        label = res_match.group(1).strip()
        label = re.sub(r'\s+', ' ', label)
        return label
    
    if "4K" in text_upper or "2160P" in text_upper:
        return "4K 2160P"
    if "HEVC" in text_upper:
        return "HEVC 1080P"
    
    return "HD 1080P"

def is_genuine_direct_stream_url(url):
    """শুধুমাত্র আসল .mkv/.mp4 বা r2.dev স্ট্রিম লিঙ্ক ফিল্টার করার ফাংশন"""
    u_lower = url.lower()
    
    if any(junk in u_lower for junk in ["google-analytics", "cinecloud.site", "neodrive.site", "ping.gif", "jwpltx", "collect?"]):
        return False
        
    if ("r2.dev" in u_lower or u_lower.endswith(".mkv") or u_lower.endswith(".mp4")) and ("http://" in u_lower or "https://" in u_lower):
        return True
        
    return False


# ==============================================================================
# 🎬 ৩. সমান্তরাল পাইপলাইন প্রসেসর (আপনার অরিজিনাল কোড)
# ==============================================================================
async def process_movie_parallel_pipeline(browser, movie_url, movie_idx, default_category_name):
    movie_captured_data = {}
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

        # আসল ক্যাটাগরি ও ট্যাগ এক্সট্র্যাক্ট করা
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

        # ড্রপডাউন থাকলে এক্সপ্যান্ড করা
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

        # পেজের সব বাটন এবং তার আশেপাশের রেজুলেশন টেক্সট এক্সট্র্যাক্ট
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

        # 🎯 ফিল্টারিং: শুধুমাত্র ১০৮০p, 2K এবং ৪K রেজুলেশন সংগ্রহ
        target_buttons = []
        for btn in all_buttons:
            combined_txt = f"{btn['button_text']} {btn['parent_text']}".lower()
            
            if any(low in combined_txt for low in ["720p", "480p", "360p"]):
                if not any(high in combined_txt for high in ["1080p", "2160p", "4k"]):
                    continue
            
            if any(high in combined_txt for high in ["1080p", "2160p", "4k"]):
                target_buttons.append(btn)

        if not target_buttons:
            print(f"❌ [MOVIE {movie_idx}/{TARGET_LIMIT_MOVIES}] No 1080p or 4K resolution available. Skipping lower qualities.", flush=True)
            return movie_url, movie_title, movie_categories, {}

        print(f"✅ [MOVIE {movie_idx}/{TARGET_LIMIT_MOVIES}] Found {len(target_buttons)} 1080p+ stream option(s). Processing all...", flush=True)

        # প্রতিটি ১০৮০p+ রেজুলেশনের জন্য লিঙ্ক প্রসেস করা
        for idx, btn_info in enumerate(target_buttons, 1):
            res_label = clean_resolution_label(f"{btn_info['parent_text']} {btn_info['button_text']}")
            target_gateway_url = btn_info["url"]

            current_stream_urls = set()

            # ব্যাকগ্রাউন্ড নেটওয়ার্ক ইন্টারসেপ্টর
            def handle_response(response):
                try:
                    raw_url = response.url
                    decoded_url = urllib.parse.unquote(raw_url)
                    
                    parsed_qs = urllib.parse.parse_qs(urllib.parse.urlparse(decoded_url).query)
                    for param in ['mu', 'id', 'link', 'url', 'file']:
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
                
                # টাইমার ও Verify Download বাটন
                verify_btn = sub_page.locator("#btn-text")
                await verify_btn.wait_for(state="visible", timeout=12000)
                await verify_btn.click()
                await sub_page.wait_for_timeout(1500)

                # Get Download Link বাটন
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

                # প্লেয়ার প্লে সিমুলেশন
                await player_page.bring_to_front()
                try:
                    await player_page.mouse.click(640, 360)
                    await player_page.locator("video, .jw-display-icon-container, svg").first.click(timeout=3000)
                except Exception:
                    pass

                # ডাইরেক্ট মিডিয়া লিঙ্ক ক্যাচ করার জন্য ডাইনামিক অপেক্ষা
                for _ in range(12):
                    if current_stream_urls:
                        break
                    await asyncio.sleep(0.5)

                if current_stream_urls:
                    movie_captured_data[res_label] = list(current_stream_urls)
                    print(f"   ✅ Captured [{res_label}]: {list(current_stream_urls)[0][:60]}...", flush=True)
                
            except Exception:
                pass
            finally:
                await sub_page.close()

    finally:
        await context.close()

    return movie_url, movie_title, movie_categories, movie_captured_data


# ==============================================================================
# 🎯 ৪. মেইন অর্কেস্ট্রেটর (Multi-Category Engine)
# ==============================================================================
async def main():
    os.makedirs("history", exist_ok=True)

    scraped_history = set()
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            scraped_history = set(line.strip() for line in f if line.strip())

    # SCAN_MODE অনুযায়ী ক্যাটাগরি ফিল্টার
    target_categories = {}
    if SCAN_MODE == "ALL":
        target_categories = CATEGORIES_MAP
    else:
        for key, val in CATEGORIES_MAP.items():
            if key.lower().replace(" ", "").replace("-", "") == SCAN_MODE.lower().replace(" ", "").replace("-", ""):
                target_categories[key] = val
                break
        if not target_categories:
            print(f"⚠️ SCAN_MODE '{SCAN_MODE}' matches no specific key. Scanning ALL categories.", flush=True)
            target_categories = CATEGORIES_MAP

    sem = asyncio.Semaphore(3)

    async def safe_process(browser_instance, movie_url, movie_idx, cat_name):
        async with sem:
            return await process_movie_parallel_pipeline(browser_instance, movie_url, movie_idx, cat_name)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=LAUNCH_ARGS)
        
        try:
            for cat_display_name, config in target_categories.items():
                cat_slug = config["slug"]
                output_filename = config["file"]
                os.makedirs(os.path.dirname(output_filename), exist_ok=True)

                print(f"\n==================================================", flush=True)
                print(f"📂 [STEP 1] Navigating to Category Page: {cat_display_name} ({cat_slug})...", flush=True)
                print(f"==================================================", flush=True)

                page_main = await browser.new_page()

                # এড-ব্লকিং রাউটিং
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

                while len(new_movie_urls) < TARGET_LIMIT_MOVIES:
                    print(f"📄 Scanning Category Page {current_page_num}...", flush=True)
                    links = await page_main.eval_on_selector_all("a", "elements => elements.map(e => e.href)")
                    
                    for link in links:
                        if link and ("full-movie-download" in link or link.endswith("-download/")):
                            if not any(c in link for c in ["-movies/", "/category/", "/genre/"]):
                                if link not in scraped_history and link not in new_movie_urls:
                                    new_movie_urls.append(link)
                                    if len(new_movie_urls) >= TARGET_LIMIT_MOVIES:
                                        break
                    
                    if len(new_movie_urls) >= TARGET_LIMIT_MOVIES:
                        break

                    current_page_num += 1
                    try:
                        next_page_url = f"{MAIN_SITE_URL}{cat_slug}/page/{current_page_num}/"
                        await page_main.goto(next_page_url, timeout=25000, wait_until="domcontentloaded")
                    except Exception:
                        print("⚠️ End of pagination reached.", flush=True)
                        break

                await page_main.close()

                if not new_movie_urls:
                    print(f"ℹ️ No new unique movies found for category: {cat_display_name}", flush=True)
                    continue

                print(f"🚀 Found {len(new_movie_urls)} unique movies in [{cat_display_name}]. Starting parallel workers...\n", flush=True)

                tasks = [safe_process(browser, movie_url, idx, cat_display_name) for idx, movie_url in enumerate(new_movie_urls, 1)]
                parallel_results = await asyncio.gather(*tasks)

                # 📝 [আপনার অরিজিনাল আউটপুট ফরম্যাট অনুযায়ী ফাইল রাইটিং]
                final_output_results = {}
                with open(HISTORY_FILE, "a", encoding="utf-8") as h_file:
                    for movie_url, title, categories, res_data in parallel_results:
                        final_output_results[movie_url] = {"title": title, "categories": categories, "data": res_data}
                        if res_data:
                            h_file.write(f"{movie_url}\n")
                            scraped_history.add(movie_url)

                with open(output_filename, "a", encoding="utf-8") as f:
                    for idx, (movie_url, info) in enumerate(final_output_results.items(), 1):
                        title = info["title"]
                        categories = info["categories"]
                        res_dict = info["data"]
                        
                        year_match = re.search(r'\b(20\d{2}|19\d{2})\b', title)
                        year = year_match.group(1) if year_match else "N/A"
                        clean_name = title.split(f"({year})")[0].split(year)[0].strip() if year_match else title
                        clean_name = re.sub(r'[\s\-\[\]\(\)]+$', '', clean_name).strip()
                        
                        f.write(f"Movie-{idx}\n")
                        f.write(f"Movie name: {clean_name}\n")
                        f.write(f"Movie Category: {categories}\n")
                        f.write(f"Movie year: {year}\n\n")
                        
                        if res_dict:
                            res_idx = 1
                            for res_name, urls in res_dict.items():
                                for u in urls:
                                    f.write(f"RESOLUTION {res_idx}: {res_name}\n")
                                    f.write(f"STREAM Link {res_idx}: {u}\n\n")
                                    res_idx += 1
                        else:
                            f.write("❌ No direct 1080p+ stream links found for this movie.\n\n")
                        
                        f.write("=" * 80 + "\n\n")

                print(f"✅ Saved results for [{cat_display_name}] to: '{output_filename}'", flush=True)

        finally:
            await browser.close()

    print("\n🎉 All Scheduled Category Scraping Completed Successfully!", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
