import asyncio
import base64
import nest_asyncio
import urllib.parse
import re
import random
import os
import sys
from playwright.async_api import async_playwright

nest_asyncio.apply()

# ==============================================================================
# ⚙️ ১. কনফিগারেশন এবং ক্যাটাগরি ম্যাপ
# ==============================================================================
MAIN_SITE_URL = os.environ.get("MAIN_SITE_URL", "https://cinefreak.net/")
HISTORY_FILE = "history/scraped_history.txt"
LIMIT_MOVIES_PER_CATEGORY_RUN = 10

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

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
]

AD_DOMAINS = ["google-analytics", "analytics", "doubleclick", "popads", "popcash", "adsterra", "exoclick"]

def log(text):
    """বাফার মুক্ত ইনস্ট্যান্ট কনসোল প্রিন্ট"""
    print(text, flush=True)

# ==============================================================================
# 🎯 ২. হেল্পার ফাংশনসমূহ
# ==============================================================================
def detect_resolution_from_stream_url(stream_url, fallback_text=""):
    decoded_filename = urllib.parse.unquote(stream_url.split('/')[-1]).upper()
    
    is_hevc = any(h in decoded_filename for h in ["HEVC", "H265", "H.265"])
    is_4k = any(k in decoded_filename for k in ["4K", "2160P", "DS4K"])
    is_1080p = any(f in decoded_filename for f in ["1080P", "FHD"])
    is_720p = "720P" in decoded_filename
    
    if is_4k:
        return "4K 2160P HEVC" if is_hevc else "4K 2160P"
    elif is_1080p:
        return "HEVC 1080P" if is_hevc else "HD 1080P"
    elif is_720p:
        return "720P"
    
    fallback_upper = fallback_text.upper()
    if "4K" in fallback_upper or "2160P" in fallback_upper:
        return "4K 2160P"
    if "HEVC" in fallback_upper:
        return "HEVC 1080P"
    return "HD 1080P"

def is_valid_stream_url(url):
    u = url.lower()
    if any(junk in u for junk in ["google-analytics", "cinecloud.site", "neodrive.site", "ping.gif", "jwpltx", "collect?"]):
        return False
    return ("r2.dev" in u or u.endswith(".mkv") or u.endswith(".mp4")) and u.startswith("http")


