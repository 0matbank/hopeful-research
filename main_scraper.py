import asyncio
import base64
import nest_asyncio
import urllib.parse
import re
import random
import os
from playwright.async_api import async_playwright

nest_asyncio.apply()

# ==============================================================================
# ⚙️ ১. সিক্রেট এবং ক্যাটাগরি কনফিগারেশন
# ==============================================================================
MAIN_SITE_URL = os.environ.get("MAIN_SITE_URL", "https://cinefreak.net/")
HISTORY_FILE = "history/scraped_history.txt"
LIMIT_MOVIES_PER_CATEGORY_RUN = 10  # প্রতি রান এ প্রতি ক্যাটাগরি থেকে সর্বোচ্চ ১০টি মুভি

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

# ==============================================================================
# 🎯 ২. স্ট্রিম URL ফাইলনাম থেকে ১০০% সঠিক রেজুলেশন এক্সট্র্যাক্ট ফাংশন
# ==============================================================================
def detect_resolution_from_stream_url(stream_url, fallback_text=""):
    """
    ফালতু বাটন টেক্সটের ওপর নির্ভর না করে সরাসরি ফাইলনাম (.mkv/.mp4) থেকে
    প্রকৃত রেজুলেশন বের করার ফাংশন।
    """
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
    
    # Fallback to web text parsing if filename is generic
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
# 🎬 ৩. মুভি সমান্তরাল পাইপলাইন প্রসেসর
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

        print(f"🎬 [{category_name} | MOVIE {movie_idx}] Opening: {movie_url}")
        await page.goto(movie_url, timeout=35000, wait_until="domcontentloaded")

        raw_title = await page.title()
        movie_title = raw_title.split(" - ")[0].split(" Full Movie")[0].replace("Watch ", "").strip()

        # সিরিজ বা মুভি ড্রপডাউন এক্সপ্যান্ড করা
        watch_online_locators = page.locator("a:has-text('Watch Online')")
        for i in range(await watch_online_locators.count()):
            try:
                href = await watch_online_locators.nth(i).get_attribute("href")
                if not href or href == "#" or "javascript" in href.lower():
                    await watch_online_locators.nth(i).click(timeout=1500)
            except Exception:
                pass

        # ১০৮০p+ ও ৪K টার্গেট বাটন ফিল্টারিং
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
            return movie_url, movie_title, category_name, {}

        # ডাইরেক্ট লিঙ্ক ক্যাপচারিং
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
                await sub_page.goto(target_gateway_url, timeout=30000, wait_until="domcontentloaded")
                verify_btn = sub_page.locator("#btn-text")
                await verify_btn.wait_for(state="visible", timeout=12000)
                await verify_btn.click()
                await sub_page.wait_for_timeout(1500)

                get_link_btn = sub_page.locator("#btn-text")
                await get_link_btn.wait_for(state="visible", timeout=12000)

                player_page = None
                for _ in range(3):
                    try:
                        await get_link_btn.click(timeout=2000)
                    except Exception:
                        pass
                    await sub_page.wait_for_timeout(1200)
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
                    await player_page.locator("video, .jw-display-icon-container, svg").first.click(timeout=2500)
                except Exception:
                    pass

                # লিঙ্ক পাওয়ার সাথে সাথেই লুপ সম্পন্ন হবে
                for _ in range(10):
                    if current_stream_urls:
                        break
                    await asyncio.sleep(0.5)

                if current_stream_urls:
                    for raw_stream_link in current_stream_urls:
                        # 🎯 ফাইলনাম থেকে রেজুলেশন নির্ণয়
                        real_res_label = detect_resolution_from_stream_url(
                            raw_stream_link, 
                            f"{btn_info['parent_text']} {btn_info['button_text']}"
                        )
                        
                        # ৭২০p লিঙ্ক হলে বাদ দেওয়া হবে
                        if "720P" not in real_res_label:
                            movie_captured_data[real_res_label] = raw_stream_link

            except Exception:
                pass
            finally:
                await sub_page.close()

            # Anti-bot delay
            await asyncio.sleep(random.uniform(1.2, 2.5))

    finally:
        await context.close()

    return movie_url, movie_title, category_name, movie_captured_data


# ==============================================================================
# 🎯 ৪. মেইন কন্ট্রোলার (সবগুলো ক্যাটাগরি প্রসেস করবে)
# ==============================================================================
async def main():
    scraped_history = set()

    # হিস্ট্রি ফাইল তৈরি ও পড়া
    os.makedirs("history", exist_ok=True)
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            scraped_history = set(line.strip() for line in f if line.strip())

    sem = asyncio.Semaphore(2)  # সেমাফোর ২ রাখা হয়েছে অ্যান্টি-ব্যান নিরাপত্তার জন্য

    async def safe_process(browser_inst, url, idx, cat_name):
        async with sem:
            return await process_single_movie(browser_inst, url, idx, cat_name)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])

        for cat_display_name, config in CATEGORIES_MAP.items():
            cat_slug = config["slug"]
            file_path = config["file"]
            os.makedirs(os.path.dirname(file_path), exist_ok=True)

            print(f"\n==================================================")
            print(f"📂 Scanning Category: {cat_display_name} ({cat_slug})")
            print(f"==================================================")

            page_main = await browser.new_page()
            
            # এড-ব্লকিং
            await page_main.route("**/*", lambda route: route.abort() if any(ad in route.request.url.lower() for ad in AD_DOMAINS) else route.continue_())

            new_movie_urls = []
            page_num = 1

            # স্মার্ট পেজিনেশন (১০টি ইউনিক নতুন মুভি সংগ্রহ)
            while len(new_movie_urls) < LIMIT_MOVIES_PER_CATEGORY_RUN:
                cat_url = f"{MAIN_SITE_URL.rstrip('/')}/{cat_slug}/page/{page_num}/" if page_num > 1 else f"{MAIN_SITE_URL.rstrip('/')}/{cat_slug}/"
                try:
                    await page_main.goto(cat_url, timeout=30000, wait_until="domcontentloaded")
                    links = await page_main.eval_on_selector_all("a", "elements => elements.map(e => e.href)")
                    
                    for link in links:
                        if link and ("full-movie-download" in link or link.endswith("-download/")):
                            if not any(c in link for c in ["-movies/", "/category/", "/genre/"]):
                                if link not in scraped_history and link not in new_movie_urls:
                                    new_movie_urls.append(link)
                                    if len(new_movie_urls) >= LIMIT_MOVIES_PER_CATEGORY_RUN:
                                        break
                    
                    if len(new_movie_urls) >= LIMIT_MOVIES_PER_CATEGORY_RUN:
                        break
                    
                    page_num += 1
                except Exception:
                    break

            await page_main.close()

            if not new_movie_urls:
                print(f"ℹ️ No new movies found for category: {cat_display_name}")
                continue

            print(f"🚀 Found {len(new_movie_urls)} new movies. Starting extraction...")

            tasks = [safe_process(browser, m_url, idx, cat_display_name) for idx, m_url in enumerate(new_movie_urls, 1)]
            results = await asyncio.gather(*tasks)

            # ক্যাটাগরি ফাইল এ রাইট করা
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

                    # হিস্ট্রিতে সেভ
                    h_f.write(f"{m_url}\n")
                    scraped_history.add(m_url)

        await browser.close()

    print("\n🎉 All Scheduled Category Scraping Completed Successfully!")

if __name__ == "__main__":
    asyncio.run(main())
