"""Audit and reconcile category history with the generated movie outputs."""

import argparse
import asyncio
import html
import json
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main_scraper as scraper  # noqa: E402


def fetch_page_metadata(url):
    request = urllib.request.Request(url, headers={"User-Agent": scraper.USER_AGENTS[0]})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read().decode("utf-8", errors="replace")
            final_url = scraper.normalize_source_url(response.geturl())
            status = response.status
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        final_url = scraper.normalize_source_url(error.geturl())
        status = error.code
    except Exception as error:
        return {"url": url, "final_url": url, "status": 0, "title": "", "error": str(error)}

    title_match = re.search(r"<title[^>]*>(.*?)</title>", body, flags=re.IGNORECASE | re.DOTALL)
    title = html.unescape(re.sub(r"\s+", " ", title_match.group(1))).strip() if title_match else ""
    title = re.sub(r"\s*\|\s*CINEFREAK\s*$", "", title, flags=re.IGNORECASE)
    image_candidates = re.findall(
        r"https?://[^\"'<>\\ ]+\.(?:webp|jpe?g|png)",
        html.unescape(body),
        flags=re.IGNORECASE,
    )
    poster = next(
        (
            image_url
            for image_url in image_candidates
            if "cropped-cgk" not in image_url.lower()
            and "cinefeak.webp" not in image_url.lower()
            and "placeholder" not in image_url.lower()
        ),
        "",
    )
    return {
        "url": url,
        "final_url": final_url,
        "status": status,
        "title": title,
        "poster": poster,
        "error": "",
    }


def fetch_all_metadata(urls):
    metadata = {}
    with ThreadPoolExecutor(max_workers=12) as executor:
        pending = {executor.submit(fetch_page_metadata, url): url for url in urls}
        for future in as_completed(pending):
            item = future.result()
            metadata[pending[future]] = item
    return metadata


def match_score(movie, source_url, metadata):
    score = scraper.source_url_match_score(movie, source_url)
    page_title = metadata.get(source_url, {}).get("title", "")
    if page_title:
        page_name, _ = scraper.resolve_movie_identity(page_title, [])
        stream_name, _ = scraper.extract_stream_identity(movie.get("res_list", []))
        score = max(
            score,
            scraper.title_similarity(movie.get("name", ""), page_name),
            scraper.title_similarity(stream_name, page_name) if stream_name else 0.0,
        )
    return score


def assign_sources(movies, category_urls, all_urls, metadata):
    assignments = {}
    used_urls = set()
    category_url_set = set(category_urls)
    for movie_index, movie in enumerate(movies):
        stored_url = scraper.normalize_source_url(movie.get("source_url", ""))
        if stored_url and stored_url in category_url_set and stored_url not in used_urls:
            assignments[movie_index] = stored_url
            used_urls.add(stored_url)

    pairs = sorted(
        (match_score(movie, url, metadata), movie_index, url)
        for movie_index, movie in enumerate(movies)
        for url in category_urls
    )
    for score, movie_index, url in reversed(pairs):
        if score < 0.72:
            break
        if movie_index not in assignments and url not in used_urls:
            assignments[movie_index] = url
            used_urls.add(url)

    missing_indexes = [index for index in range(len(movies)) if index not in assignments]
    for movie_index in missing_indexes:
        ranked = sorted(
            (match_score(movies[movie_index], url, metadata), url)
            for url in all_urls
            if url not in used_urls
        )
        if ranked and ranked[-1][0] >= 0.72:
            assignments[movie_index] = ranked[-1][1]
            used_urls.add(ranked[-1][1])

    return assignments, [url for url in category_urls if url not in used_urls]


