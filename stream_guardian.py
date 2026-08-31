"""Independent, stateful health monitor and targeted repair service for stream links."""

import argparse
import asyncio
import html
import json
import os
import re
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import main_scraper as scraper


ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "history" / "link_guardian_state.json"
QUARANTINE_FILE = ROOT / "history" / "link_guardian_quarantine.json"
DEFAULT_REPORT_FILE = ROOT / "guardian-report.json"
PROBE_CONCURRENCY = 30
SOURCE_AUDIT_CONCURRENCY = 30
TRANSIENT_FAILURE_THRESHOLD = 2
OUTAGE_CIRCUIT_BREAKER_RATIO = 0.10
QUARANTINE_RETRY_LIMIT = 1
SOURCE_GAP_RETRY_HOURS = (6, 24, 72, 168)
DEAD_LINK_BATCH_SIZE = 15
REFRESH_SOURCE_BATCH_SIZE = 3


@dataclass(frozen=True)
class ProbeResult:
    url: str
    status: str
    reason: str
    http_status: int = 0


def utc_now():
    return datetime.now(timezone.utc)


def utc_iso(value=None):
    return (value or utc_now()).isoformat()


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def atomic_write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary_path, path)


def load_category_movies(config):
    payload = load_json(ROOT / config["json"], {})
    return payload.get("movies", []) if isinstance(payload, dict) else []


def probe_stream_once(url):
    result = scraper.probe_stream_link_sync(url, timeout=8)
    return ProbeResult(
        url,
        result["status"],
        result["reason"],
        result.get("http_status", 0),
    )


def probe_stream(url, transient_retries=2):
    result = probe_stream_once(url)
    for _ in range(transient_retries):
        if result.status != "transient":
            break
        result = probe_stream_once(url)
    return result


def probe_many(urls, concurrency=PROBE_CONCURRENCY):
    unique_urls = list(dict.fromkeys(url for url in urls if url))
    results = {}
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        pending = {executor.submit(probe_stream, url): url for url in unique_urls}
        for future in as_completed(pending):
            url = pending[future]
            try:
                results[url] = future.result()
            except Exception as error:
                results[url] = ProbeResult(url, "transient", type(error).__name__.lower())
    return results


def target_option_urls_from_html(raw_html):
    """Extract the same Watch options targeted by the unchanged browser pipeline."""
    raw_html = str(raw_html or "")
    lowered_html = raw_html.lower()
    if re.search(r'''class=["'][^"']*\bep-card\b''', raw_html, re.IGNORECASE):
        urls = []
        for anchor_match in re.finditer(
            r"<a\b([^>]*)>(.*?)</a>",
            raw_html,
            re.IGNORECASE | re.DOTALL,
        ):
            # Series extractor selects anchors inside .watch-links and rejects
            # 720p/480p buttons. The latest preceding box class identifies which
            # quality box owns the anchor in the server-rendered markup.
            watch_position = lowered_html.rfind("watch-links", 0, anchor_match.start())
            download_position = lowered_html.rfind("download-links", 0, anchor_match.start())
            if watch_position <= download_position:
                continue
            button_text = html.unescape(re.sub(r"<[^>]+>", " ", anchor_match.group(2)))
            button_text = " ".join(button_text.split()).lower()
            if "download" in button_text:
                continue
            if any(marker in button_text for marker in ("720p", "480p")) and "1080p" not in button_text:
                continue
            href_match = re.search(r'''href=["']([^"']+)''', anchor_match.group(1), re.IGNORECASE)
            if href_match:
                href = html.unescape(href_match.group(1)).strip()
                if "generate.php" in href and href not in urls:
                    urls.append(href)
        return urls

    header_matches = list(
        re.finditer(r"<h[1-5]\b[^>]*>(.*?)</h[1-5]>", raw_html, re.IGNORECASE | re.DOTALL)
    )
    urls = []
    for index, header_match in enumerate(header_matches):
        header_text = html.unescape(re.sub(r"<[^>]+>", " ", header_match.group(1)))
        header_text = " ".join(header_text.split()).lower()
        if not any(marker in header_text for marker in ("1080p", "2160p", "4k", "hevc")):
            continue
        if any(marker in header_text for marker in ("720p", "480p")) and "1080p" not in header_text:
            continue

        end = (
            header_matches[index + 1].start()
            if index + 1 < len(header_matches)
            else min(len(raw_html), header_match.end() + 5000)
        )
        block = raw_html[header_match.end():end]
        watch_match = re.search(
            r"<a\b([^>]*\bdlbtn-watch\b[^>]*)>",
            block,
            re.IGNORECASE | re.DOTALL,
        )
        if not watch_match:
            continue
        href_match = re.search(r'''href=["']([^"']+)''', watch_match.group(1), re.IGNORECASE)
        if href_match:
            href = html.unescape(href_match.group(1)).strip()
            if href and href not in urls:
                urls.append(href)
    return urls


