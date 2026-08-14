import asyncio
import base64
import json
import nest_asyncio
import urllib.parse
import urllib.request
import urllib.error
import re
import random
import os
import sys
import time
from datetime import datetime, timezone
from playwright.async_api import async_playwright

nest_asyncio.apply()

# ==============================================================================
# ⚙️ ১. কনফিগারেশন (GitHub/Colab Secrets থেকে লোড করা)
# ==============================================================================
MAIN_SITE_URL = os.environ.get("MAIN_SITE_URL", "").rstrip("/") + "/"
SCAN_MODE = os.environ.get("SCAN_MODE", "AUTO").strip()
REPAIR_CATEGORY = os.environ.get("REPAIR_CATEGORY", "Bangla Movies").strip()
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
OMDB_API_KEY = os.environ.get("OMDB_API_KEY", "e0d2217b")

STATE_FILE = "history/tracker_state.json"
FAILED_REPAIRS_FILE = "history/failed_repairs.json"
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
        "slug": "bangla-dubbed",
        "dir": "categories/Dubbed/Bangla_Dubbed",
        "file": "categories/Dubbed/Bangla_Dubbed/bangla_dubbed_movies.txt",
        "json": "categories/Dubbed/Bangla_Dubbed/bangla_dubbed_movies.json",
        "m3u": "categories/Dubbed/Bangla_Dubbed/bangla_dubbed_movies.m3u"
    },
    "Tamil Movies": {
        "slug": "tamil",
        "dir": "categories/South_Indian/Tamil",
        "file": "categories/South_Indian/Tamil/tamil_movies.txt",
        "json": "categories/South_Indian/Tamil/tamil_movies.json",
        "m3u": "categories/South_Indian/Tamil/tamil_movies.m3u"
    },
    "Malayalam Movies": {
        "slug": "malayalam",
        "dir": "categories/South_Indian/Malayalam",
        "file": "categories/South_Indian/Malayalam/malayalam_movies.txt",
        "json": "categories/South_Indian/Malayalam/malayalam_movies.json",
        "m3u": "categories/South_Indian/Malayalam/malayalam_movies.m3u"
    },
    "Kannada Movies": {
        "slug": "kannada",
        "dir": "categories/South_Indian/Kannada",
        "file": "categories/South_Indian/Kannada/kannada_movies.txt",
        "json": "categories/South_Indian/Kannada/kannada_movies.json",
        "m3u": "categories/South_Indian/Kannada/kannada_movies.m3u"
    },
    "Telugu Movies": {
        "slug": "telugu",
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

# ==============================================================================
# ⚡ রিয়েল ওয়াচ লিঙ্ক প্রটেক্টেড হেলথ চেক (Improved — CDN ও validate করে)
# ==============================================================================
def is_stream_link_dead_sync(stream_url):
    """Stream URL alive কিনা চেক করে। True = Dead, False = Alive."""
    if not stream_url or stream_url in ["N/A", ""]:
        return True

    clean_url = stream_url.strip()

    # CDN URL-এর জন্য বেশি timeout দিয়ে actual check করা (আগে blind skip ছিল)
    is_cdn = any(cdn in clean_url for cdn in ["r2.dev", "cloudflarestorage.com", "r2.cloudflarestorage", "pub-"])
    check_timeout = 5 if is_cdn else 3

    # ১. HEAD request দিয়ে চেক
    try:
        req = urllib.request.Request(
            clean_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            method="HEAD"
        )
        with urllib.request.urlopen(req, timeout=check_timeout) as resp:
            if resp.status in [200, 206, 403]:
                return False
    except urllib.error.HTTPError as e:
        # 404/410 মানে confirmed dead
        if e.code in [404, 410]:
            return True
        # 403 CDN-এ মানে file exist করে কিন্তু direct access block
        if e.code == 403 and is_cdn:
            return False
    except Exception:
        pass

    # ২. HEAD fail করলে GET fallback (কিছু server HEAD support করে না)
    try:
        req = urllib.request.Request(
            clean_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Range": "bytes=0-1023"
            }
        )
        with urllib.request.urlopen(req, timeout=check_timeout) as resp:
            if resp.status in [200, 206, 403]:
                return False
    except urllib.error.HTTPError as e:
        if e.code in [404, 410]:
            return True
        if e.code == 403 and is_cdn:
            return False
    except Exception:
        pass

    return True

