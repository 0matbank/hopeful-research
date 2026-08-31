import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
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
            txt = Path(config["file"]).read_text(encoding="utf-8")
            self.assertNotIn("season", payload["movies"][0]["res_list"][0])
            self.assertNotIn("Source URL:", txt)
            self.assertNotIn(movie["source_url"], txt)
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
            Path(config["json"]).unlink()
            parsed = main_scraper.load_existing_movies(config)
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
            parsed = main_scraper.load_existing_movies(config)
            self.assertEqual([item["season"] for item in parsed[0]["res_list"]], ["S01", "S02"])
            m3u = Path(config["m3u"]).read_text(encoding="utf-8")
            self.assertIn("Example Show - S01 - HD 1080P", m3u)
            self.assertIn("Example Show - S02 - HD 1080P", m3u)

    def test_numeric_title_uses_parenthesized_release_year(self):
        res_list = [{
            "link": "https://cdn.example.com/CINEFREAK.TOP%20-%201920%20%282008%29%20WEB-DL%201080p.mkv"
        }]
        self.assertEqual(main_scraper.resolve_movie_identity("1920 (2008) Full Movie", res_list), ("1920", "2008"))

    def test_download_prefix_is_not_part_of_movie_title(self):
        res_list = [{
            "link": "https://cdn.example.com/CINEFREAK.TOP%20-%20Vishnu%20Vinyasam%20%282026%29%20WEB-DL%201080p.mkv"
        }]
        self.assertEqual(
            main_scraper.resolve_movie_identity("Download Vishnu Vinyasam (2026)", res_list),
            ("Vishnu Vinyasam", "2026"),
        )

    def test_gross_page_stream_mismatch_uses_content_identity(self):
        res_list = [{
            "link": "https://cdn.example.com/CINEFREAK.TOP%20-%20The%20Bad%20Boy%20And%20Me%202%20%282026%29%20WEB-DL%201080p.mkv"
        }]
        self.assertEqual(
            main_scraper.resolve_movie_identity("Sidelined 2: Intercepted (2025)", res_list),
            ("The Bad Boy And Me 2", "2026"),
        )

    def test_matching_stream_title_does_not_override_source_page_year(self):
        res_list = [{
            "link": "https://cdn.example.com/CINEFREAK.TOP%20-%20BrocheVarueVarura%20%282021%29%20WEB-DL%201080p.mkv"
        }]
        self.assertEqual(
            main_scraper.resolve_movie_identity("Brochevarevarura (2019) Full Movie", res_list),
            ("Brochevarevarura", "2019"),
        )

    def test_poster_validation_rejects_unrelated_result(self):
        self.assertFalse(main_scraper.poster_result_matches("Paint On Dry Leaf", "2026", "Rongin Shurma", "2026"))
        self.assertTrue(main_scraper.poster_result_matches("Need for Speed", "2014", "Need for Speed", "2014"))

    def test_media_payload_validation_rejects_html_and_accepts_mkv_mp4_hls(self):
        self.assertFalse(main_scraper.is_media_payload_sample(b"<!doctype html><html>Denied</html>", "text/html"))
        self.assertTrue(main_scraper.is_media_payload_sample(b"\x1aE\xdf\xa3" + b"x" * 64, "application/octet-stream"))
        self.assertTrue(main_scraper.is_media_payload_sample(b"\x00\x00\x00\x18ftypisom" + b"x" * 64, "video/mp4"))
        self.assertTrue(main_scraper.is_media_payload_sample(b"#EXTM3U\n#EXT-X-VERSION:3", "application/vnd.apple.mpegurl"))

    def test_short_advertising_media_assets_are_not_movie_streams(self):
        advertising_urls = [
            "https://turbohost26.online/landers/example/assets/img/background.mp4",
            "https://video.wixstatic.com/video/example/480p/mp4/file.mp4",
            "https://m.media-amazon.com/images/I/example.mp4",
            "https://www.vpnapptoggle.com/video/vid_1_mp4.mp4",
            "https://chatmate.tv/low-power-mode.mp4",
        ]
        for url in advertising_urls:
            with self.subTest(url=url):
                self.assertTrue(main_scraper.is_obvious_non_movie_media_url(url))
                self.assertEqual(main_scraper.probe_stream_link_sync(url)["reason"], "non_movie_media_asset")

        self.assertFalse(
            main_scraper.is_obvious_non_movie_media_url(
                "https://pub-example.r2.dev/CINEFREAK.TOP%20-%20Movie%20(2026)%201080p.mkv"
            )
        )

    def test_resolution_detector_preserves_hq_quality(self):
        self.assertEqual(
            main_scraper.detect_resolution_from_stream_url(
                "https://cdn.example.com/Example.WEB-DL.HQ.1080p.mkv"
            ),
            "HQ 1080P",
        )

    def test_current_source_stream_set_replaces_stale_subset(self):
        fresh = [
            {"resolution": "HEVC 1080P", "link": "https://cdn.example.com/hevc.mkv"},
            {"resolution": "HD 1080P", "link": "https://cdn.example.com/hd.mkv"},
            {"resolution": "HQ 1080P", "link": "https://cdn.example.com/hq.mkv"},
            {"resolution": "4K 2160P HEVC", "link": "https://cdn.example.com/4k.mkv"},
            {"resolution": "4K 2160P HEVC", "link": "https://cdn.example.com/4k.mkv"},
        ]
        canonical = main_scraper.canonical_fresh_res_list(fresh)
        self.assertEqual(len(canonical), 4)
        self.assertEqual([item["link"] for item in canonical], [item["link"] for item in fresh[:4]])

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
    async def test_successful_repair_is_checkpointed_before_later_source_failure(self):
        with tempfile.TemporaryDirectory() as root:
            category_dir = Path(root) / "category"
            category_dir.mkdir()
            config = {
                "dir": str(category_dir),
                "file": str(category_dir / "movies.txt"),
                "json": str(category_dir / "movies.json"),
                "m3u": str(category_dir / "movies.m3u"),
                "slug": "test",
            }
            first_source = "https://example.com/first"
            second_source = "https://example.com/second"
            first_dead = "https://cdn.example.com/first-dead.mkv"
            second_dead = "https://cdn.example.com/second-dead.mkv"
            first_fresh = "https://cdn.example.com/first-fresh.mkv"
            movies = [
                {
                    "name": "First Movie",
                    "category": "Test",
                    "year": "2026",
                    "poster": "N/A",
                    "source_url": first_source,
                    "res_list": [{"resolution": "HD 1080P", "link": first_dead}],
                },
                {
                    "name": "Second Movie",
                    "category": "Test",
                    "year": "2026",
                    "poster": "N/A",
                    "source_url": second_source,
                    "res_list": [{"resolution": "HD 1080P", "link": second_dead}],
                },
            ]
            main_scraper.save_category_outputs("Test", config, movies)

            pipeline = AsyncMock(side_effect=[
                (
                    first_source,
                    "First Movie",
                    "Test",
                    [{"resolution": "HD 1080P", "link": first_fresh}],
                    "N/A",
                ),
                RuntimeError("second source browser failure"),
            ])
            browser = SimpleNamespace(close=AsyncMock())
            playwright = SimpleNamespace(
                chromium=SimpleNamespace(launch=AsyncMock(return_value=browser))
            )
            manager = AsyncMock()
            manager.__aenter__.return_value = playwright
            manager.__aexit__.return_value = False

            with (
                patch.object(main_scraper, "async_playwright", return_value=manager),
                patch.object(main_scraper, "RUN_ENV", "github"),
                patch.object(main_scraper, "is_stream_link_dead_sync", return_value=True),
                patch.object(main_scraper, "is_page_url_alive_sync", return_value=True),
                patch.object(main_scraper, "process_movie_parallel_pipeline", pipeline),
                patch.object(
                    main_scraper,
                    "probe_stream_link_sync",
                    return_value={
                        "status": "alive",
                        "reason": "media_bytes_ok",
                        "http_status": 206,
                    },
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "second source browser failure"):
                    await main_scraper.repair_dead_links(
                        "Test",
                        config,
                        respect_cooldown=False,
                        target_source_urls={first_source, second_source},
                        target_stream_urls={
                            first_source: {first_dead},
                            second_source: {second_dead},
                        },
                    )

            payload = json.loads(Path(config["json"]).read_text(encoding="utf-8"))
            repaired = next(
                movie for movie in payload["movies"] if movie["name"] == "First Movie"
            )
            self.assertEqual(repaired["res_list"][0]["link"], first_fresh)
            self.assertIn(first_fresh, Path(config["file"]).read_text(encoding="utf-8"))
            self.assertIn(first_fresh, Path(config["m3u"]).read_text(encoding="utf-8"))

    def test_failed_candidates_do_not_block_fresh_deeper_posts(self):
        failed_urls = [f"https://example.com/failed-{index}" for index in range(10)]
        fresh_urls = [f"https://example.com/fresh-{index}" for index in range(10)]
        state = {"version": 1, "categories": {}}
        for url in failed_urls:
            main_scraper.record_candidate_outcome("Test", url, False, state, now_timestamp=1000)

        selected, cooling = main_scraper.select_scan_candidates(
            failed_urls + fresh_urls,
            "Test",
            state,
            now_timestamp=1001,
        )

        self.assertEqual(selected, fresh_urls)
        self.assertEqual(cooling, 10)

    def test_failed_candidate_is_retried_after_backoff_and_success_clears_it(self):
        url = "https://example.com/retry-me"
        state = {"version": 1, "categories": {}}
        main_scraper.record_candidate_outcome("Test", url, False, state, now_timestamp=1000)

        selected, cooling = main_scraper.select_scan_candidates([url], "Test", state, now_timestamp=1001)
        self.assertEqual(selected, [])
        self.assertEqual(cooling, 1)

        selected, cooling = main_scraper.select_scan_candidates(
            [url], "Test", state, now_timestamp=1000 + 12 * 3600
        )
        self.assertEqual(selected, [url])
        self.assertEqual(cooling, 0)
        self.assertTrue(main_scraper.record_candidate_outcome("Test", url, True, state, now_timestamp=2000))
        self.assertNotIn(url, state["categories"]["Test"])

    async def test_legacy_main_scanner_repair_modes_are_disabled(self):
        for mode in ("REPAIR_AUTO", "REPAIR_SPECIFIC"):
            with self.subTest(mode=mode):
                repair = AsyncMock()
                with (
                    patch.object(main_scraper, "SCAN_MODE", mode),
                    patch.object(main_scraper, "load_tracker_state", return_value={}),
                    patch.object(main_scraper, "repair_dead_links", repair),
                ):
                    await main_scraper.main()
                repair.assert_not_awaited()

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
                second_link = "https://cdn.example.com/movie-24-alt.mkv"
                movies[24]["res_list"].append({
                    "resolution": "HEVC 1080P",
                    "link": second_link,
                })
                json_path.write_text(json.dumps({"movies": movies}), encoding="utf-8")
                selected_link = "https://cdn.example.com/movie-24.mkv"
                await main_scraper.repair_dead_links(
                    "Test",
                    config,
                    respect_cooldown=False,
                    target_source_urls={"https://example.com/movie-24"},
                    target_stream_urls={
                        "https://example.com/movie-24": {selected_link},
                    },
                )
                self.assertEqual(health_check.call_count, 1)
                health_check.assert_called_once_with(selected_link)

    async def test_guardian_retries_missing_buttons_and_keeps_complete_fresh_set(self):
        with tempfile.TemporaryDirectory() as root:
            category_dir = Path(root) / "category"
            category_dir.mkdir()
            config = {
                "dir": str(category_dir),
                "file": str(category_dir / "movies.txt"),
                "json": str(category_dir / "movies.json"),
                "m3u": str(category_dir / "movies.m3u"),
                "slug": "test",
            }
            source_url = "https://example.com/movie"
            old_link = "https://cdn.example.com/old.mkv"
            movie = {
                "name": "Example Movie",
                "category": "Test",
                "year": "2026",
                "poster": "N/A",
                "source_url": source_url,
                "res_list": [{"resolution": "HD 1080P", "link": old_link}],
            }
            Path(config["json"]).write_text(json.dumps({"movies": [movie]}), encoding="utf-8")
            Path(config["dir"], "history.txt").write_text(source_url + "\n", encoding="utf-8")

            first_attempt = [
                {"resolution": "HEVC 1080P", "link": "https://cdn.example.com/one.mkv"},
                {"resolution": "HD 1080P", "link": "https://cdn.example.com/two.mkv"},
            ]
            second_attempt = first_attempt + [
                {"resolution": "HQ 1080P", "link": "https://cdn.example.com/three.mkv"},
                {"resolution": "4K 2160P", "link": "https://cdn.example.com/four.mkv"},
            ]
            pipeline = AsyncMock(side_effect=[
                (source_url, "Example Movie", "Test", first_attempt, "N/A"),
                (source_url, "Example Movie", "Test", second_attempt, "N/A"),
            ])
            browser = SimpleNamespace(close=AsyncMock())
            playwright = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))

            class PlaywrightContext:
                async def __aenter__(self):
                    return playwright

                async def __aexit__(self, exc_type, exc, traceback):
                    return False

            with (
                patch.object(main_scraper, "async_playwright", return_value=PlaywrightContext()),
                patch.object(main_scraper, "is_page_url_alive_sync", return_value=True),
                patch.object(main_scraper, "is_stream_link_dead_sync", return_value=False),
                patch.object(
                    main_scraper,
                    "probe_stream_link_sync",
                    return_value={"status": "alive", "reason": "media_bytes_ok", "http_status": 206},
                ),
                patch.object(main_scraper, "process_movie_parallel_pipeline", pipeline),
                patch.object(main_scraper, "load_failed_repairs", return_value={}),
                patch.object(main_scraper, "save_failed_repairs"),
            ):
                summary = await main_scraper.repair_dead_links(
                    "Test",
                    config,
                    respect_cooldown=False,
                    target_source_urls={source_url},
                    force_refresh_source_urls={source_url},
                    expected_source_counts={source_url: 4},
                )

            saved = json.loads(Path(config["json"]).read_text(encoding="utf-8"))["movies"][0]
            self.assertEqual(pipeline.await_count, 2)
            self.assertEqual(summary["updated"], 1)
            self.assertEqual(summary["unchanged"], 0)
            self.assertEqual([item["link"] for item in saved["res_list"]], [item["link"] for item in second_attempt])

    async def test_refresh_with_same_link_and_ad_does_not_rewrite_catalogue(self):
        with tempfile.TemporaryDirectory() as root:
            category_dir = Path(root) / "category"
            category_dir.mkdir()
            config = {
                "dir": str(category_dir),
                "file": str(category_dir / "movies.txt"),
                "json": str(category_dir / "movies.json"),
                "m3u": str(category_dir / "movies.m3u"),
                "slug": "test",
            }
            source_url = "https://example.com/movie"
            old_link = "https://cdn.example.com/movie.mkv"
            ad_link = "https://m.media-amazon.com/images/I/example.mp4"
            movie = {
                "name": "Example Movie",
                "category": "Test",
                "year": "2026",
                "poster": "N/A",
                "source_url": source_url,
                "res_list": [{"resolution": "HD 1080P", "link": old_link}],
            }
            json_path = Path(config["json"])
            json_path.write_text(json.dumps({"movies": [movie]}), encoding="utf-8")
            Path(config["dir"], "history.txt").write_text(source_url + "\n", encoding="utf-8")
            original_json = json_path.read_bytes()
            fresh_items = [
                {"resolution": "HD 1080P", "link": old_link},
                {"resolution": "HD 1080P", "link": ad_link},
            ]
            pipeline = AsyncMock(return_value=(
                source_url,
                "Example Movie",
                "Test",
                fresh_items,
                "N/A",
            ))
            browser = SimpleNamespace(close=AsyncMock())
            playwright = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))

            class PlaywrightContext:
                async def __aenter__(self):
                    return playwright

                async def __aexit__(self, exc_type, exc, traceback):
                    return False

            def probe(url, timeout=8):
                if url == ad_link:
                    return {"status": "dead", "reason": "non_movie_media_asset", "http_status": 0}
                return {"status": "alive", "reason": "media_bytes_ok", "http_status": 206}

            with (
                patch.object(main_scraper, "async_playwright", return_value=PlaywrightContext()),
                patch.object(main_scraper, "is_page_url_alive_sync", return_value=True),
                patch.object(main_scraper, "is_stream_link_dead_sync", return_value=False),
                patch.object(main_scraper, "probe_stream_link_sync", side_effect=probe),
                patch.object(main_scraper, "process_movie_parallel_pipeline", pipeline),
                patch.object(main_scraper, "load_failed_repairs", return_value={}),
                patch.object(main_scraper, "save_failed_repairs"),
                patch.object(main_scraper, "record_failed_repair") as record_failure,
            ):
                summary = await main_scraper.repair_dead_links(
                    "Test",
                    config,
                    respect_cooldown=False,
                    target_source_urls={source_url},
                    force_refresh_source_urls={source_url},
                    expected_source_counts={source_url: 2},
                )

            self.assertEqual(summary["updated"], 0)
            self.assertEqual(summary["unchanged"], 1)
            self.assertEqual(summary["failed"], 0)
            self.assertEqual(json_path.read_bytes(), original_json)
            self.assertFalse(Path(config["m3u"]).exists())
            record_failure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
