import asyncio
import base64
import json
import nest_asyncio
import urllib.parse
import urllib.request
import re
import random
import os
import sys
from datetime import datetime, timezone
from playwright.async_api import async_playwright

nest_asyncio.apply()

# ==============================================================================
# ⚙️ ১. কনফিগারেশন (GitHub/Colab Secrets থেকে লোড করা)
# ==============================================================================
MAIN_SITE_URL = os.environ.get("MAIN_SITE_URL", "").rstrip("/") + "/"
SCAN_MODE = os.environ.get("SCAN_MODE", "AUTO").strip()
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")

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
    "Telugu Movies",
    "Disney Hotstar"
]

CATEGORIES_MAP = {
    "Bangla Movies": {
        "slug": "bangla-movies",
        "dir": "categories/Bangla_Movies",
        "file": "categories/Bangla_Movies/bangla_movies.txt",
        "json": "categories/Bangla_Movies/bangla_movies.json",
        "m3u": "categories/Bangla_Movies/bangla_movies.m3u"
    },
    "English Movies": {
        "slug": "english-movies",
        "dir": "categories/English_Movies",
        "file": "categories/English_Movies/english_movies.txt",
        "json": "categories/English_Movies/english_movies.json",
        "m3u": "categories/English_Movies/english_movies.m3u"
    },
    "Hindi Movies": {
        "slug": "hindi-movies",
        "dir": "categories/Hindi_Movies",
        "file": "categories/Hindi_Movies/hindi_movies.txt",
        "json": "categories/Hindi_Movies/hindi_movies.json",
        "m3u": "categories/Hindi_Movies/hindi_movies.m3u"
    },
    "Hindi Dubbed Movies": {
        "slug": "hindi-dubbed-movies",
        "dir": "categories/Dubbed/Hindi_Dubbed",
        "file": "categories/Dubbed/Hindi_Dubbed/hindi_dubbed_movies.txt",
        "json": "categories/Dubbed/Hindi_Dubbed/hindi_dubbed_movies.json",
        "m3u": "categories/Dubbed/Hindi_Dubbed/hindi_dubbed_movies.m3u"
    },
    "Bangla Dubbed": {
        "slug": "bangla-dubbed-movies",
        "dir": "categories/Dubbed/Bangla_Dubbed",
        "file": "categories/Dubbed/Bangla_Dubbed/bangla_dubbed_movies.txt",
        "json": "categories/Dubbed/Bangla_Dubbed/bangla_dubbed_movies.json",
        "m3u": "categories/Dubbed/Bangla_Dubbed/bangla_dubbed_movies.m3u"
    },
    "Tamil Movies": {
        "slug": "tamil-movies",
        "dir": "categories/South_Indian/Tamil",
        "file": "categories/South_Indian/Tamil/tamil_movies.txt",
        "json": "categories/South_Indian/Tamil/tamil_movies.json",
        "m3u": "categories/South_Indian/Tamil/tamil_movies.m3u"
    },
    "Malayalam Movies": {
        "slug": "malayalam-movies",
        "dir": "categories/South_Indian/Malayalam",
        "file": "categories/South_Indian/Malayalam/malayalam_movies.txt",
        "json": "categories/South_Indian/Malayalam/malayalam_movies.json",
        "m3u": "categories/South_Indian/Malayalam/malayalam_movies.m3u"
    },
    "Kannada Movies": {
        "slug": "kannada-movies",
        "dir": "categories/South_Indian/Kannada",
        "file": "categories/South_Indian/Kannada/kannada_movies.txt",
        "json": "categories/South_Indian/Kannada/kannada_movies.json",
        "m3u": "categories/South_Indian/Kannada/kannada_movies.m3u"
    },
    "Telugu Movies": {
        "slug": "telugu-movies",
        "dir": "categories/South_Indian/Telugu",
        "file": "categories/South_Indian/Telugu/telugu_movies.txt",
        "json": "categories/South_Indian/Telugu/telugu_movies.json",
        "m3u": "categories/South_Indian/Telugu/telugu_movies.m3u"
    },
    "Disney Hotstar": {
        "slug": "ott/disney-hotstar",
        "dir": "categories/OTT/Disney_Hotstar",
        "file": "categories/OTT/Disney_Hotstar/disney_hotstar.txt",
        "json": "categories/OTT/Disney_Hotstar/disney_hotstar.json",
        "m3u": "categories/OTT/Disney_Hotstar/disney_hotstar.m3u"
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
    "bet365", "1xbet", "adsterra", "exoclick", "propellerads", "monetag", "clickadu", "taboola"
]

def sanitize_stream_url(url):
    if not url:
        return url
    return urllib.parse.quote(url, safe=':/?&=#%')

def parse_link_metadata(stream_url, default_res):
    unquoted = urllib.parse.unquote(stream_url)
    filename = unquoted.split('/')[-1]

    season = "N/A"
    episode = "N/A"

    se_match = re.search(r'\[?S(\d{1,2})\s*E(\d{1,4}(?:\s*-\s*\d{1,4})?)\]?', filename, re.IGNORECASE)
    if se_match:
        season = f"S{int(se_match.group(1)):02d}"
        ep_num = se_match.group(2).replace(" ", "")
        if "-" in ep_num:
            parts = ep_num.split("-")
            try:
                episode = f"Episode {int(parts[0]):02d}-{int(parts[1]):02d}"
            except Exception:
                episode = f"Episode {ep_num}"
        else:
            try:
                episode = f"Episode {int(ep_num):02d}"
            except Exception:
                episode = f"Episode {ep_num}"
    else:
        s_match = re.search(r'\bS(\d{1,2})\b', filename, re.IGNORECASE)
        if s_match:
            season = f"S{int(s_match.group(1)):02d}"

        ep_match = re.search(r'\b(?:EP|Episode|E)\s*[-•]?\s*(\d{1,4}(?:\s*-\s*\d{1,4})?)\b', filename, re.IGNORECASE)
        if ep_match:
            ep_num = ep_match.group(1).replace(" ", "")
            if "-" in ep_num:
                parts = ep_num.split("-")
                try:
                    episode = f"Episode {int(parts[0]):02d}-{int(parts[1]):02d}"
                except Exception:
                    episode = f"Episode {ep_num}"
            else:
                try:
                    episode = f"Episode {int(ep_num):02d}"
                except Exception:
                    episode = f"Episode {ep_num}"

    return {
        "season": season,
        "episode": episode,
        "resolution": default_res,
        "link": stream_url
    }

def clean_title_for_tmdb(name):
    clean = re.sub(r'\[.*?\]|\(.*?\)', '', name)
    clean = re.sub(r'\b(18\+|3D|2D|4K|1080p|720p|WEB-DL|BluRay|HDTC|ESub)\b', '', clean, flags=re.IGNORECASE)
    clean = re.sub(r'[:\-_+]+', ' ', clean)
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean

def fetch_tmdb_poster_sync(movie_name, year):
    if not TMDB_API_KEY:
        return "N/A"
    try:
        clean_query = clean_title_for_tmdb(movie_name)
        if not clean_query:
            clean_query = movie_name.strip()

        encoded_query = urllib.parse.quote(clean_query)
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

        results = []
        if year and str(year).isdigit() and year != "N/A":
            url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={encoded_query}&year={year}"
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode('utf-8'))
                        results = data.get("results", [])
            except Exception:
                pass

        if not results:
            url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={encoded_query}"
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode('utf-8'))
                        results = data.get("results", [])
            except Exception:
                pass

        if results:
            for item in results:
                poster_path = item.get("poster_path")
                if poster_path:
                    return f"https://image.tmdb.org/t/p/w600_and_h900_bestv2{poster_path}"
    except Exception:
        pass
    return "N/A"