async def scan_orphan_urls(orphan_by_category):
    results = {}
    async with scraper.async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=scraper.LAUNCH_ARGS)
        semaphore = asyncio.Semaphore(2)

        async def scan_one(category_name, url, index):
            async with semaphore:
                result = await scraper.process_movie_parallel_pipeline(browser, url, index, category_name)
                return category_name, result

        tasks = []
        for category_name, urls in orphan_by_category.items():
            tasks.extend(scan_one(category_name, url, index) for index, url in enumerate(urls, 1))
        try:
            for category_name, result in await asyncio.gather(*tasks):
                results.setdefault(category_name, []).append(result)
        finally:
            await browser.close()
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Rewrite synchronized category files")
    parser.add_argument("--scan-orphans", action="store_true", help="Run the unchanged scanner on unmatched history pages")
    args = parser.parse_args()

    category_data = {}
    all_urls = []
    for category_name, config in scraper.CATEGORIES_MAP.items():
        payload = json.loads((ROOT / config["json"]).read_text(encoding="utf-8"))
        history_urls = scraper.load_history_urls(ROOT / config["dir"] / "history.txt")
        skipped_urls = scraper.load_history_urls(ROOT / config["dir"] / "history_skipped.txt")
        category_data[category_name] = {
            "config": config,
            "movies": payload["movies"],
            "history": history_urls,
            "skipped": skipped_urls,
        }
        all_urls.extend(history_urls)
    all_urls = list(dict.fromkeys(all_urls))

    poster_titles = {}
    for data in category_data.values():
        for movie in data["movies"]:
            poster_titles.setdefault(movie.get("poster", ""), set()).add(scraper.normalize_title_key(movie["name"]))
    duplicated_poster_urls = {
        poster_url for poster_url, title_keys in poster_titles.items() if poster_url and len(title_keys) > 1
    }

    print(f"Fetching live metadata for {len(all_urls)} unique history URLs...")
    metadata = fetch_all_metadata(all_urls)
    orphan_by_category = {}

    for category_name, data in category_data.items():
        assignments, orphans = assign_sources(data["movies"], data["history"], all_urls, metadata)
        for index, source_url in assignments.items():
            data["movies"][index]["source_url"] = scraper.normalize_source_url(source_url)
        for movie in data["movies"]:
            name, year = scraper.resolve_movie_identity(movie.get("name", ""), movie.get("res_list", []))
            movie["name"] = name
            if year != "N/A":
                movie["year"] = year
            current_poster = str(movie.get("poster", ""))
            if current_poster in duplicated_poster_urls or "placeholder" in current_poster.lower() or (
                "m.media-amazon.com" in current_poster
                and ("@._V1_" in current_poster or current_poster.endswith("_V1_SX300.jpg"))
            ):
                source_metadata = metadata.get(movie.get("source_url", ""), {})
                if source_metadata.get("poster"):
                    movie["poster"] = source_metadata["poster"]
        missing = [data["movies"][index]["name"] for index in range(len(data["movies"])) if index not in assignments]
        orphan_by_category[category_name] = orphans
        print(
            f"{category_name}: movies={len(data['movies'])}, mapped={len(assignments)}, "
            f"unmapped={len(missing)}, orphan_history={len(orphans)}"
        )
        if missing:
            print(f"  Unmapped movies: {missing}")

    scanned = asyncio.run(scan_orphan_urls(orphan_by_category)) if args.scan_orphans else {}
    for category_name, results in scanned.items():
        data = category_data[category_name]
        identities = {
            scraper.movie_identity_key(movie["name"], movie.get("year"), scraper.is_series_movie(movie))
            for movie in data["movies"]
        }
        recovered = 0
        for source_url, title, categories, res_list, web_poster in results:
            if not res_list:
                continue
            name, year = scraper.resolve_movie_identity(title, res_list)
            parsed_res_list = [scraper.parse_link_metadata(item["link"], item["resolution"]) for item in res_list]
            identity = scraper.movie_identity_key(name, year, scraper.is_series_movie({"res_list": parsed_res_list}))
            if identity in identities:
                continue
            poster = web_poster if web_poster != "N/A" else scraper.fetch_poster_sync_combined(name, year)
            data["movies"].append({
                "name": name,
                "category": categories,
                "year": year,
                "poster": poster,
                "source_url": scraper.normalize_source_url(source_url),
                "res_list": parsed_res_list,
            })
            identities.add(identity)
            recovered += 1
        print(f"{category_name}: recovered_from_orphans={recovered}")

    if not args.apply:
        print("Dry run only. Use --apply after reviewing the report.")
        return

    for category_name, data in category_data.items():
        data["movies"], alias_urls = scraper.merge_duplicate_movies(data["movies"])
        orphan_by_category[category_name].extend(alias_urls)

    unresolved = [
        (category_name, movie["name"])
        for category_name, data in category_data.items()
        for movie in data["movies"]
        if not movie.get("source_url")
    ]
    if unresolved:
        raise SystemExit(f"Refusing to write with unresolved source URLs: {unresolved}")

    for category_name, data in category_data.items():
        config = {key: str(ROOT / value) if key in {"dir", "file", "json", "m3u"} else value for key, value in data["config"].items()}
        scraper.save_category_outputs(category_name, config, data["movies"])
        canonical_sources = {scraper.normalize_source_url(movie["source_url"]) for movie in data["movies"]}
        skipped_urls = [
            url
            for url in dict.fromkeys(data["skipped"] + orphan_by_category[category_name])
            if url not in canonical_sources
        ]
        skipped_file = Path(config["dir"]) / "history_skipped.txt"
        with open(skipped_file, "w", encoding="utf-8", newline="\n") as output_file:
            output_file.write("".join(f"{scraper.normalize_source_url(url)}\n" for url in skipped_urls))
    print("Reconciliation applied successfully.")


if __name__ == "__main__":
    main()
