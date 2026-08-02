async def main():
    scraped_history = set()
    scan_mode = os.environ.get("SCAN_MODE", "ALL") # কোলাব বা গিটহাব অ্যাকশনস থেকে মোড রিড করা

    os.makedirs("history", exist_ok=True)
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            scraped_history = set(line.strip() for line in f if line.strip())

    sem = asyncio.Semaphore(2)

    async def safe_process(browser_inst, url, idx, cat_name):
        async with sem:
            return await process_single_movie(browser_inst, url, idx, cat_name)

    # যদি নির্দিষ্ট কোনো ক্যাটাগরি সিলেক্ট করা থাকে তবে শুধুই সেটি ফিল্টার করা
    target_categories = CATEGORIES_MAP
    if scan_mode != "ALL" and scan_mode in CATEGORIES_MAP:
        target_categories = {scan_mode: CATEGORIES_MAP[scan_mode]}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])

        for cat_display_name, config in target_categories.items():
            cat_slug = config["slug"]
            file_path = config["file"]
            os.makedirs(os.path.dirname(file_path), exist_ok=True)

            print(f"\n==================================================")
            print(f"📂 Scanning Category: {cat_display_name} ({cat_slug})")
            print(f"==================================================")

            page_main = await browser.new_page()
            await page_main.route("**/*", lambda route: route.abort() if any(ad in route.request.url.lower() for ad in AD_DOMAINS) else route.continue_())

            new_movie_urls = []
            page_num = 1

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

    print("\n🎉 Scraping Process Finished Successfully!")