async def fetch_tmdb_poster(movie_name, year):
    return await asyncio.to_thread(fetch_tmdb_poster_sync, movie_name, year)

# ==============================================================================
# 📑 ব্যাকআপ রিডার (আগের কোনো মুভি ডিলিট হবে না)
# ==============================================================================
def parse_existing_output_file(file_path):
    if not os.path.exists(file_path):
        return []

    movies = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        blocks = content.split("================================================================================")
        for block in blocks:
            block = block.strip()
            if not block or "CATEGORY:" in block or "NOTICE:" in block:
                continue

            name_m = re.search(r"(?:Movie name|Show name):\s*(.*)", block)
            cat_m = re.search(r"Category:\s*(.*)", block)
            year_m = re.search(r"Year:\s*(.*)", block)
            poster_m = re.search(r"Poster:\s*(.*)", block)

            if not name_m:
                continue

            name = name_m.group(1).strip()
            cat = cat_m.group(1).strip() if cat_m else "N/A"
            year_str = year_m.group(1).strip() if year_m else "N/A"
            poster = poster_m.group(1).strip() if poster_m else "N/A"

            res_list = []
            current_season = "N/A"
            current_episode = "N/A"
            current_res = "HD 1080P"

            lines = block.split('\n')
            for line in lines:
                line = line.strip()
                if line.startswith("Season:"):
                    current_season = line.split(":", 1)[1].strip()
                elif line.startswith("Episode:"):
                    current_episode = line.split(":", 1)[1].strip()
                elif line.startswith("RESOLUTION") or line.startswith("Resolution-"):
                    current_res = line.split(":", 1)[1].strip()
                elif line.startswith("STREAM Link") or line.startswith("Link-"):
                    link_val = line.split(":", 1)[1].strip()
                    res_list.append({
                        "season": current_season,
                        "episode": current_episode,
                        "resolution": current_res,
                        "link": link_val
                    })

            if res_list:
                movies.append({
                    "name": name,
                    "category": cat,
                    "year": year_str,
                    "poster": poster,
                    "res_list": res_list
                })
    except Exception as e:
        print(f"⚠️ Error reading existing file {file_path}: {e}", flush=True)
    return movies

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

    if any(junk in u_lower for junk in [
        "yagaverse.net", "google-analytics", "cinecloud.site", "neodrive.site",
        "ping.gif", "jwpltx", "collect?", "facebook", "twitter", "manifest"
    ]):
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
# 🎬 সমান্তরাল পাইপলাইন প্রসেসর (আসল পোস্টার ও বাটন এক্সট্র্যাক্টর)
# ==============================================================================
async def process_movie_parallel_pipeline(browser, movie_url, movie_idx, default_category_name):
    movie_captured_data = []
    movie_title = "Movie Post"
    movie_categories = default_category_name
    web_poster_url = "N/A"

    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={"width": 1280, "height": 720}
    )

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

    try:
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
        page = await context.new_page()

        async def main_page_ad_blocker(route):
            u_lower = route.request.url.lower()
            if any(ad in u_lower for ad in AD_AND_ANALYTICS_DOMAINS):
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", main_page_ad_blocker)

        print(f"🎬 [MOVIE {movie_idx}/{TARGET_LIMIT_MOVIES}] Opening Page: {movie_url}", flush=True)
        await page.goto(movie_url, timeout=40000, wait_until="domcontentloaded")

        raw_title = await page.title()
        movie_title = raw_title.split(" - ")[0].split(" Full Movie")[0].replace("Watch ", "").strip()

        try:
            web_poster_url = await page.evaluate(r"""
                () => {
                    let posterImg = document.querySelector('.poster-image img, .post-thumbnail img, img[alt*="Poster"], img[alt*="poster"]');
                    if (posterImg && posterImg.src && posterImg.src.startsWith('http') && !posterImg.src.includes('cineimg.xyz')) {
                        return posterImg.src;
                    }
                    let allImgs = Array.from(document.querySelectorAll('img'));
                    for (let img of allImgs) {
                        let src = img.src || img.getAttribute('data-src') || "";
                        if (src.includes('image.tmdb.org') && src.startsWith('http')) {
                            return src;
                        }
                    }
                    let ogImg = document.querySelector('meta[property="og:image"]');
                    if (ogImg && ogImg.content && ogImg.content.startsWith('http')) {
                        return ogImg.content;
                    }
                    return "N/A";
                }
            """)
        except Exception:
            pass

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

        target_buttons = await page.evaluate(r"""
            () => {
                let matches = [];
                let seenUrls = new Set();

                let headers = Array.from(document.querySelectorAll('h4.movie-title, h4, .movie-title'));
                
                headers.forEach(h4 => {
                    let headerText = (h4.innerText || '').toLowerCase();
                    
                    if (headerText.includes('1080p') || headerText.includes('2160p') || headerText.includes('4k')) {
                        
                        if (headerText.includes('720p') && !headerText.includes('1080p')) return;
                        if (headerText.includes('480p') && !headerText.includes('1080p')) return;

                        let container = h4.nextElementSibling;
                        while (container && !container.classList.contains('dlbtn-container') && container.tagName !== 'HR') {
                            container = container.nextElementSibling;
                        }

                        if (!container || !container.classList.contains('dlbtn-container')) {
                            if (h4.parentElement) {
                                container = h4.parentElement.querySelector('.dlbtn-container');
                            }
                        }

                        if (container) {
                            let watchBtn = container.querySelector('a.dlbtn-watch');
                            if (!watchBtn) {
                                let allBtns = Array.from(container.querySelectorAll('a.dlbtn, a[href*="generate.php"]'));
                                watchBtn = allBtns.find(a => (a.innerText || '').toLowerCase().includes('watch online'));
                            }

                            if (watchBtn) {
                                let href = watchBtn.href;

                                let isTrueWatch3Marker = false;
                                try {
                                    let urlObj = new URL(href);
                                    let idParam = urlObj.searchParams.get('id');
                                    if (idParam) {
                                        let decoded = atob(idParam);
                                        if (decoded.includes('/x/')) {
                                            isTrueWatch3Marker = true;
                                        }
                                    }
                                } catch(e) {}

                                if (isTrueWatch3Marker && !seenUrls.has(href)) {
                                    seenUrls.add(href);
                                    matches.push({
                                        button_text: watchBtn.innerText.trim(),
                                        parent_text: h4.innerText.trim(),
                                        url: href
                                    });
                                }
                            }
                        }
                    }
                });

                if (matches.length === 0) {
                    let allCards = Array.from(document.querySelectorAll('.ep-card'));
                    allCards.forEach(card => {
                        let watchBox = card.querySelector('.quality-box.watch-links, .watch-links');
                        if (!watchBox) return;

                        let anchors = Array.from(watchBox.querySelectorAll('a[href*="generate.php"]'));
                        anchors.forEach(a => {
                            if (seenUrls.has(a.href)) return;

                            let txt = (a.innerText || '').trim().toLowerCase();
                            if (txt.includes('1080p') || txt.includes('2160p') || txt.includes('4k') || txt.includes('hevc')) {
                                if ((txt.includes('720p') || txt.includes('480p')) && !txt.includes('1080p')) return;

                                let isTrueWatch3Marker = false;
                                try {
                                    let urlObj = new URL(a.href);
                                    let idParam = urlObj.searchParams.get('id');
                                    if (idParam && atob(idParam).includes('/x/')) isTrueWatch3Marker = true;
                                } catch(e) {}

                                if (isTrueWatch3Marker) {
                                    seenUrls.add(a.href);
                                    let epTitleEl = card.querySelector('.ep-title');
                                    let epMetaEl = card.querySelector('.ep-meta');
                                    let blockText = (epTitleEl ? epTitleEl.innerText : '') + ' ' + (epMetaEl ? epMetaEl.innerText : '');

                                    matches.push({
                                        button_text: a.innerText.trim(),
                                        parent_text: blockText.trim(),
                                        url: a.href
                                    });
                                }
                            }
                        });
                    });
                }

                return matches;
            }
        """)

        if not target_buttons:
            print(f"❌ [MOVIE {movie_idx}/{TARGET_LIMIT_MOVIES}] No 1080p+ resolution available.", flush=True)
            return movie_url, movie_title, movie_categories, [], web_poster_url

        print(f"✅ [MOVIE {movie_idx}/{TARGET_LIMIT_MOVIES}] Found {len(target_buttons)} 1080p+ option(s). Processing...", flush=True)

        for idx, btn_info in enumerate(target_buttons, 1):
            target_gateway_url = btn_info["url"]
            current_stream_urls.clear()

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
                            if "cinecloud" in p_tab.url.lower() or "neodrive" in p_tab.url.lower():
                                player_page = p_tab
                                break
                            else:
                                try:
                                    await p_tab.close()
                                except Exception:
                                    pass
                    if player_page:
                        break

                if not player_page:
                    player_page = sub_page

                await player_page.bring_to_front()
                await player_page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(2)

                for _attempt in range(4):
                    try:
                        await player_page.mouse.click(640, 360)
                        for frame in player_page.frames:
                            try:
                                await frame.click("video, .jw-display-icon-container, svg, .play-button, body", timeout=1500)
                            except Exception:
                                pass
                        await player_page.locator("video, .jw-display-icon-container, svg, iframe").first.click(timeout=1500)
                    except Exception:
                        pass
                    await asyncio.sleep(1)

                for _ in range(24):
                    if current_stream_urls:
                        break
                    await asyncio.sleep(0.5)

                if current_stream_urls:
                    for stream_url in current_stream_urls:
                        sanitized_url = sanitize_stream_url(stream_url)
                        if not any(item['link'] == sanitized_url for item in movie_captured_data):
                            exact_res_label = detect_resolution_from_stream_url(sanitized_url)
                            movie_captured_data.append({
                                "resolution": exact_res_label,
                                "link": sanitized_url
                            })
                            print(f"   ✅ Captured [{exact_res_label}]: {sanitized_url[:65]}...", flush=True)

            except Exception:
                pass
            finally:
                await sub_page.close()

    finally:
        await context.close()

    return movie_url, movie_title, movie_categories, movie_captured_data, web_poster_url