def is_page_url_alive_sync(page_url):
    """Movie page URL (website page) alive কিনা চেক করে।"""
    if not page_url or page_url in ["N/A", ""]:
        return False
    try:
        req = urllib.request.Request(
            page_url.strip(),
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            method="HEAD"
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            if resp.status in [200, 301, 302]:
                return True
    except urllib.error.HTTPError as e:
        if e.code in [404, 410, 403]:
            return False
    except Exception:
        pass
    return False

# ==============================================================================
# 🛠️ Failed Repairs Tracker (একই dead link বারবার retry ঠেকানো)
# ==============================================================================
def load_failed_repairs():
    """failed_repairs.json থেকে আগের failed repair records লোড করে।"""
    os.makedirs("history", exist_ok=True)
    if os.path.exists(FAILED_REPAIRS_FILE):
        try:
            with open(FAILED_REPAIRS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_failed_repairs(data):
    """Failed repair records সেভ করে।"""
    os.makedirs("history", exist_ok=True)
    with open(FAILED_REPAIRS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def should_skip_repair(movie_name, failed_repairs):
    """24 ঘণ্টার মধ্যে আবার retry করবে না।"""
    key = movie_name.lower().strip()
    if key in failed_repairs:
        entry = failed_repairs[key]
        # পুরনো বা corrupt entry (float/int/string) হলে skip করো
        if not isinstance(entry, dict):
            return False
        last_attempt = entry.get("last_attempt", "")
        if last_attempt:
            try:
                last_time = datetime.fromisoformat(last_attempt)
                now = datetime.now(timezone.utc)
                hours_diff = (now - last_time).total_seconds() / 3600
                if hours_diff < 24:
                    return True
            except Exception:
                pass
    return False

def record_failed_repair(movie_name, category, dead_links, failed_repairs):
    """Failed repair log করে।"""
    key = movie_name.lower().strip()
    # পুরনো entry dict না হলে (corrupt/float), attempt_count 0 থেকে শুরু
    old_entry = failed_repairs.get(key, {})
    old_count = old_entry.get("attempt_count", 0) if isinstance(old_entry, dict) else 0
    failed_repairs[key] = {
        "name": movie_name,
        "category": category,
        "dead_links": dead_links,
        "last_attempt": datetime.now(timezone.utc).isoformat(),
        "attempt_count": old_count + 1
    }

# ==============================================================================
# 🎬 পোস্টার ফেচিং সিস্টেম (TMDB -> OMDb/IMDb -> Cinemeta Multi-Fallback)
# ==============================================================================
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

def fetch_omdb_poster_sync(movie_name, year):
    if not OMDB_API_KEY:
        return "N/A"
    try:
        clean_query = clean_title_for_tmdb(movie_name)
        if not clean_query:
            clean_query = movie_name.strip()

        encoded_query = urllib.parse.quote(clean_query)
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

        if year and str(year).isdigit() and year != "N/A":
            url = f"http://www.omdbapi.com/?t={encoded_query}&y={year}&apikey={OMDB_API_KEY}"
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode('utf-8'))
                        poster = data.get("Poster")
                        if poster and poster != "N/A" and poster.startswith("http"):
                            return poster
            except Exception:
                pass

        url = f"http://www.omdbapi.com/?t={encoded_query}&apikey={OMDB_API_KEY}"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode('utf-8'))
                    poster = data.get("Poster")
                    if poster and poster != "N/A" and poster.startswith("http"):
                        return poster
        except Exception:
            pass

    except Exception:
        pass
    return "N/A"

def fetch_cinemeta_poster_sync(movie_name, year):
    try:
        clean_query = clean_title_for_tmdb(movie_name)
        if not clean_query:
            clean_query = movie_name.strip()

        search_str = f"{clean_query} {year}".strip() if year and year != "N/A" else clean_query
        encoded_query = urllib.parse.quote(search_str)
        url = f"https://v3-cinemeta.strem.io/catalog/movie/top/search={encoded_query}.json"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode('utf-8'))
                metas = data.get("metas", [])
                if metas and isinstance(metas, list):
                    poster = metas[0].get("poster")
                    if poster and poster.startswith("http"):
                        return poster
    except Exception:
        pass
    return "N/A"

