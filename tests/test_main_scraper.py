import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import main_scraper


class MovieScraperTests(unittest.TestCase):
    def make_config(self, root):
        category_dir = Path(root) / "category"
        category_dir.mkdir()
        return {
            "dir": str(category_dir),
            "file": str(category_dir / "movies.txt"),
            "json": str(category_dir / "movies.json"),
            "m3u": str(category_dir / "movies.m3u"),
        }

    def test_missing_series_keys_are_a_normal_movie(self):
        movie = {
            "name": "Example Movie",
            "category": "Test",
            "year": "2026",
            "poster": "https://example.com/poster.jpg",
            "source_url": "https://example.com/example-movie",
            "res_list": [{"resolution": "HD 1080P", "link": "https://cdn.example.com/movie.mkv"}],
        }
        self.assertFalse(main_scraper.is_series_movie(movie))

        with tempfile.TemporaryDirectory() as root:
            config = self.make_config(root)
            main_scraper.save_category_outputs("Test", config, [movie])
            payload = json.loads(Path(config["json"]).read_text(encoding="utf-8"))
            self.assertNotIn("season", payload["movies"][0]["res_list"][0])
            self.assertEqual(
                Path(config["dir"], "history.txt").read_text(encoding="utf-8").splitlines(),
                ["https://example.com/example-movie"],
            )

    def test_partial_series_metadata_does_not_raise_key_error(self):
        movie = {
            "name": "Example Show",
            "category": "Test",
            "year": "2026",
            "poster": "N/A",
            "source_url": "https://example.com/example-show",
            "res_list": [{"season": "S01", "resolution": "HD 1080P", "link": "https://cdn.example.com/show.mkv"}],
        }
        self.assertTrue(main_scraper.is_series_movie(movie))
        with tempfile.TemporaryDirectory() as root:
            config = self.make_config(root)
            main_scraper.save_category_outputs("Test", config, [movie])
            parsed = main_scraper.parse_existing_output_file(config["file"])
            self.assertEqual(parsed[0]["source_url"], movie["source_url"])
            self.assertEqual(parsed[0]["res_list"][0]["season"], "S01")

    def test_mixed_seasons_round_trip_to_txt_and_m3u(self):
        movie = {
            "name": "Example Show",
            "category": "Test",
            "year": "2026",
            "poster": "https://example.com/poster.jpg",
            "source_url": "https://example.com/example-show",
            "res_list": [
                {"season": "S01", "resolution": "HD 1080P", "link": "https://cdn.example.com/s1.mkv"},
                {"season": "S02", "resolution": "HD 1080P", "link": "https://cdn.example.com/s2.mkv"},
            ],
        }
        with tempfile.TemporaryDirectory() as root:
            config = self.make_config(root)
            main_scraper.save_category_outputs("Test", config, [movie])
            parsed = main_scraper.parse_existing_output_file(config["file"])
            self.assertEqual([item["season"] for item in parsed[0]["res_list"]], ["S01", "S02"])
            m3u = Path(config["m3u"]).read_text(encoding="utf-8")
            self.assertIn("Example Show - S01 - HD 1080P", m3u)
            self.assertIn("Example Show - S02 - HD 1080P", m3u)

    def test_numeric_title_uses_parenthesized_release_year(self):
        res_list = [{
            "link": "https://cdn.example.com/CINEFREAK.TOP%20-%201920%20%282008%29%20WEB-DL%201080p.mkv"
        }]
        self.assertEqual(main_scraper.resolve_movie_identity("1920 (2008) Full Movie", res_list), ("1920", "2008"))

    def test_gross_page_stream_mismatch_uses_content_identity(self):
        res_list = [{
            "link": "https://cdn.example.com/CINEFREAK.TOP%20-%20The%20Bad%20Boy%20And%20Me%202%20%282026%29%20WEB-DL%201080p.mkv"
        }]
        self.assertEqual(
            main_scraper.resolve_movie_identity("Sidelined 2: Intercepted (2025)", res_list),
            ("The Bad Boy And Me 2", "2026"),
        )

    def test_poster_validation_rejects_unrelated_result(self):
        self.assertFalse(main_scraper.poster_result_matches("Paint On Dry Leaf", "2026", "Rongin Shurma", "2026"))
        self.assertTrue(main_scraper.poster_result_matches("Need for Speed", "2014", "Need for Speed", "2014"))

    def test_movie_identity_keeps_remakes_and_separate_series_releases(self):
        self.assertNotEqual(
            main_scraper.movie_identity_key("Example", "1990"),
            main_scraper.movie_identity_key("Example", "2026"),
        )
        self.assertNotEqual(
            main_scraper.movie_identity_key("The East Palace", "2025", True),
            main_scraper.movie_identity_key("East Palace", "2026", True),
        )

    def test_duplicate_series_sources_merge_new_episode_links(self):
        first = {
            "name": "The East Palace",
            "year": "2025",
            "poster": "N/A",
            "source_url": "https://example.com/east-palace-part-one",
            "res_list": [{
                "season": "S01",
                "episode": "E01",
                "resolution": "HD 1080P",
                "link": "https://cdn.example.com/East.Palace.S01E01.mkv",
            }],
        }
        second = {
            "name": "East Palace",
            "year": "2025",
            "poster": "https://example.com/poster.jpg",
            "source_url": "https://example.com/east-palace-part-two",
            "res_list": [{
                "season": "S01",
                "episode": "E02",
                "resolution": "HD 1080P",
                "link": "https://cdn.example.com/East.Palace.S01E02.mkv",
            }],
        }

        merged, aliases = main_scraper.merge_duplicate_movies([first, second])

        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0]["res_list"]), 2)
        self.assertEqual(merged[0]["poster"], second["poster"])
        self.assertEqual(aliases, [second["source_url"]])

    def test_duplicate_source_urls_are_rejected(self):
        base = {
            "category": "Test",
            "year": "2026",
            "poster": "N/A",
            "source_url": "https://example.com/same",
            "res_list": [{"resolution": "HD 1080P", "link": "https://cdn.example.com/movie.mkv"}],
        }
        movies = [{**base, "name": "One"}, {**base, "name": "Two"}]
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, "Duplicate source_url"):
                main_scraper.save_category_outputs("Test", self.make_config(root), movies)

    def test_repair_removes_unreplaceable_dead_quality_when_alive_quality_remains(self):
        alive_link = "https://cdn.example.com/movie.1080p.mkv"
        original = [
            {"resolution": "HEVC 1080P", "link": "https://cdn.example.com/dead-hevc.mkv"},
            {"resolution": "HD 1080P", "link": alive_link},
        ]
        repaired, replaced, removed = main_scraper.reconcile_repair_links(
            original,
            [0],
            [{"resolution": "HD 1080P", "link": alive_link}],
        )
        self.assertEqual(repaired, [original[1]])
        self.assertEqual((replaced, removed), (0, 1))

    def test_repair_matches_fresh_links_by_resolution(self):
        original = [
            {"resolution": "HD 1080P", "link": "https://cdn.example.com/dead-hd.mkv"},
            {"resolution": "HEVC 1080P", "link": "https://cdn.example.com/dead-hevc.mkv"},
        ]
        fresh = [
            {"resolution": "HEVC 1080P", "link": "https://cdn.example.com/fresh-hevc.mkv"},
            {"resolution": "HD 1080P", "link": "https://cdn.example.com/fresh-hd.mkv"},
        ]
        repaired, replaced, removed = main_scraper.reconcile_repair_links(original, [0, 1], fresh)
        self.assertEqual([item["resolution"] for item in repaired], ["HD 1080P", "HEVC 1080P"])
        self.assertEqual((replaced, removed), (2, 0))

    def test_repair_does_not_assign_a_different_season_to_dead_episode(self):
        original = [{
            "season": "S01",
            "episode": "Episode 01",
            "resolution": "HD 1080P",
            "link": "https://cdn.example.com/dead-s1e1.mkv",
        }]
        fresh = [{
            "season": "S02",
            "episode": "Episode 01",
            "resolution": "HD 1080P",
            "link": "https://cdn.example.com/fresh-s2e1.mkv",
        }]
        repaired, replaced, removed = main_scraper.reconcile_repair_links(original, [0], fresh)
        self.assertEqual(repaired[0]["season"], "S02")
        self.assertEqual((replaced, removed), (0, 1))

    def test_site_search_rejects_unrelated_first_result(self):
        unrelated = {
            "href": "https://example.com/random-action-movie-2026-download",
            "text": "Random Action Movie (2026)",
        }
        exact = {
            "href": "https://example.com/hero-2021-full-movie-download",
            "text": "Hero (2021) Full Movie Download",
        }
        self.assertLess(main_scraper.site_search_match_score("Hero", unrelated), 0.72)
        self.assertGreaterEqual(main_scraper.site_search_match_score("Hero", exact), 0.72)

    def test_failed_repair_cache_is_category_scoped(self):
        failures = {}
        main_scraper.record_failed_repair("Hero", "Bangla Movies", ["dead"], failures)
        self.assertTrue(main_scraper.should_skip_repair("Hero", "Bangla Movies", failures))
        self.assertFalse(main_scraper.should_skip_repair("Hero", "Hindi Movies", failures))


class ScannerStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_repair_does_not_overwrite_scan_rotation(self):
        state = {"current_category_index": 4, "run_count": 2}
        repair = AsyncMock()
        with (
            patch.object(main_scraper, "SCAN_MODE", "REPAIR_AUTO"),
            patch.object(main_scraper, "load_tracker_state", return_value=state),
            patch.object(main_scraper, "save_tracker_state") as save_state,
            patch.object(main_scraper, "repair_dead_links", repair),
        ):
            await main_scraper.main()

        self.assertEqual(state["current_category_index"], 4)
        self.assertEqual(state["run_count"], 2)
        self.assertEqual(state["repair_category_index"], 5)
        save_state.assert_called_once_with(state)

    async def test_specific_repair_bypasses_cooldown_and_gets_full_time_budget(self):
        repair = AsyncMock()
        with (
            patch.object(main_scraper, "SCAN_MODE", "REPAIR_SPECIFIC"),
            patch.object(main_scraper, "REPAIR_CATEGORY", "Hindi Movies"),
            patch.object(main_scraper, "load_tracker_state", return_value={}),
            patch.object(main_scraper, "repair_dead_links", repair),
        ):
            await main_scraper.main()

        repair.assert_awaited_once_with(
            "Hindi Movies",
            main_scraper.CATEGORIES_MAP["Hindi Movies"],
            respect_cooldown=False,
            max_repair_minutes=75,
        )

    async def test_specific_repair_checks_beyond_the_old_first_twenty_limit(self):
        with tempfile.TemporaryDirectory() as root:
            category_dir = Path(root) / "category"
            category_dir.mkdir()
            json_path = category_dir / "movies.json"
            movies = [
                {
                    "name": f"Movie {index}",
                    "category": "Test",
                    "year": "2026",
                    "poster": "N/A",
                    "source_url": f"https://example.com/movie-{index}",
                    "res_list": [{
                        "resolution": "HD 1080P",
                        "link": f"https://cdn.example.com/movie-{index}.mkv",
                    }],
                }
                for index in range(25)
            ]
            json_path.write_text(json.dumps({"movies": movies}), encoding="utf-8")
            config = {
                "dir": str(category_dir),
                "file": str(category_dir / "movies.txt"),
                "json": str(json_path),
                "m3u": str(category_dir / "movies.m3u"),
                "slug": "test",
            }
            with patch.object(main_scraper, "is_stream_link_dead_sync", return_value=False) as health_check:
                await main_scraper.repair_dead_links("Test", config, respect_cooldown=False)
                self.assertEqual(health_check.call_count, 25)
                health_check.reset_mock()
                await main_scraper.repair_dead_links(
                    "Test",
                    config,
                    respect_cooldown=False,
                    target_source_urls={"https://example.com/movie-24"},
                )
                self.assertEqual(health_check.call_count, 1)


if __name__ == "__main__":
    unittest.main()