# ==============================================================================
# 🎯 মেইন কন্ট্রোলার (হিস্টরি চেক -> স্ক্যান -> পোস্টার রিপেয়ার -> সোর্ট -> সেভ)
# ==============================================================================
async def main():
    state = load_tracker_state()
    cat_index = state.get("current_category_index", 0)
    run_count = state.get("run_count", 1)

    target_category_name = None
    is_auto_mode = False

    if SCAN_MODE in ["AUTO", "ALL"]:
        is_auto_mode = True
        if cat_index >= len(CATEGORIES_LIST):
            cat_index = 0
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
    json_filename = config["json"]
    m3u_filename = config["m3u"]
    history_filename = os.path.join(cat_dir, "history.txt")

    os.makedirs(cat_dir, exist_ok=True)

    # 📝 ১. আগের মুভি ফাইল এবং হিস্টরি নিখুঁতভাবে লোড করা
    existing_movies = parse_existing_output_file(output_filename)
    existing_names = {m["name"].lower() for m in existing_movies}
    print(f"📂 Loaded {len(existing_movies)} existing movie(s) from previous scans.", flush=True)

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
            MAX_PAGE_SAFETY_LIMIT = 5

            # 🎯 ২. হিস্টরিতে না থাকা একদম নতুন ১০টি মুভি খুঁজে বের করা
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
                    if ("-download" in link_clean or "full-movie" in link_clean or "web-dl" in link_clean or "full-series" in link_clean):
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

            # 🎯 ৩. নতুন মুভিগুলো পাওয়ার পর সেগুলোকে স্ক্র্যাপ করা
            new_movies_list = []
            if new_movie_urls:
                print(f"🚀 Found {len(new_movie_urls)} new unique movie(s). Starting extraction...\n", flush=True)
                tasks = [safe_process(browser, movie_url, idx, target_category_name) for idx, movie_url in enumerate(new_movie_urls, 1)]
                parallel_results = await asyncio.gather(*tasks)

                with open(history_filename, "a", encoding="utf-8") as h_file:
                    for movie_url, title, categories, res_list, web_poster in parallel_results:
                        if res_list:
                            h_file.write(f"{movie_url}\n")

                for movie_url, title, categories, res_list, web_poster in parallel_results:
                    if res_list:
                        year_match = re.search(r'\b(20\d{2}|19\d{2})\b', title)
                        year = year_match.group(1) if year_match else "N/A"
                        clean_name = title.split(f"({year})")[0].split(year)[0].strip() if year_match else title
                        clean_name = re.sub(r'[\s\-\[\]\(\)]+$', '', clean_name).strip()

                        if (year == "N/A" or not clean_name):
                            first_link = urllib.parse.unquote(res_list[0]['link'])
                            fn = first_link.split('/')[-1]
                            fn_year = re.search(r'\b(20\d{2}|19\d{2})\b', fn)
                            if fn_year and year == "N/A":
                                year = fn_year.group(1)
                            if not clean_name or len(clean_name) < 3:
                                fn_clean = fn.split('(')[0].replace('CINEFREAK.TOP -', '').strip()
                                if fn_clean:
                                    clean_name = fn_clean

                        if clean_name.lower() not in existing_names:
                            poster_url = web_poster if web_poster != "N/A" else "N/A"
                            if poster_url == "N/A":
                                poster_url = await fetch_tmdb_poster(clean_name, year)

                            parsed_res_list = []
                            for item in res_list:
                                meta = parse_link_metadata(item['link'], item['resolution'])
                                parsed_res_list.append(meta)

                            new_movies_list.append({
                                "name": clean_name,
                                "category": categories,
                                "year": year,
                                "poster": poster_url,
                                "res_list": parsed_res_list
                            })
                            existing_names.add(clean_name.lower())
            else:
                print("ℹ️ No new movies found to scrape on category pages.", flush=True)

            # 🎯 ৪. পুরানো + নতুন সব মুভি একত্রিত করা (নো-ডিলিট লজিক)
            all_movies = existing_movies + new_movies_list

            # 🎯 ৫. [POSTER AUTO-REPAIR] ফাঁকা বা মিসিং পোস্টারগুলোর জন্য TMDB ব্যাকফিল
            for m in all_movies:
                if not m.get("poster") or m["poster"] in ["N/A", "", "None"] or "cineimg.xyz" in m["poster"]:
                    repaired_poster = await fetch_tmdb_poster(m["name"], m["year"])
                    if repaired_poster != "N/A":
                        m["poster"] = repaired_poster

            # 🎯 ৬. সাল অনুযায়ী বড় থেকে ছোট (Descending - 2026, 2025, 2024...) সাজানো
            def get_sort_year(m):
                y = m.get("year", "0")
                return int(y) if str(y).isdigit() else 0

            all_movies.sort(key=get_sort_year, reverse=True)

            current_utc_time = datetime.now(timezone.utc).strftime("%Y-%m-%d | %H:%M:%S (UTC)")
            total_items_count = len(all_movies)

            # 🎯 ৭. TXT ফাইল জেনারেট ও ওভাররাইট করা (১ থেকে N পারফেক্ট সিরিয়াল)
            with open(output_filename, "w", encoding="utf-8") as f:
                f.write("=" * 80 + "\n")
                f.write(f"CATEGORY: {target_category_name}\n")
                f.write(f"TOTAL MOVIES: {total_items_count}\n")
                f.write(f"LAST UPDATED: {current_utc_time}\n")
                f.write("NOTICE: This repository and data are created strictly for EDUCATIONAL PURPOSES only and not for any commercial use.\n")
                f.write("=" * 80 + "\n\n")

                for idx, movie in enumerate(all_movies, 1):
                    is_series = any(item.get('season') != "N/A" or item.get('episode') != "N/A" for item in movie['res_list']) if isinstance(movie['res_list'], list) and len(movie['res_list']) > 0 and isinstance(movie['res_list'][0], dict) else False
                    title_type = "Show name" if is_series else "Movie name"

                    f.write(f"Movie-{idx}\n")
                    f.write(f"{title_type}: {movie['name']}\n")
                    f.write(f"Category: {movie['category']}\n")
                    f.write(f"Year: {movie['year']}\n")
                    f.write(f"Poster: {movie['poster']}\n\n")

                    if is_series and movie['res_list']:
                        season_str = "S01"
                        for item in movie['res_list']:
                            if item.get('season') != "N/A":
                                season_str = item['season']
                                break
                        f.write(f"Season: {season_str}\n\n")

                        episodes_group = {}
                        for item in movie['res_list']:
                            e_val = item.get('episode', 'N/A')
                            if e_val not in episodes_group:
                                episodes_group[e_val] = []
                            episodes_group[e_val].append(item)

                        for ep_name, links in episodes_group.items():
                            f.write(f"Episode: {ep_name}\n")
                            for r_idx, l_item in enumerate(links, 1):
                                f.write(f"  Resolution-{r_idx}: {l_item.get('resolution', 'HD 1080P')}\n")
                                f.write(f"  Link-{r_idx}: {l_item['link']}\n")
                            f.write("\n")
                    elif movie['res_list']:
                        for r_idx, item in enumerate(movie['res_list'], 1):
                            res_title = item.get('resolution', 'HD 1080P')
                            f.write(f"RESOLUTION {r_idx}: {res_title}\n")
                            f.write(f"STREAM Link {r_idx}: {item['link']}\n\n")

                    f.write("=" * 80 + "\n\n")

            # 🎯 ৮. JSON ফাইল জেনারেট ও ওভাররাইট করা
            json_payload = {
                "category_info": {
                    "category_name": target_category_name,
                    "total_movies": total_items_count,
                    "last_updated": current_utc_time,
                    "purpose": "Strictly for educational purposes and not for commercial use."
                },
                "movies": all_movies
            }
            with open(json_filename, "w", encoding="utf-8") as jf:
                json.dump(json_payload, jf, indent=2, ensure_ascii=False)

            # 🎯 ৯. M3U ফাইল জেনারেট ও ওভাররাইট করা
            with open(m3u_filename, "w", encoding="utf-8") as m3u:
                m3u.write("#EXTM3U\n")
                m3u.write(f"#EXT-X-NAME: {target_category_name}\n")
                m3u.write(f"#EXT-X-TOTAL-ITEMS: {total_items_count}\n")
                m3u.write(f"#EXT-X-UPDATED: {current_utc_time}\n")
                m3u.write("#EXT-X-NOTICE: Strictly for EDUCATIONAL PURPOSES only, not for commercial use.\n\n")

                for movie in all_movies:
                    m_name = movie['name']
                    m_poster = movie.get('poster', 'N/A')
                    m_year = movie.get('year', 'N/A')
                    
                    for item in movie.get('res_list', []):
                        res_label = item.get('resolution', 'HD 1080P')
                        link_val = item.get('link')
                        season_val = item.get('season', 'N/A')
                        ep_val = item.get('episode', 'N/A')
                        
                        if link_val:
                            if season_val != "N/A" and ep_val != "N/A":
                                title_str = f"{m_name} - {season_val}{ep_val} - {res_label}"
                                group_str = f"{target_category_name} - {m_name}"
                            else:
                                title_str = f"{m_name} ({m_year}) - {res_label}"
                                group_str = target_category_name
                                
                            m3u.write(f'#EXTINF:-1 tvg-logo="{m_poster}" group-title="{group_str}", {title_str}\n')
                            m3u.write(f"{link_val}\n")

            print(f"✅ Successfully updated TXT, JSON, and M3U files for [{target_category_name}] (Total: {total_items_count} movies).", flush=True)

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
