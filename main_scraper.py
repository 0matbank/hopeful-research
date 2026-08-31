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
import unicodedata
from difflib import SequenceMatcher
from datetime import datetime, timezone
from playwright.async_api import async_playwright

# Scheduled runs normally use UTF-8, but an imported scanner can inherit the
# legacy Windows console encoding.  Keep status output from aborting a repair.
if os.name == "nt" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError, ValueError):
        pass

nest_asyncio.apply()

# ==============================================================================
# ⚙️ ১. কনফিগারেশন (GitHub/Colab Secrets থেকে লোড করা)
# ==============================================================================
MAIN_SITE_URL = os.environ.get("MAIN_SITE_URL", "").rstrip("/") + "/"
SCAN_MODE = os.environ.get("SCAN_MODE", "AUTO").strip()
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
OMDB_API_KEY = os.environ.get("OMDB_API_KEY", "e0d2217b")

STATE_FILE = "history/tracker_state.json"
FAILED_REPAIRS_FILE = "history/failed_repairs.json"
CANDIDATE_RETRY_FILE = "history/candidate_retry_state.json"
TARGET_LIMIT_MOVIES = 10
CANDIDATE_DISCOVERY_LIMIT = 60
CANDIDATE_RETRY_DELAYS_HOURS = (12, 24, 72, 168)

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

# রানিং environment detect
RUN_ENV = "github" if os.environ.get("GITHUB_ACTIONS") else "colab"

# ⚡ Time limit system — GitHub Actions-এ timeout হওয়ার আগেই থামবে
SCRAPER_START_TIME = time.time()
# GitHub-এ 40 মিনিট স্ক্র্যাপিং, 15 মিনিট repair, 5 মিনিট push এর জন্য রেখে
# Colab-এ কোনো timeout নেই
MAX_SCRAPE_MINUTES = 40 if RUN_ENV == "github" else 9999
MAX_REPAIR_MINUTES = 15 if RUN_ENV == "github" else 9999

def is_time_running_out(max_minutes=None):
    """নির্ধারিত সময়ের ভেতরে কাজ শেষ করতে হবে কিনা চেক করে।"""
    if max_minutes is None:
        max_minutes = MAX_SCRAPE_MINUTES
    elapsed = (time.time() - SCRAPER_START_TIME) / 60
    return elapsed >= max_minutes

LAUNCH_ARGS = [
    # স্টেল্থ মোড — automation detect এড়াতে
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-infobars",
    "--disable-extensions",
    # fingerprint masking
    "--disable-web-security",
    "--disable-features=IsolateOrigins,site-per-process",
    "--ignore-certificate-errors",
    "--allow-running-insecure-content",
    # memory/performance
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
    # রেন্ডম timezone দেওয়া (বাংলাদেশ বা ভারতের যেকোনো)
    "--lang=en-US,en",
    "--window-size=1366,768",
]

USER_AGENTS = [
    # Windows Chrome (latest)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Mac Chrome
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    # Windows Firefox
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    # Mac Firefox
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:127.0) Gecko/20100101 Firefox/127.0",
    # Windows Edge
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
    # Mobile Chrome (Android)
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.60 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
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