# ==============================================================================
# 🎬 ৩. সিঙ্গেল মুভি পাইপলাইন
# ==============================================================================
async def process_single_movie(browser, movie_url, movie_idx, category_name):
    movie_captured_data = {}
    
    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={"width": 1280, "height": 720}
    )
    
    try:
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
        page = await context.new_page()

        log(f"🎬 [{category_name} | MOVIE {movie_idx}] Opening: {movie_url}")
        await page.goto(movie_url, timeout=25000, wait_until="domcontentloaded")

        raw_title = await page.title()
        movie_title = raw_title.split(" - ")[0].split(" Full Movie")[0].replace("Watch ", "").strip()

        # ড্রপডাউন এক্সপ্যান্ড
        watch_online_locators = page.locator("a:has-text('Watch Online'), button:has-text('Watch Online'), .accordion-title, details summary")
        for i in range(await watch_online_locators.count()):
            try:
                await watch_online_locators.nth(i).click(timeout=1000)
            except Exception:
                pass

        all_buttons = await page.evaluate("""
            () => {
                let matches = [];
                let anchors = Array.from(document.querySelectorAll('a[href*="generate.php"]'));
                anchors.forEach(a => {
                    let text = a.innerText.trim();
                    let blockText = a.parentElement ? a.parentElement.innerText.trim() : "";
                    matches.push({ button_text: text, parent_text: blockText, url: a.href });
                });
                return matches;
            }
        """)

        target_buttons = []
        for btn in all_buttons:
            combined = f"{btn['button_text']} {btn['parent_text']}".lower()
            if any(high in combined for high in ["1080p", "2160p", "4k", "hq"]):
                if not any(low in btn['button_text'].lower() for low in ["720p", "480p", "360p"]):
                    target_buttons.append(btn)

        if not target_buttons:
            target_buttons = [b for b in all_buttons if "generate.php" in b["url"]]

        if not target_buttons:
            log(f"   ⚠️ [{category_name} | MOVIE {movie_idx}] No download buttons found on page.")
            return movie_url, movie_title, category_name, {}

        for btn_info in target_buttons[:3]:
            target_gateway_url = btn_info["url"]
            current_stream_urls = set()

            def handle_response(response):
                try:
                    decoded_url = urllib.parse.unquote(response.url)
                    parsed_qs = urllib.parse.parse_qs(urllib.parse.urlparse(decoded_url).query)
                    for param in ['mu', 'id', 'link', 'url', 'file']:
                        if param in parsed_qs:
                            val = parsed_qs[param][0]
                            dec_val = urllib.parse.unquote(val)
                            if is_valid_stream_url(dec_val):
                                current_stream_urls.add(dec_val)
                                return
                            try:
                                padded = val + "=" * (-len(val) % 4)
                                dec_b64 = base64.b64decode(padded).decode("utf-8")
                                if is_valid_stream_url(dec_b64):
                                    current_stream_urls.add(dec_b64)
                                    return
                            except Exception:
                                pass

                    if is_valid_stream_url(decoded_url):
                        current_stream_urls.add(decoded_url)
                except Exception:
                    pass

            context.on("response", handle_response)
            sub_page = await context.new_page()

            try:
                await sub_page.goto(target_gateway_url, timeout=20000, wait_until="domcontentloaded")
                
                verify_btn = sub_page.locator("#btn-text, #verify-btn, a:has-text('Verify'), button:has-text('Verify'), .btn-main").first
                await verify_btn.wait_for(state="visible", timeout=12000)
                await verify_btn.click()
                await sub_page.wait_for_timeout(1000)

                get_link_btn = sub_page.locator("#btn-text, #get-link, a:has-text('Get Link'), a:has-text('Download'), .btn-main").first
                await get_link_btn.wait_for(state="visible", timeout=12000)

                player_page = None
                for _ in range(3):
                    try:
                        await get_link_btn.click(timeout=1500)
                    except Exception:
                        pass
                    await sub_page.wait_for_timeout(1000)
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
                    await player_page.locator("video, .jw-display-icon-container, svg").first.click(timeout=2000)
                except Exception:
                    pass

                for _ in range(8):
                    if current_stream_urls:
                        break
                    await asyncio.sleep(0.5)

                if current_stream_urls:
                    for raw_stream_link in current_stream_urls:
                        real_res_label = detect_resolution_from_stream_url(
                            raw_stream_link, 
                            f"{btn_info['parent_text']} {btn_info['button_text']}"
                        )
                        if "720P" not in real_res_label:
                            movie_captured_data[real_res_label] = raw_stream_link
                            log(f"   ✅ Captured [{real_res_label}]: {raw_stream_link[:50]}...")

            except Exception as err:
                log(f"   ⚠️ Gateway Timeout / Skip")
            finally:
                await sub_page.close()

            await asyncio.sleep(1.0)

    finally:
        await context.close()

    return movie_url, movie_title, category_name, movie_captured_data


