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
                self.assertNotIn("Source URL:", txt)
                self.assertTrue(all(source_url not in txt for source_url in source_urls))
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
        self.assertIn("cron: '17 */2 * * *'", guardian_workflow)
        self.assertIn("সর্বোচ্চ ১৫টি confirmed dead stream link", guardian_workflow)
        self.assertIn("github.event_name == 'push' && 'DRY_RUN'", guardian_workflow)
        self.assertIn("id: guardian_repair", guardian_workflow)
        self.assertIn("continue-on-error: true", guardian_workflow)
        self.assertIn(
            "if: always() && (github.event_name == 'schedule' || github.event.inputs.guardian_mode == 'APPLY')",
            guardian_workflow,
        )
        self.assertIn("steps.guardian_repair.outcome == 'failure'", guardian_workflow)
        self.assertIn("git diff --staged --quiet -- categories/", guardian_workflow)
        self.assertIn("id: guardian_publish", guardian_workflow)
        self.assertIn("git diff --staged --name-status", guardian_workflow)
        self.assertIn('echo "status=pushed" >> "$GITHUB_OUTPUT"', guardian_workflow)
        self.assertIn("Guardian Detailed Run Summary", guardian_workflow)
        self.assertIn("GITHUB_STEP_SUMMARY", guardian_workflow)
        self.assertIn("Category scan results", guardian_workflow)
        self.assertIn("Publish result", guardian_workflow)
        self.assertIn("Stream Guardian state maintenance", guardian_workflow)
        self.assertIn("Stream Guardian validated category repair", guardian_workflow)
        self.assertNotIn("Auto Repair Dead Links", scanner_workflow)
        self.assertNotIn("REPAIR_AUTO", scanner_workflow)
        self.assertNotIn("REPAIR_SPECIFIC", scanner_workflow)
        self.assertNotIn("repair_category:", scanner_workflow)
        self.assertIn("- MANUAL_CATEGORY", scanner_workflow)
        self.assertIn("scan_category:", scanner_workflow)
        self.assertIn("github.event.inputs.scan_category", scanner_workflow)
        for category_name in main_scraper.CATEGORIES_LIST:
            self.assertIn(f"- {category_name}", scanner_workflow)


if __name__ == "__main__":
    unittest.main()