def source_target_option_count(source_url):
    request = urllib.request.Request(
        source_url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw_html = response.read().decode("utf-8", "replace")
        return len(target_option_urls_from_html(raw_html)), "ok"
    except urllib.error.HTTPError as error:
        return None, f"http_{error.code}"
    except Exception as error:
        return None, type(error).__name__.lower()


def audit_source_completeness(catalog):
    """Find non-series records whose live source page exposes more target buttons."""
    owners = {}
    for category_name, category_data in catalog.items():
        for movie in category_data["movies"]:
            source_url = scraper.normalize_source_url(movie.get("source_url", ""))
            if not source_url:
                continue
            stored_count = len({item.get("link", "") for item in movie.get("res_list", []) if item.get("link")})
            owners.setdefault(source_url, []).append((category_name, stored_count))

    results = {}
    with ThreadPoolExecutor(max_workers=SOURCE_AUDIT_CONCURRENCY) as executor:
        pending = {executor.submit(source_target_option_count, url): url for url in owners}
        for future in as_completed(pending):
            url = pending[future]
            try:
                results[url] = future.result()
            except Exception as error:
                results[url] = (None, type(error).__name__.lower())

    incomplete_sources = {}
    observed_source_counts = {}
    incomplete_movies = 0
    for source_url, source_owners in owners.items():
        source_count, _ = results[source_url]
        if source_count is None:
            continue
        for category_name, stored_count in source_owners:
            observed_source_counts.setdefault(category_name, {})[source_url] = source_count
            if source_count > stored_count:
                incomplete_sources.setdefault(category_name, {})[source_url] = source_count
                incomplete_movies += 1

    stats = {
        "source_pages_checked": sum(count is not None for count, _ in results.values()),
        "source_pages_failed": sum(count is None for count, _ in results.values()),
        "source_incomplete_movies": incomplete_movies,
    }
    return incomplete_sources, observed_source_counts, stats


def source_gap_key(category_name, source_url):
    return f"{scraper.normalize_title_key(category_name)}::{scraper.normalize_source_url(source_url)}"


def catalog_source_counts(catalog):
    counts = {}
    for category_name, category_data in catalog.items():
        for movie in category_data["movies"]:
            source_url = scraper.normalize_source_url(movie.get("source_url", ""))
            if not source_url:
                continue
            counts[(category_name, source_url)] = len({
                item.get("link", "")
                for item in movie.get("res_list", [])
                if item.get("link")
            })
    return counts


def source_gap_retry_due(entry, advertised_count, stored_count, now=None):
    """Retry immediately when the page/catalogue changed; otherwise honor backoff."""
    if not isinstance(entry, dict):
        return True
    if int(entry.get("advertised_count", -1)) != int(advertised_count):
        return True
    if int(entry.get("stored_count", -1)) != int(stored_count):
        return True
    try:
        return datetime.fromisoformat(entry.get("next_retry_at", "")) <= (now or utc_now())
    except (TypeError, ValueError):
        return True


def due_source_gaps(incomplete_sources, catalog, previous_gaps, now=None):
    stored_counts = catalog_source_counts(catalog)
    due = {}
    deferred = 0
    for category_name, source_counts in incomplete_sources.items():
        for source_url, advertised_count in source_counts.items():
            stored_count = stored_counts.get((category_name, source_url), 0)
            entry = previous_gaps.get(source_gap_key(category_name, source_url), {})
            if source_gap_retry_due(entry, advertised_count, stored_count, now=now):
                due.setdefault(category_name, {})[source_url] = advertised_count
            else:
                deferred += 1
    return due, deferred


def schedule_source_gap(category_name, source_url, advertised_count, stored_count, previous=None, now=None):
    previous = previous if isinstance(previous, dict) else {}
    same_observation = (
        int(previous.get("advertised_count", -1)) == int(advertised_count)
        and int(previous.get("stored_count", -1)) == int(stored_count)
    )
    attempt_count = int(previous.get("attempt_count", 0)) + 1 if same_observation else 1
    delay_hours = SOURCE_GAP_RETRY_HOURS[min(attempt_count - 1, len(SOURCE_GAP_RETRY_HOURS) - 1)]
    current_time = now or utc_now()
    return {
        "category": category_name,
        "source_url": scraper.normalize_source_url(source_url),
        "advertised_count": int(advertised_count),
        "stored_count": int(stored_count),
        "attempt_count": attempt_count,
        "last_attempt": utc_iso(current_time),
        "next_retry_at": utc_iso(current_time + timedelta(hours=delay_hours)),
        "last_outcome": "no_additional_distinct_media",
    }


def merge_repair_queue(
    previous_queue,
    category_names,
    catalog,
    owners,
    probes,
    affected_dead_links,
    incomplete_sources,
    due_incomplete_sources,
    now=None,
):
    """Merge newly discovered work with unfinished work without losing targets."""
    current_time = now or utc_now()
    selected_categories = set(category_names)
    current_sources = {
        source_gap_key(category_name, source_url)
        for (category_name, source_url) in catalog_source_counts(catalog)
    }
    source_links = {}
    for link, link_owners in owners.items():
        for owner in link_owners:
            key = source_gap_key(owner["category"], owner["source_url"])
            source_links.setdefault(key, set()).add(link)

    queue = {}
    for key, entry in (previous_queue or {}).items():
        if not isinstance(entry, dict):
            continue
        category_name = entry.get("category", "")
        if category_name not in selected_categories:
            queue[key] = entry
            continue
        if key not in current_sources:
            continue
        if entry.get("kind") == "dead":
            pending_links = [
                link
                for link in entry.get("dead_links", [])
                if link in source_links.get(key, set())
                and probes.get(link, ProbeResult(link, "transient", "not_checked")).status != "alive"
            ]
            if pending_links:
                queue[key] = {**entry, "dead_links": pending_links}
        elif entry.get("kind") == "refresh":
            source_url = scraper.normalize_source_url(entry.get("source_url", ""))
            if source_url in incomplete_sources.get(category_name, {}):
                queue[key] = entry

    def add_or_upgrade(category_name, source_url, kind, expected_count=0, dead_links=None):
        normalized_url = scraper.normalize_source_url(source_url)
        key = source_gap_key(category_name, normalized_url)
        old_entry = queue.get(key, {})
        effective_kind = "dead" if kind == "dead" or old_entry.get("kind") == "dead" else "refresh"
        combined_dead_links = list(dict.fromkeys([
            *old_entry.get("dead_links", []),
            *(dead_links or []),
        ]))
        queue[key] = {
            "category": category_name,
            "source_url": normalized_url,
            "kind": effective_kind,
            "expected_count": int(expected_count or old_entry.get("expected_count", 0)),
            "dead_links": combined_dead_links if effective_kind == "dead" else [],
            "discovered_at": old_entry.get("discovered_at", utc_iso(current_time)),
            "last_seen": utc_iso(current_time),
            "attempt_count": int(old_entry.get("attempt_count", 0)),
            "last_attempt": old_entry.get("last_attempt", ""),
        }

    for category_name, source_links_by_url in affected_dead_links.items():
        for source_url, dead_links in source_links_by_url.items():
            add_or_upgrade(category_name, source_url, "dead", dead_links=sorted(dead_links))
    for category_name, source_counts in due_incomplete_sources.items():
        for source_url, expected_count in source_counts.items():
            add_or_upgrade(category_name, source_url, "refresh", expected_count)
    return queue


def select_repair_batch(
    queue,
    dead_link_limit=DEAD_LINK_BATCH_SIZE,
    refresh_limit=REFRESH_SOURCE_BATCH_SIZE,
    category_names=None,
):
    selected_categories = set(category_names or [])

    def sort_key(item):
        key, entry = item
        attempted = int(entry.get("attempt_count", 0)) > 0
        return (
            0 if entry.get("kind") == "dead" else 1,
            1 if attempted else 0,
            entry.get("last_attempt") or entry.get("discovered_at", ""),
            key,
        )

    ordered = sorted(
        (
            (key, entry)
            for key, entry in queue.items()
            if not selected_categories or entry.get("category") in selected_categories
        ),
        key=sort_key,
    )
    selected = {}
    remaining_dead_budget = max(0, int(dead_link_limit))
    for key, entry in ordered:
        if entry.get("kind") != "dead" or remaining_dead_budget <= 0:
            continue
        links = list(dict.fromkeys(entry.get("dead_links", [])))
        if not links:
            continue
        selected[key] = {**entry, "dead_links": links[:remaining_dead_budget]}
        remaining_dead_budget -= len(selected[key]["dead_links"])

    # Missing-button refreshes are lower priority and run only when no dead
    # link is waiting, keeping the repair workload small and predictable.
    if selected:
        return selected
    for key, entry in ordered:
        if entry.get("kind") == "refresh":
            selected[key] = entry
            if len(selected) >= max(0, int(refresh_limit)):
                break
    return selected


def selected_category_names(requested):
    if not requested or requested.lower() == "all":
        return list(scraper.CATEGORIES_LIST)
    for category_name in scraper.CATEGORIES_LIST:
        if category_name.lower() == requested.lower():
            return [category_name]
    raise ValueError(f"Unknown category: {requested}")


def build_catalog(category_names):
    catalog = {}
    owners = {}
    for category_name in category_names:
        config = scraper.CATEGORIES_MAP[category_name]
        movies = load_category_movies(config)
        catalog[category_name] = {"config": config, "movies": movies}
        for movie in movies:
            source_url = scraper.normalize_source_url(movie.get("source_url", ""))
            for item in movie.get("res_list", []):
                link = item.get("link", "")
                if link:
                    owners.setdefault(link, []).append({
                        "category": category_name,
                        "source_url": source_url,
                        "movie_name": movie.get("name", "Unknown"),
                    })
    return catalog, owners


def classify_probes(probes, previous_state, threshold=TRANSIENT_FAILURE_THRESHOLD):
    old_suspects = previous_state.get("suspects", {}) if isinstance(previous_state, dict) else {}
    suspects = dict(old_suspects)
    confirmed = {}

    for url, result in probes.items():
        if result.status == "alive":
            suspects.pop(url, None)
        elif result.status == "dead":
            confirmed[url] = result.reason
            suspects.pop(url, None)
        else:
            previous = old_suspects.get(url, {})
            if not isinstance(previous, dict):
                previous = {}
            failure_count = int(previous.get("failure_count", 0)) + 1
            suspects[url] = {
                "failure_count": failure_count,
                "last_reason": result.reason,
                "last_checked": utc_iso(),
            }
            if failure_count >= threshold:
                confirmed[url] = f"repeated_{result.reason}"

    return confirmed, suspects


def remove_history_url(path, url):
    normalized = scraper.normalize_source_url(url)
    remaining = [item for item in scraper.load_history_urls(path) if item != normalized]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("".join(f"{item}\n" for item in remaining), encoding="utf-8", newline="\n")


def quarantine_key(category_name, source_url):
    return f"{scraper.normalize_title_key(category_name)}::{scraper.normalize_source_url(source_url)}"


def quarantine_movie(quarantine, category_name, movie, dead_links):
    source_url = scraper.normalize_source_url(movie.get("source_url", ""))
    key = quarantine_key(category_name, source_url)
    old_entry = quarantine.get("entries", {}).get(key, {})
    attempt_count = int(old_entry.get("attempt_count", 0)) + 1
    quarantine.setdefault("entries", {})[key] = {
        "category": category_name,
        "source_url": source_url,
        "movie": movie,
        "dead_links": list(dict.fromkeys(dead_links)),
        "quarantined_at": old_entry.get("quarantined_at", utc_iso()),
        "last_attempt": utc_iso(),
        "next_retry_at": utc_iso(utc_now() + timedelta(hours=6)),
        "attempt_count": attempt_count,
    }


def retry_due(entry):
    value = entry.get("next_retry_at", "")
    if not value:
        return True
    try:
        return datetime.fromisoformat(value) <= utc_now()
    except ValueError:
        return True


def schedule_quarantine_retry(entry):
    attempts = int(entry.get("attempt_count", 0)) + 1
    delay_hours = min(72, 6 * (2 ** min(attempts - 1, 4)))
    entry["attempt_count"] = attempts
    entry["last_attempt"] = utc_iso()
    entry["next_retry_at"] = utc_iso(utc_now() + timedelta(hours=delay_hours))


async def restore_quarantined(quarantine, category_names, report):
    due_entries = [
        (key, entry)
        for key, entry in quarantine.get("entries", {}).items()
        if entry.get("category") in category_names and retry_due(entry)
    ][:QUARANTINE_RETRY_LIMIT]
    if not due_entries:
        return False

    changed = False
    async with scraper.async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=scraper.LAUNCH_ARGS)
        try:
            for key, entry in due_entries:
                category_name = entry["category"]
                source_url = entry["source_url"]
                repair_url = source_url
                if not await asyncio.to_thread(scraper.is_page_url_alive_sync, source_url):
                    old_movie = entry.get("movie", {})
                    repair_url = await scraper.search_movie_on_site(
                        browser,
                        old_movie.get("name", ""),
                        scraper.CATEGORIES_MAP[category_name]["slug"],
                    )
                    if not repair_url:
                        schedule_quarantine_retry(entry)
                        report["quarantine_retry_failed"] += 1
                        changed = True
                        continue

                result = await scraper.process_movie_parallel_pipeline(browser, repair_url, 0, category_name)
                _, title, categories, fresh_res_list, web_poster = result
                parsed_items = [
                    scraper.parse_link_metadata(item.get("link", ""), item.get("resolution", "HD 1080P"))
                    for item in fresh_res_list
                    if item.get("link")
                ]
                probes = await asyncio.to_thread(probe_many, [item["link"] for item in parsed_items])
                live_items = [item for item in parsed_items if probes[item["link"]].status == "alive"]
                if not live_items:
                    schedule_quarantine_retry(entry)
                    report["quarantine_retry_failed"] += 1
                    changed = True
                    continue

                config = scraper.CATEGORIES_MAP[category_name]
                movies = load_category_movies(config)
                old_movie = entry.get("movie", {})
                name, year = scraper.resolve_movie_identity(title or old_movie.get("name", ""), live_items)
                restored_movie = {
                    "name": name,
                    "category": categories or old_movie.get("category", category_name),
                    "year": year if year != "N/A" else old_movie.get("year", "N/A"),
                    "poster": web_poster if web_poster != "N/A" else old_movie.get("poster", "N/A"),
                    "source_url": scraper.normalize_source_url(repair_url),
                    "res_list": live_items,
                }
                movies.append(restored_movie)
                scraper.save_category_outputs(category_name, config, movies)
                canonical_sources = {
                    scraper.normalize_source_url(movie.get("source_url", ""))
                    for movie in load_category_movies(config)
                }
                if scraper.normalize_source_url(repair_url) in canonical_sources:
                    remove_history_url(Path(config["dir"]) / "history_skipped.txt", repair_url)
                quarantine["entries"].pop(key, None)
                report["restored_movies"] += 1
                changed = True
        finally:
            await browser.close()
    return changed