# ==============================================================================
# 🎯 ৪. মেইন কন্ট্রোলার
# ==============================================================================
async def main():
    scraped_history = set()
    scan_mode = os.environ.get("SCAN_MODE", "ALL").strip()

    os.makedirs("history", exist_ok=True)
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            scraped_history = set(line.strip() for line in f if line.strip())

    target_categories = {}
    if scan_mode == "ALL":
        target_categories = CATEGORIES_MAP
    else:
        for key, val in CATEGORIES_MAP.items():
            if key.lower().replace(" ", "").replace("-", "") == scan_mode.lower().replace(" ", "").replace("-", ""):
                target_categories[key] = val
                break
        
        if not target_categories:
            log(f"⚠️ SCAN_MODE '{scan_mode}' matches no specific key. Scanning ALL categories.")
            target_categories = CATEGORIES_MAP

    sem = asyncio.Semaphore(2)

    async def safe_process(browser_inst, url, idx, cat_name):
        async with sem:
            return await process_single_movie(browser_inst, url, idx, cat_name)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])

        for cat_display_name, config in target_categories.items():
            cat_slug = config["slug"]
            file_path = config["file"]
            os.makedirs(os.path.dirname(file_path), exist_ok=True)

            log(f"\n==================================================")
            log(f"📂 Scanning Category: {cat_display_name} ({cat_slug})")
            log(f"==================================================")

            page_main = await browser.new_page()
            await page_main.route("**/*", lambda route: route.abort() if any(ad in route.request.url.lower() for ad in AD_DOMAINS) else route.continue_())

            new_movie_urls = []
            page_num = 1

            while len(new_movie_urls) < LIMIT_MOVIES_PER_CATEGORY_RUN:
                cat_url = f"{MAIN_SITE_URL.rstrip('/')}/{cat_slug}/page/{page_num}/" if page_num > 1 else f"{MAIN_SITE_URL.rstrip('/')}/{cat_slug}/"
                try:
                    await page_main.goto(cat_url, timeout=25000, wait_until="domcontentloaded")
                    
                    extracted_urls = await page_main.evaluate("""
                        () => {
                            let cards = Array.from(document.querySelectorAll('article, .post-card, .type-post, .entry-title, .post-header'));
                            let urls = [];
                            cards.forEach(card => {
                                let a = card.querySelector('a');
                                if (a && a.href) urls.push(a.href);
                            });
                            if (urls.length === 0) {
                                let anchors = Array.from(document.querySelectorAll('#main a, #content a, .posts-layout a, article a'));
                                anchors.forEach(a => { if (a.href) urls.push(a.href); });
                            }
                            return urls;
                        }
                    """)

                    for link in extracted_urls:
                        if link and link.startswith("http") and link.rstrip('/') != MAIN_SITE_URL.rstrip('/'):
                            if not any(junk in link for junk in ["/category/", "/page/", "/tag/", "/genre/", "/author/"]):
                                if link not in scraped_history and link not in new_movie_urls:
                                    new_movie_urls.append(link)
                                    if len(new_movie_urls) >= LIMIT_MOVIES_PER_CATEGORY_RUN:
                                        break
                    
                    if len(new_movie_urls) >= LIMIT_MOVIES_PER_CATEGORY_RUN:
                        break
                    
                    page_num += 1
                except Exception as page_err:
                    log(f"⚠️ Page scan error: {page_err}")
                    break

            await page_main.close()

            if not new_movie_urls:
                log(f"ℹ️ No new movies found for category: {cat_display_name}")
                continue

            log(f"🚀 Found {len(new_movie_urls)} new movies. Starting extraction...")

            tasks = [safe_process(browser, m_url, idx, cat_display_name) for idx, m_url in enumerate(new_movie_urls, 1)]
            results = await asyncio.gather(*tasks)

            with open(file_path, "a", encoding="utf-8") as out_f, open(HISTORY_FILE, "a", encoding="utf-8") as h_f:
                for idx, (m_url, title, cat_name, res_dict) in enumerate(results, 1):
                    if not res_dict:
                        continue

                    year_match = re.search(r'\b(20\d{2}|19\d{2})\b', title)
                    year = year_match.group(1) if year_match else "N/A"
                    clean_name = title.split(f"({year})")[0].split(year)[0].strip() if year_match else title
                    clean_name = re.sub(r'[\s\-\[\]\(\)]+$', '', clean_name).strip()

                    out_f.write(f"Movie-{idx}\n")
                    out_f.write(f"Movie name: {clean_name}\n")
                    out_f.write(f"Movie Category: {cat_name}\n")
                    out_f.write(f"Movie year: {year}\n\n")

                    res_count = 1
                    for res_name, stream_link in res_dict.items():
                        out_f.write(f"RESOLUTION {res_count}: {res_name}\n")
                        out_f.write(f"STREAM Link {res_count}: {stream_link}\n\n")
                        res_count += 1

                    out_f.write("=" * 80 + "\n\n")
                    h_f.write(f"{m_url}\n")
                    scraped_history.add(m_url)

        await browser.close()

    log("\n🎉 All Scheduled Category Scraping Completed Successfully!")

if __name__ == "__main__":
    asyncio.run(main())