def fetch_poster_sync_combined(movie_name, year):
    poster = fetch_tmdb_poster_sync(movie_name, year)
    if poster != "N/A":
        return poster

    poster = fetch_omdb_poster_sync(movie_name, year)
    if poster != "N/A":
        return poster

    poster = fetch_cinemeta_poster_sync(movie_name, year)
    if poster != "N/A":
        return poster

    return "N/A"

async def fetch_tmdb_poster(movie_name, year):
    return await asyncio.to_thread(fetch_poster_sync_combined, movie_name, year)

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
# 🎬 সমান্তরাল পাইপলাইন প্রসেসর (নির্ভুল কার্ড-বাই-কার্ড বাটন এক্সট্র্যাক্টর)
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

        # 🎯 বাটন এক্সট্র্যাক্টর (Download বাটন সম্পূর্ণ বাদ দিয়ে শুধুমাত্র Watch বাটন ফিল্টারিং)
        target_buttons = await page.evaluate(r"""
            () => {
                let matches = [];
                let seenUrls = new Set();

                // ১. যদি পেজে .ep-card (সিরিজ পেজ) থাকে, তবে কার্ড বাই কার্ড আলাদা স্ক্যান
                let epCards = Array.from(document.querySelectorAll('.ep-card'));
                if (epCards.length > 0) {
                    epCards.forEach(card => {
                        let epTitle = card.querySelector('.ep-title, .ep-meta') ? card.querySelector('.ep-title, .ep-meta').innerText : 'Episode';
                        let watchBtns = Array.from(card.querySelectorAll('.watch-links a, .quality-box a, a.dlbtn-watch, a[href*="generate.php"]'));

                        watchBtns.forEach(a => {
                            if (seenUrls.has(a.href)) return;

                            let txt = (a.innerText || '').toLowerCase().trim();
                            let parentBox = a.closest('.download-links, .watch-links, .quality-box, .dlbtn-container');
                            let parentClass = parentBox ? parentBox.className.toLowerCase() : '';

                            // ❌ Download বাটন সম্পূর্ণ বাদ দেওয়া
                            if (txt.includes('download') && !txt.includes('watch')) return;
                            if (parentClass.includes('download-links')) return;

                            // ৭২০পি বা ৪৮০পি বাটন এড়িয়ে চলা
                            if ((txt.includes('720p') || txt.includes('480p')) && !txt.includes('1080p')) return;

                            let isTrueWatch3Marker = false;
                            try {
                                let urlObj = new URL(a.href);
                                let idParam = urlObj.searchParams.get('id');
                                if (idParam && atob(idParam).includes('/x/')) isTrueWatch3Marker = true;
                            } catch(e) {}

                            if (isTrueWatch3Marker || a.href.includes('generate.php')) {
                                seenUrls.add(a.href);
                                matches.push({
                                    button_text: a.innerText.trim(),
                                    parent_text: epTitle.trim(),
                                    url: a.href
                                });
                            }
                        });
                    });
                } else {
                    // ২. সাধারণ সিঙ্গেল মুভি পেজ স্ক্যান
                    let headers = Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, .movie-title'));
                    headers.forEach(h => {
                        let hText = (h.innerText || '').toLowerCase();

                        if (hText.includes('1080p') || hText.includes('2160p') || hText.includes('4k') || hText.includes('hevc')) {
                            if ((hText.includes('720p') || hText.includes('480p')) && !hText.includes('1080p')) return;

                            let container = h.nextElementSibling;
                            let searchLimit = 0;
                            while (container && !container.querySelector('a[href*="generate.php"], a.dlbtn') && searchLimit < 4) {
                                container = container.nextElementSibling;
                                searchLimit++;
                            }

                            if (!container || !container.querySelector('a')) {
                                if (h.parentElement) container = h.parentElement;
                            }

                            if (container) {
                                let watchBtn = Array.from(container.querySelectorAll('a.dlbtn-watch, a.dlbtn, a[href*="generate.php"]')).find(a => {
                                    let txt = (a.innerText || '').toLowerCase().trim();
                                    return !txt.includes('download') || txt.includes('watch');
                                });

                                if (watchBtn && !seenUrls.has(watchBtn.href)) {
                                    let isTrueWatch3Marker = false;
                                    try {
                                        let urlObj = new URL(watchBtn.href);
                                        let idParam = urlObj.searchParams.get('id');
                                        if (idParam && atob(idParam).includes('/x/')) isTrueWatch3Marker = true;
                                    } catch(e) {}

                                    if (isTrueWatch3Marker || watchBtn.href.includes('generate.php')) {
                                        seenUrls.add(watchBtn.href);
                                        matches.push({
                                            button_text: watchBtn.innerText.trim(),
                                            parent_text: h.innerText.trim(),
                                            url: watchBtn.href
                                        });
                                    }
                                }
                            }
                        }
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
# 🔍 Main Site Search — Movie Name দিয়ে নতুন Page URL খোঁজা
# ==============================================================================
async def search_movie_on_site(browser, movie_name, cat_slug):
    """Main site-এ movie name দিয়ে search করে matching page URL বের করে।"""
    found_url = None
    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={"width": 1280, "height": 720}
    )
    try:
        page = await context.new_page()

        async def ad_blocker(route):
            u_lower = route.request.url.lower()
            if any(ad in u_lower for ad in AD_AND_ANALYTICS_DOMAINS) or u_lower.endswith((".png", ".jpg", ".jpeg", ".woff2")):
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", ad_blocker)

        # WordPress standard search (?s=query)
        clean_query = clean_title_for_tmdb(movie_name)
        search_url = f"{MAIN_SITE_URL}?s={urllib.parse.quote(clean_query)}"
        print(f"   🔍 Searching on site: {search_url}", flush=True)

        try:
            await page.goto(search_url, timeout=25000, wait_until="domcontentloaded")

            search_results = await page.evaluate("""
                () => {
                    let links = Array.from(document.querySelectorAll('article a, .post-card a, .type-post a, .entry-title a, h2 a, h3 a'));
                    return links.map(a => ({href: a.href, text: (a.innerText || '').trim()}))
                                .filter(l => l.href && l.href.startsWith('http') && (l.href.includes('-download') || l.href.includes('full-movie') || l.href.includes('web-dl')));
                }
            """)

            if search_results:
                query_lower = clean_query.lower()
                for result in search_results:
                    result_text_lower = result.get("text", "").lower()
                    query_words = set(query_lower.split())
                    result_words = set(result_text_lower.split())
                    overlap = len(query_words & result_words)
                    if overlap >= max(1, len(query_words) // 2):
                        found_url = result["href"]
                        print(f"   ✅ Search match found: {found_url}", flush=True)
                        break

                if not found_url and search_results:
                    found_url = search_results[0]["href"]
                    print(f"   ⚠️ No exact match, using first result: {found_url}", flush=True)
        except Exception as e:
            print(f"   ⚠️ Search failed: {e}", flush=True)

        # Search fail হলে category page scan
        if not found_url and cat_slug:
            try:
                cat_url = f"{MAIN_SITE_URL}{cat_slug}/"
                print(f"   🔍 Fallback: Scanning category page: {cat_url}", flush=True)
                await page.goto(cat_url, timeout=25000, wait_until="domcontentloaded")

                cat_links = await page.evaluate("""
                    () => {
                        let links = Array.from(document.querySelectorAll('article a, .post-card a, .type-post a, .entry-title a, h2 a, h3 a'));
                        return links.map(a => ({href: a.href, text: (a.innerText || '').trim()}))
                                    .filter(l => l.href && l.href.startsWith('http') && (l.href.includes('-download') || l.href.includes('full-movie') || l.href.includes('web-dl')));
                    }
                """)

                query_lower = clean_title_for_tmdb(movie_name).lower()
                for link_info in cat_links:
                    link_text_lower = link_info.get("text", "").lower()
                    link_slug = link_info["href"].lower().split("/")[-1] if "/" in link_info["href"] else ""
                    query_words = set(query_lower.split())
                    text_words = set(link_text_lower.split())
                    slug_words = set(link_slug.replace("-", " ").split())

                    text_overlap = len(query_words & text_words)
                    slug_overlap = len(query_words & slug_words)

                    if text_overlap >= max(1, len(query_words) // 2) or slug_overlap >= max(1, len(query_words) // 2):
                        found_url = link_info["href"]
                        print(f"   ✅ Category page match: {found_url}", flush=True)
                        break
            except Exception as e:
                print(f"   ⚠️ Category scan failed: {e}", flush=True)

    finally:
        await context.close()

    return found_url

# ==============================================================================
# 🛠️ Dead Link Repair Pipeline (পুরানো মুভির Dead Link চেক ও রিপেয়ার)
# ==============================================================================
REPAIR_LIMIT_PER_RUN = 20

async def repair_dead_links(target_category_name, config):
    """Category-র existing movies-এ dead link চেক করে fresh link দিয়ে replace করে।"""
    json_filename = config["json"]
    output_filename = config["file"]
    m3u_filename = config["m3u"]
    cat_dir = config["dir"]
    cat_slug = config["slug"]

    os.makedirs(cat_dir, exist_ok=True)

    # ১. Existing movies লোড করা
    existing_movies = []
    if os.path.exists(json_filename):
        try:
            with open(json_filename, "r", encoding="utf-8") as f:
                data = json.load(f)
                existing_movies = data.get("movies", [])
        except Exception:
            pass

    if not existing_movies:
        existing_movies = parse_existing_output_file(output_filename)

    if not existing_movies:
        print(f"ℹ️ No existing movies found for [{target_category_name}]. Nothing to repair.", flush=True)
        return

    print(f"\n{'='*60}", flush=True)
    print(f"🛠️ REPAIR MODE: Checking [{target_category_name}] — {len(existing_movies)} movie(s)", flush=True)
    print(f"{'='*60}\n", flush=True)

    # ২. Failed repairs tracker লোড
    failed_repairs = load_failed_repairs()

    # ৩. Dead link detection — সব movie ও link একসাথে parallel চেক (⚡ Fast)
    movies_with_dead_links = []

    # Limit করা movies বাছাই (skip ও limit আগেই apply)
    candidates = []
    checked_count = 0
    for movie in existing_movies:
        if checked_count >= REPAIR_LIMIT_PER_RUN:
            print(f"⚠️ Repair limit reached ({REPAIR_LIMIT_PER_RUN} movies checked). Remaining will be checked next run.", flush=True)
            break
        movie_name = movie.get("name", "Unknown")
        if should_skip_repair(movie_name, failed_repairs):
            print(f"⏭️ Skipping '{movie_name}' (failed repair < 24h ago)", flush=True)
            continue
        res_list = movie.get("res_list", [])
        if not res_list:
            continue
        checked_count += 1
        candidates.append(movie)

    # ⚡ সব link একসাথে parallel check (max 15 concurrent)
    sem = asyncio.Semaphore(15)

    async def check_one_link(movie_name, idx, link):
        """একটা link check করে result দেয়।"""
        if not link or link in ["N/A", ""]:
            return idx, True, link  # empty = dead
        async with sem:
            is_dead = await asyncio.to_thread(is_stream_link_dead_sync, link)
        if is_dead:
            print(f"   💀 Dead link found in '{movie_name}': {link[:70]}...", flush=True)
        else:
            print(f"   ✅ Link alive for '{movie_name}': {link[:70]}...", flush=True)
        return idx, is_dead, link

    async def check_movie_links(movie):
        """একটা movie-র সব link parallel check করে।"""
        movie_name = movie.get("name", "Unknown")
        res_list = movie.get("res_list", [])
        tasks = [
            check_one_link(movie_name, idx, item.get("link", ""))
            for idx, item in enumerate(res_list)
        ]
        results = await asyncio.gather(*tasks)
        dead_indices = [idx for idx, is_dead, _ in results if is_dead]
        dead_links = [link for _, is_dead, link in results if is_dead]
        return movie, dead_indices, dead_links

    # সব candidate movie একসাথে check
    all_movie_results = await asyncio.gather(*[check_movie_links(m) for m in candidates])

    for movie, dead_indices, dead_links in all_movie_results:
        if dead_indices:
            movies_with_dead_links.append({
                "movie": movie,
                "dead_indices": dead_indices,
                "dead_links": dead_links
            })

    if not movies_with_dead_links:
        print(f"\n✅ All checked links are alive! No repairs needed for [{target_category_name}].", flush=True)
        return

    print(f"\n🔧 Found {len(movies_with_dead_links)} movie(s) with dead links. Starting repair...\n", flush=True)

    # ৪. Repair — Playwright দিয়ে fresh link scrape
    repaired_count = 0
    failed_count = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=LAUNCH_ARGS)

        try:
            for repair_info in movies_with_dead_links:
                movie = repair_info["movie"]
                dead_indices = repair_info["dead_indices"]
                movie_name = movie.get("name", "Unknown")
                movie_category = movie.get("category", target_category_name)

                print(f"\n🔧 Repairing: '{movie_name}'", flush=True)

                # Step A: history.txt থেকে original page URL খোঁজা
                history_filename = os.path.join(cat_dir, "history.txt")
                original_page_url = None

                if os.path.exists(history_filename):
                    name_slug = movie_name.lower().replace(" ", "-").replace("'", "").replace(":", "")
                    with open(history_filename, "r", encoding="utf-8") as hf:
                        for line in hf:
                            line = line.strip()
                            if name_slug in line.lower() or any(word in line.lower() for word in movie_name.lower().split()[:3] if len(word) > 3):
                                original_page_url = line
                                break

                # Step B: Original page alive কিনা চেক
                repair_page_url = None

                if original_page_url:
                    print(f"   📄 Found history URL: {original_page_url}", flush=True)
                    page_alive = await asyncio.to_thread(is_page_url_alive_sync, original_page_url)
                    if page_alive:
                        repair_page_url = original_page_url
                        print(f"   ✅ Original page is alive!", flush=True)
                    else:
                        print(f"   ❌ Original page is DEAD (404). Searching for new page...", flush=True)

                # Step C: Page dead বা history-তে URL নেই → main site search
                if not repair_page_url:
                    searched_url = await search_movie_on_site(browser, movie_name, cat_slug)
                    if searched_url:
                        repair_page_url = searched_url
                    else:
                        print(f"   ❌ Could not find '{movie_name}' on site. Logging as failed.", flush=True)
                        record_failed_repair(movie_name, movie_category, repair_info["dead_links"], failed_repairs)
                        failed_count += 1
                        continue

                # Step D: Page থেকে fresh stream link scrape
                print(f"   🎬 Re-scraping from: {repair_page_url}", flush=True)
                _, _, _, fresh_res_list, _ = await process_movie_parallel_pipeline(
                    browser, repair_page_url, 0, target_category_name
                )

                if not fresh_res_list:
                    print(f"   ❌ No fresh stream links found for '{movie_name}'. Logging as failed.", flush=True)
                    record_failed_repair(movie_name, movie_category, repair_info["dead_links"], failed_repairs)
                    failed_count += 1
                    continue

                # Step E: নতুন link validate করা (double-check)
                validated_fresh = []
                for fresh_item in fresh_res_list:
                    fresh_link = fresh_item.get("link", "")
                    fresh_dead = await asyncio.to_thread(is_stream_link_dead_sync, fresh_link)
                    if not fresh_dead:
                        validated_fresh.append(fresh_item)
                        print(f"   ✅ Validated fresh link: {fresh_link[:70]}...", flush=True)
                    else:
                        print(f"   ⚠️ Fresh link ALSO dead (skipping): {fresh_link[:70]}...", flush=True)

                if not validated_fresh:
                    print(f"   ❌ All fresh links are also dead for '{movie_name}'. Logging as failed.", flush=True)
                    record_failed_repair(movie_name, movie_category, repair_info["dead_links"], failed_repairs)
                    failed_count += 1
                    continue

                # Step F: Dead link replace করা
                original_res_list = movie.get("res_list", [])
                new_res_list = []

                for idx, item in enumerate(original_res_list):
                    if idx in dead_indices:
                        old_res = item.get("resolution", "HD 1080P")
                        replaced = False
                        for fresh in validated_fresh:
                            fresh_res = fresh.get("resolution", "HD 1080P")
                            if fresh_res == old_res or not replaced:
                                new_item = {
                                    "resolution": fresh.get("resolution", old_res),
                                    "link": fresh["link"]
                                }
                                if item.get("season") and item["season"] != "N/A":
                                    new_item["season"] = item["season"]
                                if item.get("episode") and item["episode"] != "N/A":
                                    new_item["episode"] = item["episode"]
                                new_res_list.append(new_item)
                                validated_fresh.remove(fresh)
                                replaced = True
                                break
                        if not replaced:
                            new_res_list.append(item)
                    else:
                        new_res_list.append(item)

                # বাকি validated fresh links যেগুলো unused — add করা
                for extra_fresh in validated_fresh:
                    new_res_list.append(extra_fresh)

                movie["res_list"] = new_res_list
                repaired_count += 1
                print(f"   ✅ Successfully repaired '{movie_name}'!", flush=True)

                # Repair সফল হলে failed_repairs থেকে remove
                key = movie_name.lower().strip()
                if key in failed_repairs:
                    del failed_repairs[key]

        finally:
            await browser.close()

    # ৫. Failed repairs সেভ
    save_failed_repairs(failed_repairs)

    # ৬. Updated files সেভ (TXT, JSON, M3U)
    if repaired_count > 0:
        def get_sort_year(m):
            y = m.get("year", "0")
            return int(y) if str(y).isdigit() else 0
        existing_movies.sort(key=get_sort_year, reverse=True)

        current_utc_time = datetime.now(timezone.utc).strftime("%Y-%m-%d | %H:%M:%S (UTC)")
        total_items_count = len(existing_movies)

        # TXT file
        with open(output_filename, "w", encoding="utf-8") as f:
            f.write("=" * 80 + "\n")
            f.write(f"CATEGORY: {target_category_name}\n")
            f.write(f"TOTAL MOVIES: {total_items_count}\n")
            f.write(f"LAST UPDATED: {current_utc_time}\n")
            f.write("NOTICE: This repository and data are created strictly for EDUCATIONAL PURPOSES only and not for any commercial use.\n")
            f.write("=" * 80 + "\n\n")

            for idx, movie in enumerate(existing_movies, 1):
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

        # JSON file
        clean_movies_for_json = []
        for movie in existing_movies:
            m_obj = {
                "name": movie["name"],
                "category": movie["category"],
                "year": movie["year"],
                "poster": movie["poster"],
                "res_list": []
            }
            for item in movie.get("res_list", []):
                res_item = {}
                s_val = item.get("season")
                e_val = item.get("episode")
                if s_val and s_val != "N/A":
                    res_item["season"] = s_val
                if e_val and e_val != "N/A":
                    res_item["episode"] = e_val
                res_item["resolution"] = item.get("resolution", "HD 1080P")
                res_item["link"] = item.get("link", "")
                m_obj["res_list"].append(res_item)
            clean_movies_for_json.append(m_obj)

        json_payload = {
            "category_info": {
                "category_name": target_category_name,
                "total_movies": total_items_count,
                "last_updated": current_utc_time,
                "purpose": "Strictly for educational purposes and not for commercial use."
            },
            "movies": clean_movies_for_json
        }
        with open(json_filename, "w", encoding="utf-8") as jf:
            json.dump(json_payload, jf, indent=2, ensure_ascii=False)

        # M3U file
        with open(m3u_filename, "w", encoding="utf-8") as m3u:
            m3u.write("#EXTM3U\n")
            m3u.write(f"#EXT-X-NAME: {target_category_name}\n")
            m3u.write(f"#EXT-X-TOTAL-ITEMS: {total_items_count}\n")
            m3u.write(f"#EXT-X-UPDATED: {current_utc_time}\n")
            m3u.write("#EXT-X-NOTICE: Strictly for EDUCATIONAL PURPOSES only, not for commercial use.\n\n")

            for movie in existing_movies:
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

        print(f"\n✅ Repair complete! Repaired: {repaired_count}, Failed: {failed_count}", flush=True)
        print(f"✅ Updated TXT, JSON, M3U files for [{target_category_name}].", flush=True)
    else:
        print(f"\n⚠️ No movies were successfully repaired. Failed: {failed_count}", flush=True)



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
    elif SCAN_MODE == "REPAIR_AUTO":
        # 🛠️ REPAIR_AUTO — ট্র্যাকার স্টেট অনুযায়ী category rotate করে repair
        if cat_index >= len(CATEGORIES_LIST):
            cat_index = 0
        repair_cat = CATEGORIES_LIST[cat_index]
        repair_config = CATEGORIES_MAP[repair_cat]
        print(f"🛠️ [REPAIR AUTO] Repairing Category: '{repair_cat}'", flush=True)
        await repair_dead_links(repair_cat, repair_config)
        # Rotate to next category for next run
        next_index = (cat_index + 1) % len(CATEGORIES_LIST)
        state["current_category_index"] = next_index
        state["run_count"] = 1
        save_tracker_state(state)
        print(f"🔄 [STATE UPDATE] Repair done for '{repair_cat}'. Next repair target: '{CATEGORIES_LIST[next_index]}'", flush=True)
        print("\n🎉 Repair Completed Successfully!", flush=True)
        return
    elif SCAN_MODE == "REPAIR_SPECIFIC":
        # 🛠️ REPAIR_SPECIFIC — নির্দিষ্ট category repair
        repair_cat = REPAIR_CATEGORY
        if repair_cat not in CATEGORIES_MAP:
            repair_cat = CATEGORIES_LIST[0]
            print(f"⚠️ Invalid REPAIR_CATEGORY. Defaulting to '{repair_cat}'", flush=True)
        repair_config = CATEGORIES_MAP[repair_cat]
        print(f"🛠️ [REPAIR SPECIFIC] Repairing Category: '{repair_cat}'", flush=True)
        await repair_dead_links(repair_cat, repair_config)
        print("\n🎉 Repair Completed Successfully!", flush=True)
        return
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

            # 🎯 ৫. [POSTER & YEAR AUTO-REPAIR] পুরানো মুভির Year লিঙ্ক থেকে রিকভার ও মিসিং পোস্টার TMDB ব্যাকফিল
            for m in all_movies:
                if not m.get("year") or m["year"] == "N/A":
                    if m.get("res_list"):
                        for item in m["res_list"]:
                            link_str = urllib.parse.unquote(item.get("link", ""))
                            y_match = re.search(r'\b(20\d{2}|19\d{2})\b', link_str)
                            if y_match:
                                m["year"] = y_match.group(1)
                                break

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

            # 🎯 ৮. JSON ফাইল জেনারেট ও ওভাররাইট করা (সাধারণ মুভিতে season/episode বাদ দিয়ে ক্লিন JSON)
            clean_movies_for_json = []
            for movie in all_movies:
                m_obj = {
                    "name": movie["name"],
                    "category": movie["category"],
                    "year": movie["year"],
                    "poster": movie["poster"],
                    "res_list": []
                }
                for item in movie.get("res_list", []):
                    res_item = {}
                    s_val = item.get("season")
                    e_val = item.get("episode")

                    if s_val and s_val != "N/A":
                        res_item["season"] = s_val
                    if e_val and e_val != "N/A":
                        res_item["episode"] = e_val

                    res_item["resolution"] = item.get("resolution", "HD 1080P")
                    res_item["link"] = item.get("link", "")
                    m_obj["res_list"].append(res_item)

                clean_movies_for_json.append(m_obj)

            json_payload = {
                "category_info": {
                    "category_name": target_category_name,
                    "total_movies": total_items_count,
                    "last_updated": current_utc_time,
                    "purpose": "Strictly for educational purposes and not for commercial use."
                },
                "movies": clean_movies_for_json
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