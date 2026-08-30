import json
import re
import unittest
from collections import Counter
from pathlib import Path

import main_scraper


ROOT = Path(__file__).resolve().parents[1]


class RepositoryDataTests(unittest.TestCase):
    def test_every_category_output_and_history_match(self):
        for category_name, config in main_scraper.CATEGORIES_MAP.items():
            with self.subTest(category=category_name):
                payload = json.loads((ROOT / config["json"]).read_text(encoding="utf-8"))
                movies = payload["movies"]
                txt = (ROOT / config["file"]).read_text(encoding="utf-8")
                m3u = (ROOT / config["m3u"]).read_text(encoding="utf-8")
                history = main_scraper.load_history_urls(ROOT / config["dir"] / "history.txt")
                skipped = main_scraper.load_history_urls(ROOT / config["dir"] / "history_skipped.txt")

                json_links = [item["link"] for movie in movies for item in movie["res_list"]]
                txt_links = [
                    match.group(1).strip()
                    for match in re.finditer(r"(?m)^\s*(?:STREAM Link \d+|Link-\d+):\s*(.+)$", txt)
                ]
                m3u_links = [line.strip() for line in m3u.splitlines() if line.startswith(("http://", "https://"))]
                source_urls = [movie["source_url"] for movie in movies]

                self.assertEqual(payload["category_info"]["total_movies"], len(movies))
                self.assertEqual(len(re.findall(r"(?m)^(?:Movie name|Show name):", txt)), len(movies))
                self.assertEqual(Counter(json_links), Counter(txt_links))
                self.assertEqual(Counter(json_links), Counter(m3u_links))
                self.assertEqual(source_urls, history)
                self.assertEqual(len(source_urls), len(set(source_urls)))
                self.assertTrue(set(source_urls).isdisjoint(skipped))
                self.assertTrue(all(movie.get("poster") not in {None, "", "N/A", "None"} for movie in movies))
                self.assertTrue(all(f'source-url="{url}"' in m3u for url in source_urls))

                identities = [
                    main_scraper.movie_identity_key(
                        movie["name"], movie.get("year"), main_scraper.is_series_movie(movie)
                    )
                    for movie in movies
                ]
                self.assertEqual(len(identities), len(set(identities)))

    def test_scanner_and_guardian_share_one_non_cancelling_write_lock(self):
        scanner_workflow = (ROOT / ".github/workflows/scraper.yml").read_text(encoding="utf-8")
        guardian_workflow = (ROOT / ".github/workflows/stream_guardian.yml").read_text(encoding="utf-8")
        for workflow in (scanner_workflow, guardian_workflow):
            self.assertIn("group: movie-catalog-writer", workflow)
            self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("cron: '17 */3 * * *'", guardian_workflow)
        self.assertIn("github.event_name == 'push' && 'DRY_RUN'", guardian_workflow)
        self.assertNotIn("Auto Repair Dead Links", scanner_workflow)


if __name__ == "__main__":
    unittest.main()