async def clean_after_targeted_repair(
    category_name,
    source_urls,
    quarantine,
    report,
    target_identities=None,
    target_dead_links=None,
):
    config = scraper.CATEGORIES_MAP[category_name]
    movies = load_category_movies(config)
    source_urls = {scraper.normalize_source_url(url) for url in source_urls}
    target_identities = set(target_identities or [])
    target_dead_links = {link for link in (target_dead_links or []) if link}

    def is_target_movie(movie):
        source_matches = scraper.normalize_source_url(movie.get("source_url", "")) in source_urls
        identity = scraper.movie_identity_key(
            movie.get("name", ""), movie.get("year"), scraper.is_series_movie(movie)
        )
        return source_matches or identity in target_identities

    target_movies = [
        movie for movie in movies if is_target_movie(movie)
    ]
    target_links = [
        item.get("link", "")
        for movie in target_movies
        for item in movie.get("res_list", [])
        if not target_dead_links or item.get("link", "") in target_dead_links
    ]
    probes = await asyncio.to_thread(probe_many, target_links)

    changed = False
    retained_movies = []
    for movie in movies:
        source_url = scraper.normalize_source_url(movie.get("source_url", ""))
        if not is_target_movie(movie):
            retained_movies.append(movie)
            continue

        dead_items = [
            item for item in movie.get("res_list", [])
            if (not target_dead_links or item.get("link", "") in target_dead_links)
            if probes.get(item.get("link", ""), ProbeResult("", "transient", "not_checked")).status == "dead"
        ]
        if not dead_items:
            retained_movies.append(movie)
            continue

        safe_items = [
            item for item in movie.get("res_list", [])
            if probes.get(item.get("link", ""), ProbeResult("", "transient", "not_checked")).status != "dead"
        ]
        if safe_items:
            # Confirmed dead বাদ দিই; transient quality-টি পরের run পর্যন্ত প্রকাশিত থাকে।
            movie["res_list"] = safe_items
            retained_movies.append(movie)
            report["dead_links_removed"] += len(dead_items)
            changed = True
        else:
            quarantine_movie(quarantine, category_name, movie, [item.get("link", "") for item in dead_items])
            scraper.append_unique_url(Path(config["dir"]) / "history_skipped.txt", source_url)
            report["quarantined_movies"] += 1
            report["dead_links_removed"] += len(dead_items)
            changed = True

    if changed:
        scraper.save_category_outputs(category_name, config, retained_movies)
    return changed