def normalize_title_key(value):
    """Return a conservative comparison key for movie-title validation."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"\[18\+\]", " ", text)
    text = re.sub(r"\b(?:full|movie|series|watch|download|web|dl|bluray|hdtc|esub|hevc)\b", " ", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def title_similarity(left, right):
    left_key = normalize_title_key(left)
    right_key = normalize_title_key(right)
    if not left_key or not right_key:
        return 0.0
    if left_key == right_key:
        return 1.0
    if left_key in right_key or right_key in left_key:
        shorter = min(len(left_key), len(right_key))
        longer = max(len(left_key), len(right_key))
        return 0.80 + (0.20 * shorter / longer)
    return SequenceMatcher(None, left_key, right_key).ratio()


def movie_identity_key(name, year, is_series=False):
    """Keep separate releases/pages while merging exact title/year duplicates."""
    normalized_year = str(year) if str(year).isdigit() else "N/A"
    title_key = normalize_title_key(name)
    if re.match(r"^\s*the\b", str(name or ""), flags=re.IGNORECASE):
        title_key = title_key[3:]
    return title_key, normalized_year


def extract_stream_identity(res_list):
    """Extract the content title/year from a captured direct-stream filename."""
    for item in res_list or []:
        stream_url = urllib.parse.unquote(item.get("link", ""))
        filename = urllib.parse.urlparse(stream_url).path.rsplit("/", 1)[-1]
        filename = re.sub(r"\.(?:mkv|mp4|m3u8)$", "", filename, flags=re.IGNORECASE)
        filename = re.sub(r"^CINEFREAK\.TOP\s*-\s*", "", filename, flags=re.IGNORECASE)

        year_match = re.search(r"\(((?:19|20)\d{2})\)", filename)
        stream_year = year_match.group(1) if year_match else "N/A"
        title_part = filename[:year_match.start()] if year_match else filename
        title_part = re.split(
            r"\s*(?:\[S\d{1,2}E\d|\bS\d{1,2}\b|\bWEB-DL\b|\bBluRay\b|\bHDTC\b|\bDS4K\b|\b1080p\b|\b2160p\b)",
            title_part,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        stream_title = re.sub(r"\s+", " ", title_part).strip(" -_[]()")
        if stream_title:
            return stream_title, stream_year
    return "", "N/A"


def resolve_movie_identity(page_title, res_list):
    """Prefer stream metadata only when it clearly contradicts the page title."""
    page_title = re.sub(r"\s+", " ", str(page_title or "")).strip()
    page_year_match = re.search(r"\(((?:19|20)\d{2})\)", page_title)
    if not page_year_match:
        page_year_match = re.search(r"\b((?:19|20)\d{2})\b", page_title)
    page_year = page_year_match.group(1) if page_year_match else "N/A"

    page_name = page_title
    if page_year_match:
        leading_title = page_title[:page_year_match.start()].strip()
        if leading_title:
            page_name = leading_title
    page_name = re.split(
        r"\s*(?:\||\bFull Movie\b|\bWEB-DL\b|\bBluRay\b|\bHDTC\b)",
        page_name,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    page_name = re.sub(r"^(?:Watch|Download)\s+", "", page_name, flags=re.IGNORECASE)
    page_name = re.sub(r"[\s\-\[\]()]+$", "", page_name).strip()

    stream_name, stream_year = extract_stream_identity(res_list)
    descriptive_words = re.findall(r"[A-Za-z]{2,}", stream_name)
    stream_name_is_descriptive = len(descriptive_words) >= 2 and bool(re.search(r"\s", stream_name))
    used_stream_identity = False
    if not page_name or page_name == "Movie Post":
        if stream_name_is_descriptive:
            page_name = stream_name
            used_stream_identity = True
        else:
            page_name = "Movie Post"
    elif stream_name_is_descriptive and title_similarity(page_name, stream_name) < 0.55:
        print(f"WARNING: Title/content mismatch: page='{page_name}', stream='{stream_name}'. Using stream identity.", flush=True)
        page_name = stream_name
        used_stream_identity = True

    if stream_year != "N/A" and (page_year == "N/A" or used_stream_identity):
        year = stream_year
    else:
        year = page_year
    return page_name, year


def poster_result_matches(movie_name, year, result_title, result_year=None):
    """Reject unrelated first-search-result posters."""
    if title_similarity(movie_name, result_title) < 0.72:
        return False
    wanted_year = str(year or "")
    found_year_match = re.search(r"\b(?:19|20)\d{2}\b", str(result_year or ""))
    if wanted_year.isdigit() and found_year_match and wanted_year != found_year_match.group(0):
        return False
    return True

# ==============================================================================
# ⚡ রিয়েল ওয়াচ লিঙ্ক প্রটেক্টেড হেলথ চেক (Improved — CDN ও validate করে)
# ==============================================================================
def is_media_payload_sample(payload, content_type="", url=""):
    """Recognize actual media bytes and reject HTML/JSON error bodies."""
    payload = bytes(payload or b"")
    content_type = str(content_type or "").split(";", 1)[0].strip().lower()
    prefix = payload[:512].lstrip().lower()

    if not payload or prefix.startswith((b"<!doctype html", b"<html", b"<?xml", b"{")):
        return False
    if payload.startswith(b"\x1aE\xdf\xa3"):  # Matroska/WebM EBML
        return True
    if b"ftyp" in payload[:64]:  # MP4/MOV
        return True
    if prefix.startswith(b"#extm3u"):  # HLS playlist
        return True
    if payload.startswith((b"FLV", b"OggS")):
        return True
    if payload.startswith(b"RIFF") and payload[8:12] in {b"AVI ", b"WAVE"}:
        return True
    if len(payload) > 376 and payload[0] == 0x47 and payload[188] == 0x47:
        return True
    return content_type.startswith(("video/", "audio/")) and len(payload) >= 1024


def is_obvious_non_movie_media_url(stream_url):
    """Reject short advertising/product assets that happen to be valid video files."""
    try:
        parsed = urllib.parse.urlsplit(str(stream_url or "").strip())
    except ValueError:
        return True

    host = parsed.netloc.lower().split(":", 1)[0]
    path = urllib.parse.unquote(parsed.path).lower()
    if host in {"video.wixstatic.com", "m.media-amazon.com", "www.vpnapptoggle.com", "vpnapptoggle.com"}:
        return True
    return any(marker in path for marker in (
        "/landers/",
        "/assets/img/",
        "/480p/mp4/file.mp4",
        "/background.mp4",
        "/low-power-mode.mp4",
    ))


def probe_stream_link_sync(stream_url, timeout=8):
    """Return an alive/dead/transient verdict based on readable media bytes."""
    clean_url = str(stream_url or "").strip()
    if not clean_url or clean_url == "N/A":
        return {"status": "dead", "reason": "empty_url", "http_status": 0}
    if is_obvious_non_movie_media_url(clean_url):
        return {"status": "dead", "reason": "non_movie_media_asset", "http_status": 0}

    request = urllib.request.Request(
        clean_url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Range": "bytes=0-65535",
            "Accept": "*/*",
            "Accept-Encoding": "identity",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            payload = response.read(65536)
            content_type = response.headers.get("Content-Type", "")
            if status in {200, 206} and is_media_payload_sample(payload, content_type, clean_url):
                return {"status": "alive", "reason": "media_bytes_ok", "http_status": status}
            if status in {200, 206}:
                return {"status": "dead", "reason": "non_media_payload", "http_status": status}
            return {"status": "transient", "reason": f"http_{status}", "http_status": status}
    except urllib.error.HTTPError as error:
        status = int(error.code)
        if status in {401, 403, 404, 410}:
            return {"status": "dead", "reason": f"http_{status}", "http_status": status}
        return {"status": "transient", "reason": f"http_{status}", "http_status": status}
    except (urllib.error.URLError, TimeoutError) as error:
        return {"status": "transient", "reason": type(error).__name__.lower(), "http_status": 0}
    except Exception as error:
        return {"status": "transient", "reason": type(error).__name__.lower(), "http_status": 0}


def is_stream_link_dead_sync(stream_url):
    """True unless a range GET returns recognizable playable media bytes."""
    return probe_stream_link_sync(stream_url)["status"] != "alive"

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
        if e.code in [404, 410]:
            return False
        # Cloudflare অনেক সময় urllib HEAD-এ 403 দিলেও browser/WARP-এ page খোলে।
        if e.code == 403:
            return True
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

def failed_repair_key(movie_name, category):
    return f"{normalize_title_key(category)}::{normalize_title_key(movie_name)}"


def should_skip_repair(movie_name, category, failed_repairs):
    """24 ঘণ্টার মধ্যে আবার retry করবে না।"""
    key = failed_repair_key(movie_name, category)
    entry = failed_repairs.get(key)
    if entry is None:
        # পুরোনো title-only tracker backward-compatibleভাবে পড়ি, কিন্তু category মিললেই।
        legacy_entry = failed_repairs.get(movie_name.lower().strip())
        if isinstance(legacy_entry, dict) and legacy_entry.get("category") == category:
            entry = legacy_entry
    if entry is not None:
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
    key = failed_repair_key(movie_name, category)
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


def clear_failed_repair(movie_name, category, failed_repairs):
    failed_repairs.pop(failed_repair_key(movie_name, category), None)
    legacy_key = movie_name.lower().strip()
    legacy_entry = failed_repairs.get(legacy_key)
    if isinstance(legacy_entry, dict) and legacy_entry.get("category") == category:
        failed_repairs.pop(legacy_key, None)

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
            ranked_results = sorted(
                results,
                key=lambda item: max(
                    title_similarity(movie_name, item.get("title", "")),
                    title_similarity(movie_name, item.get("original_title", "")),
                ),
                reverse=True,
            )
            for item in ranked_results:
                result_title = item.get("title") or item.get("original_title") or ""
                result_year = item.get("release_date", "")
                if not poster_result_matches(movie_name, year, result_title, result_year):
                    continue
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
                        if (
                            poster
                            and poster != "N/A"
                            and poster.startswith("http")
                            and poster_result_matches(movie_name, year, data.get("Title", ""), data.get("Year", ""))
                        ):
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
                    if (
                        poster
                        and poster != "N/A"
                        and poster.startswith("http")
                        and poster_result_matches(movie_name, year, data.get("Title", ""), data.get("Year", ""))
                    ):
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
                    ranked_metas = sorted(
                        metas,
                        key=lambda item: title_similarity(movie_name, item.get("name", "")),
                        reverse=True,
                    )
                    for item in ranked_metas:
                        if not poster_result_matches(movie_name, year, item.get("name", ""), item.get("releaseInfo", "")):
                            continue
                        poster = item.get("poster")
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
def parse_existing_output_file(file_path, source_urls=None):
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
            source_m = re.search(r"Source URL:\s*(.*)", block)

            if not name_m:
                continue

            name = name_m.group(1).strip()
            cat = cat_m.group(1).strip() if cat_m else "N/A"
            year_str = year_m.group(1).strip() if year_m else "N/A"
            poster = poster_m.group(1).strip() if poster_m else "N/A"
            source_url = source_m.group(1).strip().rstrip("/") if source_m else ""

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
                    "source_url": source_url,
                    "res_list": res_list
                })
    except Exception as e:
        print(f"⚠️ Error reading existing file {file_path}: {e}", flush=True)
    if source_urls:
        normalized_sources = [normalize_source_url(url) for url in source_urls]
        for index, movie in enumerate(movies):
            if not movie.get("source_url") and index < len(normalized_sources):
                movie["source_url"] = normalized_sources[index]
    return movies


def normalize_source_url(url):
    return str(url or "").strip().rstrip("/")


def load_history_urls(history_filename):
    if not os.path.exists(history_filename):
        return []
    seen = set()
    urls = []
    with open(history_filename, "r", encoding="utf-8") as history_file:
        for line in history_file:
            url = normalize_source_url(line)
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def load_existing_movies(config):
    """Load canonical JSON, with a history-mapped TXT fallback for recovery."""
    json_filename = config["json"]
    if os.path.exists(json_filename):
        try:
            with open(json_filename, "r", encoding="utf-8") as json_file:
                movies = json.load(json_file).get("movies", [])
            if movies:
                return movies
        except Exception:
            pass

    history_filename = os.path.join(config["dir"], "history.txt")
    return parse_existing_output_file(
        config["file"],
        load_history_urls(history_filename),
    )


def append_unique_url(filename, url):
    normalized_url = normalize_source_url(url)
    if not normalized_url:
        return
    existing_urls = set(load_history_urls(filename))
    if normalized_url not in existing_urls:
        with open(filename, "a", encoding="utf-8", newline="\n") as output_file:
            output_file.write(f"{normalized_url}\n")


def load_candidate_retry_state(filename=CANDIDATE_RETRY_FILE):
    """Load retry cooldowns for category-page candidates that produced no stream."""
    if not os.path.exists(filename):
        return {"version": 1, "categories": {}}
    try:
        with open(filename, "r", encoding="utf-8") as state_file:
            state = json.load(state_file)
        if not isinstance(state, dict) or not isinstance(state.get("categories"), dict):
            raise ValueError("invalid candidate retry state")
        state["version"] = 1
        return state
    except Exception:
        return {"version": 1, "categories": {}}


def save_candidate_retry_state(state, filename=CANDIDATE_RETRY_FILE):
    """Persist candidate retry state without risking a partially written JSON file."""
    parent = os.path.dirname(filename) or "."
    os.makedirs(parent, exist_ok=True)
    temporary_filename = f"{filename}.tmp"
    with open(temporary_filename, "w", encoding="utf-8", newline="\n") as state_file:
        json.dump(state, state_file, indent=2, ensure_ascii=False)
        state_file.write("\n")
    os.replace(temporary_filename, filename)


def select_scan_candidates(discovered_urls, category, retry_state, now_timestamp=None, limit=TARGET_LIMIT_MOVIES):
    """Prefer unseen candidates and postpone failed ones until their retry window."""
    now_timestamp = time.time() if now_timestamp is None else float(now_timestamp)
    category_state = retry_state.setdefault("categories", {}).setdefault(category, {})
    fresh = []
    due = []
    cooling = []
    seen = set()

    for raw_url in discovered_urls:
        url = normalize_source_url(raw_url)
        if not url or url in seen:
            continue
        seen.add(url)
        entry = category_state.get(url)
        if not isinstance(entry, dict):
            fresh.append(url)
        elif float(entry.get("retry_after_epoch", 0) or 0) <= now_timestamp:
            due.append(url)
        else:
            cooling.append(url)

    selected = (fresh + due)[:limit]
    return selected, len(cooling)


def record_candidate_outcome(category, url, succeeded, retry_state, now_timestamp=None):
    """Clear successful candidates or apply bounded exponential retry backoff."""
    now_timestamp = time.time() if now_timestamp is None else float(now_timestamp)
    normalized_url = normalize_source_url(url)
    category_state = retry_state.setdefault("categories", {}).setdefault(category, {})

    if succeeded:
        return category_state.pop(normalized_url, None) is not None

    previous = category_state.get(normalized_url, {})
    failure_count = int(previous.get("failure_count", 0) or 0) + 1
    delay_index = min(failure_count - 1, len(CANDIDATE_RETRY_DELAYS_HOURS) - 1)
    delay_hours = CANDIDATE_RETRY_DELAYS_HOURS[delay_index]
    category_state[normalized_url] = {
        "failure_count": failure_count,
        "last_failed_at": datetime.fromtimestamp(now_timestamp, timezone.utc).isoformat(),
        "retry_after_epoch": int(now_timestamp + delay_hours * 3600),
        "retry_after": datetime.fromtimestamp(now_timestamp + delay_hours * 3600, timezone.utc).isoformat(),
    }
    return True


def source_url_match_score(movie, source_url):
    slug = urllib.parse.unquote(urllib.parse.urlparse(source_url).path).strip("/").rsplit("/", 1)[-1]
    scores = [title_similarity(movie.get("name", ""), slug)]
    stream_name, _ = extract_stream_identity(movie.get("res_list", []))
    if stream_name:
        scores.append(title_similarity(stream_name, slug))
    return max(scores)


def find_source_url_for_movie(movie, history_urls):
    stored_url = normalize_source_url(movie.get("source_url", ""))
    if stored_url:
        return stored_url
    ranked = sorted(
        ((source_url_match_score(movie, url), url) for url in history_urls),
        reverse=True,
    )
    if ranked and ranked[0][0] >= 0.72:
        return ranked[0][1]
    return ""


def is_series_movie(movie):
    res_list = movie.get("res_list", [])
    return any(
        item.get("season", "N/A") != "N/A" or item.get("episode", "N/A") != "N/A"
        for item in res_list
        if isinstance(item, dict)
    )


def merge_duplicate_movies(movies):
    """Merge duplicate records after recovering series metadata from filenames."""
    merged = []
    by_identity = {}
    alias_source_urls = []
    for movie in movies:
        normalized_res_list = []
        for item in movie.get("res_list", []):
            normalized_item = dict(item)
            detected = parse_link_metadata(item.get("link", ""), item.get("resolution", "HD 1080P"))
            if normalized_item.get("season", "N/A") == "N/A" and detected["season"] != "N/A":
                normalized_item["season"] = detected["season"]
            if normalized_item.get("episode", "N/A") == "N/A" and detected["episode"] != "N/A":
                normalized_item["episode"] = detected["episode"]
            normalized_res_list.append(normalized_item)
        movie["res_list"] = normalized_res_list

        identity = movie_identity_key(movie.get("name", ""), movie.get("year"), is_series_movie(movie))
        if identity not in by_identity:
            by_identity[identity] = movie
            merged.append(movie)
            continue

        target = by_identity[identity]
        existing_links = {item.get("link") for item in target.get("res_list", [])}
        for item in movie.get("res_list", []):
            if item.get("link") not in existing_links:
                target.setdefault("res_list", []).append(item)
                existing_links.add(item.get("link"))
        if target.get("poster") in {None, "", "N/A", "None"} and movie.get("poster"):
            target["poster"] = movie["poster"]
        alias_url = normalize_source_url(movie.get("source_url", ""))
        if alias_url and alias_url != normalize_source_url(target.get("source_url", "")):
            alias_source_urls.append(alias_url)

    return merged, alias_source_urls


def save_category_outputs(target_category_name, config, movies):
    """Write TXT/JSON/M3U/history from one canonical in-memory movie list."""
    output_filename = config["file"]
    json_filename = config["json"]
    m3u_filename = config["m3u"]
    history_filename = os.path.join(config["dir"], "history.txt")
    skipped_history_filename = os.path.join(config["dir"], "history_skipped.txt")

    movies, alias_source_urls = merge_duplicate_movies(movies)
    for alias_url in alias_source_urls:
        append_unique_url(skipped_history_filename, alias_url)

    def get_sort_year(movie):
        value = movie.get("year", "0")
        return int(value) if str(value).isdigit() else 0

    movies.sort(key=get_sort_year, reverse=True)
    current_utc_time = datetime.now(timezone.utc).strftime("%Y-%m-%d | %H:%M:%S (UTC)")
    total_items_count = len(movies)

    source_urls = []
    for movie in movies:
        source_url = normalize_source_url(movie.get("source_url", ""))
        if not source_url:
            raise ValueError(f"Missing source_url for '{movie.get('name', 'Unknown')}'")
        movie["source_url"] = source_url
        source_urls.append(source_url)
        if not movie.get("name") or not movie.get("res_list"):
            raise ValueError(f"Incomplete movie record for source URL: {source_url}")

    if len(set(source_urls)) != len(source_urls):
        raise ValueError("Duplicate source_url values found; history cannot be synchronized safely")

    with open(output_filename, "w", encoding="utf-8", newline="\n") as output_file:
        output_file.write("=" * 80 + "\n")
        output_file.write(f"CATEGORY: {target_category_name}\n")
        output_file.write(f"TOTAL MOVIES: {total_items_count}\n")
        output_file.write(f"LAST UPDATED: {current_utc_time}\n")
        output_file.write("NOTICE: This repository and data are created strictly for EDUCATIONAL PURPOSES only and not for any commercial use.\n")
        output_file.write("=" * 80 + "\n\n")

        for index, movie in enumerate(movies, 1):
            is_series = is_series_movie(movie)
            title_type = "Show name" if is_series else "Movie name"
            output_file.write(f"Movie-{index}\n")
            output_file.write(f"{title_type}: {movie['name']}\n")
            output_file.write(f"Category: {movie['category']}\n")
            output_file.write(f"Year: {movie['year']}\n")
            output_file.write(f"Poster: {movie['poster']}\n\n")

            if is_series:
                for link_index, item in enumerate(movie["res_list"], 1):
                    # প্রতি link-এর metadata লিখি, যাতে mixed season/episode carry-over না হয়।
                    output_file.write(f"Season: {item.get('season', 'N/A')}\n")
                    output_file.write(f"Episode: {item.get('episode', 'N/A')}\n")
                    output_file.write(f"  Resolution-{link_index}: {item.get('resolution', 'HD 1080P')}\n")
                    output_file.write(f"  Link-{link_index}: {item['link']}\n")
                    output_file.write("\n")
            else:
                for link_index, item in enumerate(movie["res_list"], 1):
                    output_file.write(f"RESOLUTION {link_index}: {item.get('resolution', 'HD 1080P')}\n")
                    output_file.write(f"STREAM Link {link_index}: {item['link']}\n\n")
            output_file.write("=" * 80 + "\n\n")

    clean_movies_for_json = []
    for movie in movies:
        movie_object = {
            "name": movie["name"],
            "category": movie["category"],
            "year": movie["year"],
            "poster": movie["poster"],
            "source_url": movie["source_url"],
            "res_list": [],
        }
        for item in movie.get("res_list", []):
            result_item = {}
            if item.get("season", "N/A") != "N/A":
                result_item["season"] = item["season"]
            if item.get("episode", "N/A") != "N/A":
                result_item["episode"] = item["episode"]
            result_item["resolution"] = item.get("resolution", "HD 1080P")
            result_item["link"] = item.get("link", "")
            movie_object["res_list"].append(result_item)
        clean_movies_for_json.append(movie_object)

    json_payload = {
        "category_info": {
            "category_name": target_category_name,
            "total_movies": total_items_count,
            "last_updated": current_utc_time,
            "purpose": "Strictly for educational purposes and not for commercial use.",
        },
        "movies": clean_movies_for_json,
    }
    with open(json_filename, "w", encoding="utf-8", newline="\n") as json_file:
        json.dump(json_payload, json_file, indent=2, ensure_ascii=False)

    with open(m3u_filename, "w", encoding="utf-8", newline="\n") as m3u_file:
        m3u_file.write("#EXTM3U\n")
        m3u_file.write(f"#EXT-X-NAME: {target_category_name}\n")
        m3u_file.write(f"#EXT-X-TOTAL-ITEMS: {total_items_count}\n")
        m3u_file.write(f"#EXT-X-UPDATED: {current_utc_time}\n")
        m3u_file.write("#EXT-X-NOTICE: Strictly for EDUCATIONAL PURPOSES only, not for commercial use.\n\n")
        for movie in movies:
            source_attr = movie["source_url"].replace('"', "%22")
            poster_attr = movie.get("poster", "N/A").replace('"', "%22")
            for item in movie.get("res_list", []):
                link_value = item.get("link")
                if not link_value:
                    continue
                resolution = item.get("resolution", "HD 1080P")
                season = item.get("season", "N/A")
                episode = item.get("episode", "N/A")
                if season != "N/A" or episode != "N/A":
                    metadata_parts = [value for value in (season, episode) if value != "N/A"]
                    title = f"{movie['name']} - {' '.join(metadata_parts)} - {resolution}"
                    group = f"{target_category_name} - {movie['name']}"
                else:
                    title = f"{movie['name']} ({movie.get('year', 'N/A')}) - {resolution}"
                    group = target_category_name
                m3u_file.write(
                    f'#EXTINF:-1 tvg-logo="{poster_attr}" group-title="{group}" source-url="{source_attr}", {title}\n'
                )
                m3u_file.write(f"{link_value}\n")

    with open(history_filename, "w", encoding="utf-8", newline="\n") as history_file:
        for source_url in source_urls:
            history_file.write(f"{source_url}\n")

    return total_items_count

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
        if re.search(r"(?:^|[. _-])HQ(?:[. _-]|$)", filename):
            return "HQ 1080P HEVC" if is_hevc else "HQ 1080P"
        return "HEVC 1080P" if is_hevc else "HD 1080P"

    return "HEVC 1080P" if is_hevc else "HD 1080P"

def is_genuine_direct_stream_url(url):
    u_lower = url.lower()

    if any(junk in u_lower for junk in [
        "yagaverse.net", "google-analytics", "cinecloud.site", "neodrive.site",
        "ping.gif", "jwpltx", "collect?", "facebook", "twitter", "manifest",
        "cloudflarestorage.com",  # ❌ direct download storage — stream না
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

        # 🎯 বাটন এক্সট্র্যাক্টর (Download বাটন সম্পূর্ণ বাদ দিয়ে শুধুমাত্র Watch বাটন ফিল্টারিং)
        target_buttons = await page.evaluate(r"""
            () => {
                let matches = [];
                let seenUrls = new Set();

                // ১. যদি পেজে .ep-card (সিরিজ পেজ) থাকে, তবে কার্ড বাই কার্ড আলাদা স্ক্যান
                let epCards = Array.from(document.querySelectorAll('.ep-card'));
                if (epCards.length > 0) {
                    epCards.forEach(card => {
                        let epTitle = card.querySelector('.ep-title, .ep-meta') ? card.querySelector('.ep-title, .ep-meta').innerText : 'Episode';

                        // শুধু .watch-links এর ভেতরের button — download-links সম্পূর্ণ বাদ
                        let watchBtns = Array.from(card.querySelectorAll('.watch-links a, a.dlbtn-watch'));

                        watchBtns.forEach(a => {
                            if (seenUrls.has(a.href)) return;

                            let txt = (a.innerText || '').toLowerCase().trim();

                            // ❌ যেকোনো download keyword থাকলে সম্পূর্ণ বাদ
                            if (txt.includes('download')) return;

                            // ❌ parent container যদি download-links হয় বাদ
                            let parentBox = a.closest('.download-links');
                            if (parentBox) return;

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
                await sub_page.goto(target_gateway_url, timeout=20000, wait_until="domcontentloaded")

                verify_btn = sub_page.locator("#btn-text")
                await verify_btn.wait_for(state="visible", timeout=8000)
                await verify_btn.click()
                await sub_page.wait_for_timeout(600)

                get_link_btn = sub_page.locator("#btn-text")
                await get_link_btn.wait_for(state="visible", timeout=8000)

                player_page = None

                for _ in range(4):
                    try:
                        await get_link_btn.click(timeout=1500)
                    except Exception:
                        pass

                    await sub_page.wait_for_timeout(600)

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
                await asyncio.sleep(0.8)

                for _attempt in range(3):
                    if current_stream_urls:
                        break
                    try:
                        await player_page.mouse.click(640, 360)
                        for frame in player_page.frames:
                            try:
                                await frame.click("video, .jw-display-icon-container, svg, .play-button, body", timeout=1000)
                            except Exception:
                                pass
                        await player_page.locator("video, .jw-display-icon-container, svg, iframe").first.click(timeout=1000)
                    except Exception:
                        pass
                    await asyncio.sleep(0.5)

                for _ in range(16):
                    if current_stream_urls:
                        break
                    await asyncio.sleep(0.4)

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
def site_search_match_score(movie_name, result):
    href = result.get("href", "")
    text = result.get("text", "")
    page_name, _ = resolve_movie_identity(text, [])
    slug = urllib.parse.unquote(urllib.parse.urlparse(href).path).strip("/").rsplit("/", 1)[-1]
    return max(title_similarity(movie_name, page_name), title_similarity(movie_name, slug))


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

            ranked_results = sorted(
                ((site_search_match_score(movie_name, result), result["href"]) for result in search_results),
                reverse=True,
            )
            if ranked_results and ranked_results[0][0] >= 0.72:
                score, found_url = ranked_results[0]
                print(f"   ✅ Search match found ({score:.2f}): {found_url}", flush=True)
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

                ranked_links = sorted(
                    ((site_search_match_score(movie_name, result), result["href"]) for result in cat_links),
                    reverse=True,
                )
                if ranked_links and ranked_links[0][0] >= 0.72:
                    score, found_url = ranked_links[0]
                    print(f"   ✅ Category page match ({score:.2f}): {found_url}", flush=True)
            except Exception as e:
                print(f"   ⚠️ Category scan failed: {e}", flush=True)

    finally:
        await context.close()

    return found_url

# ==============================================================================
# 🛠️ Dead Link Repair Pipeline (পুরানো মুভির Dead Link চেক ও রিপেয়ার)
# ==============================================================================
REPAIR_CHECK_CONCURRENCY = 15


def reconcile_repair_links(original_res_list, dead_indices, validated_fresh):
    """Replace dead entries deterministically and never report success with dead leftovers."""
    dead_indices = set(dead_indices)
    new_res_list = []
    seen_links = set()

    for index, item in enumerate(original_res_list):
        link = item.get("link", "")
        if index not in dead_indices and link and link not in seen_links:
            new_res_list.append(dict(item))
            seen_links.add(link)

    fresh_candidates = []
    for fresh in validated_fresh:
        fresh_item = dict(fresh)
        detected = parse_link_metadata(
            fresh_item.get("link", ""), fresh_item.get("resolution", "HD 1080P")
        )
        for key in ("season", "episode"):
            if fresh_item.get(key, "N/A") == "N/A" and detected[key] != "N/A":
                fresh_item[key] = detected[key]
        link = fresh_item.get("link", "")
        if link and link not in seen_links and all(link != item.get("link") for item in fresh_candidates):
            fresh_candidates.append(fresh_item)

    def candidate_score(old_item, fresh_item):
        score = 0
        for key in ("season", "episode"):
            old_value = old_item.get(key, "N/A")
            fresh_value = fresh_item.get(key, "N/A")
            if old_value != "N/A" and fresh_value == old_value:
                score += 4
            elif old_value != "N/A" and fresh_value != "N/A" and fresh_value != old_value:
                score -= 6
        if fresh_item.get("resolution", "HD 1080P") == old_item.get("resolution", "HD 1080P"):
            score += 2
        return score

    replaced_count = 0
    for index in sorted(dead_indices):
        old_item = original_res_list[index]
        if not fresh_candidates:
            continue
        compatible_indexes = []
        for candidate_index, fresh_item in enumerate(fresh_candidates):
            metadata_conflict = any(
                old_item.get(key, "N/A") != "N/A"
                and fresh_item.get(key, "N/A") != "N/A"
                and old_item.get(key) != fresh_item.get(key)
                for key in ("season", "episode")
            )
            if not metadata_conflict:
                compatible_indexes.append(candidate_index)
        if not compatible_indexes:
            continue
        best_index = max(
            compatible_indexes,
            key=lambda candidate_index: candidate_score(old_item, fresh_candidates[candidate_index]),
        )
        fresh_item = fresh_candidates.pop(best_index)
        replacement = {
            "resolution": fresh_item.get("resolution", old_item.get("resolution", "HD 1080P")),
            "link": fresh_item["link"],
        }
        for key in ("season", "episode"):
            value = fresh_item.get(key, "N/A")
            if value == "N/A":
                value = old_item.get(key, "N/A")
            if value != "N/A":
                replacement[key] = value
        new_res_list.append(replacement)
        seen_links.add(replacement["link"])
        replaced_count += 1

    for fresh_item in fresh_candidates:
        link = fresh_item.get("link", "")
        if link and link not in seen_links:
            new_res_list.append(fresh_item)
            seen_links.add(link)

    removed_count = len(dead_indices) - replaced_count
    return new_res_list, replaced_count, removed_count


def canonical_fresh_res_list(validated_fresh):
    """Return the current source page's validated stream set, in button order."""
    canonical = []
    seen_links = set()
    for item in validated_fresh:
        fresh_item = dict(item)
        link = fresh_item.get("link", "")
        if not link or link in seen_links:
            continue
        detected = parse_link_metadata(
            link,
            fresh_item.get("resolution", "HD 1080P"),
        )
        for key in ("season", "episode"):
            if fresh_item.get(key, "N/A") == "N/A" and detected[key] != "N/A":
                fresh_item[key] = detected[key]
        canonical.append(fresh_item)
        seen_links.add(link)
    return canonical

async def repair_dead_links(
    target_category_name,
    config,
    *,
    respect_cooldown=True,
    max_repair_minutes=MAX_REPAIR_MINUTES,
    target_source_urls=None,
    force_refresh_source_urls=None,
    expected_source_counts=None,
):
    """Category-র existing movies-এ dead link চেক করে fresh link দিয়ে replace করে।"""
    json_filename = config["json"]
    output_filename = config["file"]
    m3u_filename = config["m3u"]
    cat_dir = config["dir"]
    cat_slug = config["slug"]

    os.makedirs(cat_dir, exist_ok=True)

    # ১. Existing movies লোড করা
    existing_movies = load_existing_movies(config)

    if not existing_movies:
        print(f"ℹ️ No existing movies found for [{target_category_name}]. Nothing to repair.", flush=True)
        return

    print(f"\n{'='*60}", flush=True)
    print(f"REPAIR MODE: Checking [{target_category_name}] - {len(existing_movies)} movie(s)", flush=True)
    print(f"{'='*60}\n", flush=True)

    # ২. Failed repairs tracker লোড
    failed_repairs = load_failed_repairs()

    # ৩. Dead link detection — সব movie ও link একসাথে parallel চেক (⚡ Fast)
    movies_with_dead_links = []

    # পুরো category check করি; আগের first-20 logic পরের movie-গুলোকে অনন্তকাল বাদ দিত।
    target_source_urls = {
        normalize_source_url(url) for url in (target_source_urls or []) if normalize_source_url(url)
    }
    force_refresh_source_urls = {
        normalize_source_url(url)
        for url in (force_refresh_source_urls or [])
        if normalize_source_url(url)
    }
    expected_source_counts = {
        normalize_source_url(url): int(count)
        for url, count in (expected_source_counts or {}).items()
        if normalize_source_url(url) and int(count) > 0
    }
    candidates = []
    for movie in existing_movies:
        movie_name = movie.get("name", "Unknown")
        movie_category = movie.get("category", target_category_name)
        if target_source_urls and normalize_source_url(movie.get("source_url", "")) not in target_source_urls:
            continue
        if respect_cooldown and should_skip_repair(movie_name, movie_category, failed_repairs):
            print(f"⏭️ Skipping '{movie_name}' (failed repair < 24h ago)", flush=True)
            continue
        res_list = movie.get("res_list", [])
        if not res_list:
            continue
        candidates.append(movie)

    # ⚡ সব link একসাথে parallel check
    sem = asyncio.Semaphore(REPAIR_CHECK_CONCURRENCY)

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
        force_refresh = normalize_source_url(movie.get("source_url", "")) in force_refresh_source_urls
        if dead_indices or force_refresh:
            movies_with_dead_links.append({
                "movie": movie,
                "dead_indices": dead_indices,
                "dead_links": dead_links,
                "force_refresh": force_refresh,
            })

    if not movies_with_dead_links:
        print(f"\n✅ All checked links are alive! No repairs needed for [{target_category_name}].", flush=True)
        return

    print(
        f"\n🔧 Found {len(movies_with_dead_links)} movie(s) requiring repair/refresh. Starting...\n",
        flush=True,
    )

    # ৪. Repair — Playwright দিয়ে fresh link scrape
    repaired_count = 0
    failed_count = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=LAUNCH_ARGS)

        try:
            repair_sem = asyncio.Semaphore(2 if RUN_ENV == "github" else 3)

            async def repair_one(repair_info, repair_index):
                async with repair_sem:
                    if is_time_running_out(max_repair_minutes):
                        print("⏰ Repair time budget reached; skipping remaining repair work safely.", flush=True)
                        return 0, 0

                    movie = repair_info["movie"]
                    dead_indices = repair_info["dead_indices"]
                    force_refresh = repair_info["force_refresh"]
                    movie_name = movie.get("name", "Unknown")
                    movie_category = movie.get("category", target_category_name)
                    print(f"\n🔧 Repairing [{repair_index}/{len(movies_with_dead_links)}]: '{movie_name}'", flush=True)

                    history_filename = os.path.join(cat_dir, "history.txt")
                    history_urls = load_history_urls(history_filename)
                    original_page_url = find_source_url_for_movie(movie, history_urls)
                    repair_page_url = None

                    if original_page_url:
                        print(f"   📄 Found history URL: {original_page_url}", flush=True)
                        page_alive = await asyncio.to_thread(is_page_url_alive_sync, original_page_url)
                        if page_alive:
                            repair_page_url = original_page_url
                            print("   ✅ Original page is alive!", flush=True)
                        else:
                            print("   ❌ Original page is DEAD (404). Searching for new page...", flush=True)

                    if not repair_page_url:
                        repair_page_url = await search_movie_on_site(browser, movie_name, cat_slug)
                        if not repair_page_url:
                            print(f"   ❌ Could not find '{movie_name}' on site. Logging as failed.", flush=True)
                            record_failed_repair(
                                movie_name,
                                movie_category,
                                repair_info["dead_links"],
                                failed_repairs,
                            )
                            return 0, 1

                    current_source_url = normalize_source_url(movie.get("source_url", ""))
                    expected_count = expected_source_counts.get(current_source_url, 0)
                    validated_fresh = []
                    scrape_attempts = 2 if expected_count else 1
                    for scrape_attempt in range(1, scrape_attempts + 1):
                        if scrape_attempt > 1:
                            print(
                                f"   🔁 Missing-button retry {scrape_attempt}/{scrape_attempts} "
                                f"for '{movie_name}'...",
                                flush=True,
                            )
                        print(f"   🎬 Re-scraping from: {repair_page_url}", flush=True)
                        _, _, _, fresh_res_list, _ = await process_movie_parallel_pipeline(
                            browser,
                            repair_page_url,
                            repair_index,
                            target_category_name,
                        )
                        if fresh_res_list:
                            fresh_checks = await asyncio.gather(*[
                                asyncio.to_thread(is_stream_link_dead_sync, item.get("link", ""))
                                for item in fresh_res_list
                            ])
                            for fresh_item, fresh_dead in zip(fresh_res_list, fresh_checks):
                                fresh_link = fresh_item.get("link", "")
                                if not fresh_dead:
                                    validated_fresh.append(fresh_item)
                                    print(f"   ✅ Validated fresh link: {fresh_link[:70]}...", flush=True)
                                else:
                                    print(f"   ⚠️ Fresh link ALSO dead (skipping): {fresh_link[:70]}...", flush=True)

                        canonical_fresh = canonical_fresh_res_list(validated_fresh)
                        if not expected_count or len(canonical_fresh) >= expected_count:
                            break

                    if not validated_fresh:
                        print(f"   ❌ No fresh stream links found for '{movie_name}'. Logging as failed.", flush=True)
                        record_failed_repair(
                            movie_name,
                            movie_category,
                            repair_info["dead_links"],
                            failed_repairs,
                        )
                        return 0, 1

                    canonical_fresh = canonical_fresh_res_list(validated_fresh)
                    if expected_count and len(canonical_fresh) >= expected_count:
                        # All currently advertised target buttons were captured and
                        # validated, so the source page can safely be authoritative.
                        new_res_list = canonical_fresh
                        replaced_links = min(len(dead_indices), len(new_res_list))
                        removed_dead_links = max(0, len(dead_indices) - replaced_links)
                    else:
                        # A click can fail transiently even when its button exists.
                        # Preserve old playable entries and merge any fresh successes.
                        new_res_list, replaced_links, removed_dead_links = reconcile_repair_links(
                            movie.get("res_list", []),
                            dead_indices,
                            canonical_fresh,
                        )
                        if expected_count:
                            print(
                                f"   ⚠️ Captured {len(canonical_fresh)}/{expected_count} advertised "
                                "options; using non-destructive merge.",
                                flush=True,
                            )
                    if not new_res_list:
                        print(f"   ❌ No usable stream remains for '{movie_name}'. Logging as failed.", flush=True)
                        record_failed_repair(
                            movie_name,
                            movie_category,
                            repair_info["dead_links"],
                            failed_repairs,
                        )
                        return 0, 1

                    movie["res_list"] = new_res_list
                    movie["source_url"] = normalize_source_url(repair_page_url)
                    clear_failed_repair(movie_name, movie_category, failed_repairs)
                    print(
                        f"   ✅ Successfully repaired '{movie_name}' "
                        f"(replaced={replaced_links}, removed_dead={removed_dead_links}, "
                        f"source_refresh={force_refresh}, current_links={len(new_res_list)})!",
                        flush=True,
                    )
                    return 1, 0

            repair_results = await asyncio.gather(*[
                repair_one(repair_info, index)
                for index, repair_info in enumerate(movies_with_dead_links, 1)
            ])
            repaired_count = sum(result[0] for result in repair_results)
            failed_count = sum(result[1] for result in repair_results)

        finally:
            await browser.close()

    # ৫. Failed repairs সেভ
    save_failed_repairs(failed_repairs)

    # ৬. Updated files সেভ (TXT, JSON, M3U)
    if repaired_count > 0:
        save_category_outputs(target_category_name, config, existing_movies)
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
    elif SCAN_MODE in {"REPAIR_AUTO", "REPAIR_SPECIFIC"}:
        print("Legacy main-scanner repair mode is disabled. Use Stream Guardian.", flush=True)
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
    skipped_history_filename = os.path.join(cat_dir, "history_skipped.txt")

    os.makedirs(cat_dir, exist_ok=True)

    # 📝 ১. আগের মুভি ফাইল এবং হিস্টরি নিখুঁতভাবে লোড করা
    existing_movies = load_existing_movies(config)
    for movie in existing_movies:
        resolved_name, resolved_year = resolve_movie_identity(movie.get("name", ""), movie.get("res_list", []))
        movie["name"] = resolved_name
        if resolved_year != "N/A":
            movie["year"] = resolved_year
    existing_by_identity = {
        movie_identity_key(movie["name"], movie.get("year"), is_series_movie(movie)): movie
        for movie in existing_movies
    }
    existing_identities = set(existing_by_identity)
    print(f"📂 Loaded {len(existing_movies)} existing movie(s) from previous scans.", flush=True)

    scraped_history = set(load_history_urls(history_filename))
    scraped_history.update(load_history_urls(skipped_history_filename))
    candidate_retry_state = load_candidate_retry_state()
    retry_entries = candidate_retry_state.setdefault("categories", {}).setdefault(target_category_name, {})
    retry_state_changed = False
    for completed_url in list(retry_entries):
        if normalize_source_url(completed_url) in scraped_history:
            retry_entries.pop(completed_url, None)
            retry_state_changed = True

    # GitHub runner-এ CPU/RAM কম → বেশি browser = ধীর
    # ২টা browser একসাথে = repair-এর মতো fast
    sem = asyncio.Semaphore(2 if RUN_ENV == "github" else 3)

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

            discovered_movie_urls = []
            current_page_num = 1
            MAX_PAGE_SAFETY_LIMIT = 5

            # 🎯 ২. হিস্টরিতে না থাকা একদম নতুন ১০টি মুভি খুঁজে বের করা
            while len(discovered_movie_urls) < CANDIDATE_DISCOVERY_LIMIT and current_page_num <= MAX_PAGE_SAFETY_LIMIT:
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
                            normalized_link = normalize_source_url(link_clean)
                            if normalized_link not in scraped_history and normalized_link not in discovered_movie_urls:
                                discovered_movie_urls.append(normalized_link)
                                if len(discovered_movie_urls) >= CANDIDATE_DISCOVERY_LIMIT:
                                    break

                if len(discovered_movie_urls) >= CANDIDATE_DISCOVERY_LIMIT:
                    break

                current_page_num += 1
                if current_page_num <= MAX_PAGE_SAFETY_LIMIT:
                    try:
                        next_page_url = f"{MAIN_SITE_URL}{cat_slug}/page/{current_page_num}/"
                        await page_main.goto(next_page_url, timeout=25000, wait_until="domcontentloaded")
                    except Exception:
                        break

            new_movie_urls, cooling_candidate_count = select_scan_candidates(
                discovered_movie_urls,
                target_category_name,
                candidate_retry_state,
            )
            if cooling_candidate_count:
                print(
                    f"⏳ Deferred {cooling_candidate_count} failed candidate(s); scanning fresh/deeper posts instead.",
                    flush=True,
                )

            await page_main.close()

            # 🎯 ৩. নতুন মুভিগুলো পাওয়ার পর সেগুলোকে স্ক্র্যাপ করা
            new_movies_list = []
            if new_movie_urls:
                # GitHub-এ time limit হলে কম movie process করো
                parallel_results = []
                if is_time_running_out():
                    print(f"\n⏰ Time limit approaching! Skipping extraction to save progress.", flush=True)
                    new_movie_urls = []
                else:
                    print(f"🚀 Found {len(new_movie_urls)} new unique movie(s). Starting extraction...\n", flush=True)
                    tasks = [safe_process(browser, movie_url, idx, target_category_name) for idx, movie_url in enumerate(new_movie_urls, 1)]
                    parallel_results = await asyncio.gather(*tasks)

                for movie_url, title, categories, res_list, web_poster in parallel_results:
                    retry_state_changed = record_candidate_outcome(
                        target_category_name,
                        movie_url,
                        bool(res_list),
                        candidate_retry_state,
                    ) or retry_state_changed
                    if res_list:
                        clean_name, year = resolve_movie_identity(title, res_list)

                        parsed_res_list = []
                        for item in res_list:
                            meta = parse_link_metadata(item['link'], item['resolution'])
                            parsed_res_list.append(meta)

                        identity = movie_identity_key(
                            clean_name,
                            year,
                            any(item.get("season", "N/A") != "N/A" for item in parsed_res_list),
                        )
                        if identity not in existing_identities:
                            poster_url = web_poster if web_poster != "N/A" else "N/A"
                            if poster_url == "N/A":
                                poster_url = await fetch_tmdb_poster(clean_name, year)

                            new_movie = {
                                "name": clean_name,
                                "category": categories,
                                "year": year,
                                "poster": poster_url,
                                "source_url": normalize_source_url(movie_url),
                                "res_list": parsed_res_list
                            }
                            new_movies_list.append(new_movie)
                            existing_by_identity[identity] = new_movie
                            existing_identities.add(identity)
                        else:
                            # একই title/year-এর আলাদা source page-এ নতুন quality/episode থাকতে পারে।
                            # Record-টি writer-এ পাঠালে canonical merge হবে এবং alias URL সংরক্ষিত থাকবে।
                            target_movie = existing_by_identity[identity]
                            new_movies_list.append({
                                "name": clean_name,
                                "category": categories,
                                "year": year,
                                "poster": web_poster if web_poster != "N/A" else target_movie.get("poster", "N/A"),
                                "source_url": normalize_source_url(movie_url),
                                "res_list": parsed_res_list,
                            })
                            print(f"INFO: Merging duplicate movie identity: '{clean_name}' ({year})", flush=True)
            else:
                print("ℹ️ No new movies found to scrape on category pages.", flush=True)

            # 🎯 ৪. পুরানো + নতুন সব মুভি একত্রিত করা (নো-ডিলিট লজিক)
            all_movies = existing_movies + new_movies_list

            # 🎯 ৫. [POSTER & YEAR AUTO-REPAIR] পুরানো মুভির Year লিঙ্ক থেকে রিকভার ও মিসিং পোস্টার TMDB ব্যাকফিল
            for m in all_movies:
                if not m.get("year") or m["year"] == "N/A":
                    _, stream_year = extract_stream_identity(m.get("res_list", []))
                    if stream_year != "N/A":
                        m["year"] = stream_year

                if not m.get("poster") or m["poster"] in ["N/A", "", "None"] or "cineimg.xyz" in m["poster"]:
                    repaired_poster = await fetch_tmdb_poster(m["name"], m["year"])
                    if repaired_poster != "N/A":
                        m["poster"] = repaired_poster

            total_items_count = save_category_outputs(target_category_name, config, all_movies)
            if retry_state_changed:
                save_candidate_retry_state(candidate_retry_state)
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