async def run_guardian(category_names, apply_changes, report_path):
    report = {
        "started_at": utc_iso(),
        "mode": "apply" if apply_changes else "dry_run",
        "categories": category_names,
        "movies": 0,
        "stream_entries": 0,
        "unique_streams": 0,
        "alive": 0,
        "transient": 0,
        "confirmed_dead": 0,
        "affected_movies": 0,
        "source_pages_checked": 0,
        "source_pages_failed": 0,
        "source_incomplete_movies": 0,
        "source_refresh_due": 0,
        "source_refresh_deferred": 0,
        "source_refresh_checked": 0,
        "source_refresh_no_change": 0,
        "catalogue_updates": 0,
        "repair_queue_before": 0,
        "repair_queue_added": 0,
        "dead_links_queued": 0,
        "repair_batch_selected": 0,
        "dead_links_batch_selected": 0,
        "repair_queue_remaining": 0,
        "dead_links_queue_remaining": 0,
        "repaired_categories": 0,
        "dead_links_removed": 0,
        "quarantined_movies": 0,
        "restored_movies": 0,
        "quarantine_retry_failed": 0,
        "circuit_breaker": False,
    }
    quarantine = load_json(QUARANTINE_FILE, {"version": 1, "entries": {}})
    quarantine_before = json.dumps(quarantine, sort_keys=True)

    catalog, owners = build_catalog(category_names)
    report["movies"] = sum(len(data["movies"]) for data in catalog.values())
    report["stream_entries"] = sum(
        len(movie.get("res_list", []))
        for data in catalog.values()
        for movie in data["movies"]
    )
    report["unique_streams"] = len(owners)
    probes = await asyncio.to_thread(probe_many, owners)
    report["alive"] = sum(result.status == "alive" for result in probes.values())
    report["transient"] = sum(result.status == "transient" for result in probes.values())
    report["probe_reasons"] = dict(sorted(Counter(result.reason for result in probes.values()).items()))

    previous_state = load_json(
        STATE_FILE,
        {"version": 3, "suspects": {}, "source_gaps": {}, "repair_queue": {}},
    )
    confirmed, suspects = classify_probes(probes, previous_state)
    for url, value in suspects.items():
        if url in owners:
            value["categories"] = sorted({owner["category"] for owner in owners[url]})
    report["confirmed_dead"] = len(confirmed)

    affected_sources = {}
    affected_dead_links = {}
    affected_movies = set()
    for url in confirmed:
        for owner in owners.get(url, []):
            affected_sources.setdefault(owner["category"], set()).add(owner["source_url"])
            affected_dead_links.setdefault(owner["category"], {}).setdefault(
                owner["source_url"], set()
            ).add(url)
            affected_movies.add((owner["category"], owner["source_url"]))
    report["affected_movies"] = len(affected_movies)

    incomplete_sources, observed_source_counts, source_audit_stats = await asyncio.to_thread(
        audit_source_completeness,
        catalog,
    )
    report.update(source_audit_stats)
    old_source_gaps = (
        previous_state.get("source_gaps", {}) if isinstance(previous_state, dict) else {}
    )
    if not isinstance(old_source_gaps, dict):
        old_source_gaps = {}
    due_incomplete_sources, deferred_source_gaps = due_source_gaps(
        incomplete_sources,
        catalog,
        old_source_gaps,
    )
    report["source_refresh_due"] = sum(len(values) for values in due_incomplete_sources.values())
    report["source_refresh_deferred"] = deferred_source_gaps
    old_repair_queue = (
        previous_state.get("repair_queue", {}) if isinstance(previous_state, dict) else {}
    )
    if not isinstance(old_repair_queue, dict):
        old_repair_queue = {}
    repair_queue = merge_repair_queue(
        old_repair_queue,
        category_names,
        catalog,
        owners,
        probes,
        affected_dead_links,
        incomplete_sources,
        due_incomplete_sources,
    )
    repair_batch = (
        select_repair_batch(repair_queue, category_names=category_names)
        if apply_changes
        else {}
    )
    report["repair_queue_before"] = len(old_repair_queue)
    report["repair_queue_added"] = len(set(repair_queue) - set(old_repair_queue))
    report["dead_links_queued"] = sum(
        len(entry.get("dead_links", []))
        for entry in repair_queue.values()
        if entry.get("kind") == "dead"
    )
    report["repair_batch_selected"] = len(repair_batch)
    report["dead_links_batch_selected"] = sum(
        len(entry.get("dead_links", []))
        for entry in repair_batch.values()
        if entry.get("kind") == "dead"
    )
    attempted_source_gaps = set()
    batch_outcomes = {}

    # Widespread timeout/5xx সাধারণত runner/CDN outage; confirmed 404 repair আটকাবে না।
    transient_ratio = report["transient"] / max(1, len(owners))
    if report["transient"] and transient_ratio >= OUTAGE_CIRCUIT_BREAKER_RATIO:
        report["circuit_breaker"] = True
        report["message"] = "Systemic transient failure threshold reached; no catalogue mutation was allowed."
        repair_batch = {}
        report["repair_batch_selected"] = 0
        report["dead_links_batch_selected"] = 0
    elif apply_changes:
        await restore_quarantined(quarantine, category_names, report)
        batch_by_category = {}
        for key, entry in repair_batch.items():
            batch_by_category.setdefault(entry["category"], {})[key] = entry
        repair_categories = set(batch_by_category)
        for category_name in category_names:
            if category_name not in repair_categories:
                continue
            category_batch = batch_by_category[category_name]
            target_dead_links = {
                entry["source_url"]: set(entry.get("dead_links", []))
                for entry in category_batch.values()
                if entry.get("kind") == "dead"
            }
            dead_source_urls = set(target_dead_links)
            refresh_source_counts = {
                entry["source_url"]: int(entry.get("expected_count", 0))
                for entry in category_batch.values()
                if entry.get("kind") == "refresh"
            }
            refresh_source_urls = set(refresh_source_counts)
            attempted_source_gaps.update(
                source_gap_key(category_name, source_url)
                for source_url in refresh_source_urls
            )
            source_urls = dead_source_urls | refresh_source_urls
            expected_source_counts = {
                source_url: count
                for source_url, count in observed_source_counts.get(category_name, {}).items()
                if source_url in refresh_source_urls
            }
            repair_summary = await scraper.repair_dead_links(
                category_name,
                scraper.CATEGORIES_MAP[category_name],
                respect_cooldown=False,
                max_repair_minutes=35,
                target_source_urls=source_urls,
                force_refresh_source_urls=refresh_source_urls,
                expected_source_counts=expected_source_counts,
                target_stream_urls=target_dead_links,
            )
            repair_summary = repair_summary if isinstance(repair_summary, dict) else {}
            for source_url, outcome in repair_summary.get("outcomes", {}).items():
                batch_outcomes[source_gap_key(category_name, source_url)] = outcome
            report["source_refresh_checked"] += len(refresh_source_urls)
            report["source_refresh_no_change"] += int(repair_summary.get("unchanged", 0))
            report["catalogue_updates"] += int(repair_summary.get("updated", 0))
            category_changed = int(repair_summary.get("updated", 0)) > 0
            if dead_source_urls:
                selected_dead_identities = {
                    scraper.movie_identity_key(
                        movie.get("name", ""),
                        movie.get("year"),
                        scraper.is_series_movie(movie),
                    )
                    for movie in catalog[category_name]["movies"]
                    if scraper.normalize_source_url(movie.get("source_url", ""))
                    in dead_source_urls
                }
                category_changed = await clean_after_targeted_repair(
                    category_name,
                    dead_source_urls,
                    quarantine,
                    report,
                    selected_dead_identities,
                    target_dead_links={
                        link
                        for links in target_dead_links.values()
                        for link in links
                    },
                ) or category_changed
            if category_changed:
                report["repaired_categories"] += 1

    # বর্তমান catalogue-এ আর নেই এমন URL suspect state থেকে সরিয়ে দিই।
    current_catalog, current_owners = build_catalog(category_names)
    selected_urls = set(current_owners)
    old_suspects = previous_state.get("suspects", {}) if isinstance(previous_state, dict) else {}
    all_categories_selected = set(category_names) == set(scraper.CATEGORIES_LIST)
    preserved_other_suspects = {}
    if not all_categories_selected:
        preserved_other_suspects = {
            url: value
            for url, value in old_suspects.items()
            if url not in owners
            and isinstance(value, dict)
            and set(value.get("categories", [])).isdisjoint(category_names)
        }
    final_suspects = {
        **preserved_other_suspects,
        **{url: value for url, value in suspects.items() if url in selected_urls},
    }
    preserved_other_gaps = {}
    if not all_categories_selected:
        preserved_other_gaps = {
            key: value
            for key, value in old_source_gaps.items()
            if isinstance(value, dict) and value.get("category") not in category_names
        }

    if report["circuit_breaker"]:
        final_source_gaps = dict(old_source_gaps)
    else:
        current_source_counts = catalog_source_counts(current_catalog)
        selected_source_gaps = {}
        for category_name, source_counts in incomplete_sources.items():
            for source_url, advertised_count in source_counts.items():
                key = source_gap_key(category_name, source_url)
                stored_count = current_source_counts.get((category_name, source_url))
                if stored_count is None or stored_count >= advertised_count:
                    continue
                if key in attempted_source_gaps:
                    selected_source_gaps[key] = schedule_source_gap(
                        category_name,
                        source_url,
                        advertised_count,
                        stored_count,
                        old_source_gaps.get(key),
                    )
                elif key in old_source_gaps:
                    selected_source_gaps[key] = old_source_gaps[key]
        final_source_gaps = {**preserved_other_gaps, **selected_source_gaps}

    current_source_keys = {
        source_gap_key(category_name, source_url)
        for (category_name, source_url) in catalog_source_counts(current_catalog)
    }
    current_links_by_source = {}
    for category_name, category_data in current_catalog.items():
        for movie in category_data["movies"]:
            key = source_gap_key(category_name, movie.get("source_url", ""))
            current_links_by_source[key] = {
                item.get("link", "")
                for item in movie.get("res_list", [])
                if item.get("link")
            }
    queue_now = utc_now()
    for key, entry in repair_batch.items():
        if key not in current_source_keys:
            repair_queue.pop(key, None)
            continue
        queued_entry = dict(repair_queue.get(key, entry))
        if entry.get("kind") == "dead":
            remaining_dead_links = [
                link
                for link in queued_entry.get("dead_links", [])
                if link in current_links_by_source.get(key, set())
            ]
            if not remaining_dead_links:
                repair_queue.pop(key, None)
                continue
            queued_entry["dead_links"] = remaining_dead_links
            queued_entry["attempt_count"] = int(queued_entry.get("attempt_count", 0)) + 1
            queued_entry["last_attempt"] = utc_iso(queue_now)
            queued_entry["last_outcome"] = batch_outcomes.get(key) or "still_published"
            repair_queue[key] = queued_entry
            continue
        outcome = batch_outcomes.get(key)
        if outcome in {"updated", "unchanged"}:
            repair_queue.pop(key, None)
            continue
        queued_entry["attempt_count"] = int(queued_entry.get("attempt_count", 0)) + 1
        queued_entry["last_attempt"] = utc_iso(queue_now)
        queued_entry["last_outcome"] = outcome or "not_attempted"
        repair_queue[key] = queued_entry
    report["repair_queue_remaining"] = len(repair_queue)
    report["dead_links_queue_remaining"] = sum(
        len(entry.get("dead_links", []))
        for entry in repair_queue.values()
        if entry.get("kind") == "dead"
    )

    state_payload = {
        "version": 3,
        "suspects": final_suspects,
        "source_gaps": final_source_gaps,
        "repair_queue": repair_queue,
    }
    state_changed = (
        final_suspects != old_suspects
        or final_source_gaps != old_source_gaps
        or repair_queue != old_repair_queue
        or int(previous_state.get("version", 1)) != 3
    )
    if apply_changes and state_changed:
        state_payload["updated_at"] = utc_iso()
        atomic_write_json(STATE_FILE, state_payload)

    if apply_changes and json.dumps(quarantine, sort_keys=True) != quarantine_before:
        quarantine["updated_at"] = utc_iso()
        atomic_write_json(QUARANTINE_FILE, quarantine)

    report["completed_at"] = utc_iso()
    atomic_write_json(report_path, report)
    print(json.dumps(report, indent=2, ensure_ascii=False), flush=True)
    return 2 if report["circuit_breaker"] else 0


def parse_args():
    parser = argparse.ArgumentParser(description="Monitor and repair published stream links independently.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Repair and synchronize confirmed failures")
    mode.add_argument("--dry-run", action="store_true", help="Audit only; do not change catalogue/state")
    parser.add_argument("--category", default="All", help="All or one configured category name")
    parser.add_argument("--report", default=str(DEFAULT_REPORT_FILE), help="JSON report output path")
    return parser.parse_args()


def main():
    args = parse_args()
    categories = selected_category_names(args.category)
    return asyncio.run(run_guardian(categories, args.apply and not args.dry_run, Path(args.report)))


if __name__ == "__main__":
    raise SystemExit(main())
