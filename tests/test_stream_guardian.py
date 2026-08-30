import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import main_scraper
import stream_guardian


class StreamProbeTests(unittest.TestCase):
    def test_head_404_is_not_dead_when_range_get_works(self):
        with patch.object(stream_guardian, "request_status", side_effect=[(404, "http_404"), (206, "")]):
            result = stream_guardian.probe_stream_once("https://cdn.example.com/movie.mkv")
        self.assertEqual(result.status, "alive")

    def test_two_404_responses_are_confirmed_dead(self):
        with patch.object(stream_guardian, "request_status", side_effect=[(404, "http_404"), (404, "http_404")]):
            result = stream_guardian.probe_stream_once("https://cdn.example.com/movie.mkv")
        self.assertEqual(result.status, "dead")
        self.assertEqual(result.http_status, 404)

    def test_transient_failure_requires_two_separate_runs(self):
        url = "https://cdn.example.com/movie.mkv"
        result = stream_guardian.ProbeResult(url, "transient", "timeout")
        confirmed, suspects = stream_guardian.classify_probes({url: result}, {"suspects": {}})
        self.assertNotIn(url, confirmed)
        self.assertEqual(suspects[url]["failure_count"], 1)

        confirmed, suspects = stream_guardian.classify_probes(
            {url: result}, {"suspects": suspects}
        )
        self.assertIn(url, confirmed)
        self.assertEqual(suspects[url]["failure_count"], 2)

    def test_alive_result_clears_previous_suspicion(self):
        url = "https://cdn.example.com/movie.mkv"
        state = {"suspects": {url: {"failure_count": 1}}}
        result = stream_guardian.ProbeResult(url, "alive", "head_ok", 200)
        confirmed, suspects = stream_guardian.classify_probes({url: result}, state)
        self.assertEqual(confirmed, {})
        self.assertNotIn(url, suspects)


class GuardianIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_dry_run_audits_catalog_without_changing_state(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            state_path = root / "state.json"
            quarantine_path = root / "quarantine.json"
            report_path = root / "report.json"

            def all_alive(urls, concurrency=stream_guardian.PROBE_CONCURRENCY):
                return {
                    url: stream_guardian.ProbeResult(url, "alive", "test", 200)
                    for url in urls
                }

            with (
                patch.object(stream_guardian, "STATE_FILE", state_path),
                patch.object(stream_guardian, "QUARANTINE_FILE", quarantine_path),
                patch.object(stream_guardian, "probe_many", all_alive),
            ):
                exit_code = await stream_guardian.run_guardian(
                    list(main_scraper.CATEGORIES_LIST), False, report_path
                )

            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(report["movies"], 497)
            self.assertEqual(report["stream_entries"], 1152)
            self.assertEqual(report["alive"], report["unique_streams"])
            self.assertFalse(state_path.exists())
            self.assertFalse(quarantine_path.exists())

    async def test_apply_targets_only_the_affected_source_and_accepts_fresh_link(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            category_dir = root / "category"
            category_dir.mkdir()
            source_url = "https://example.com/movie"
            moved_source_url = "https://example.com/movie-new-page"
            dead_url = "https://cdn.example.com/dead.mkv"
            fresh_url = "https://cdn.example.com/fresh.mkv"
            config = {
                "dir": str(category_dir),
                "file": str(category_dir / "movies.txt"),
                "json": str(category_dir / "movies.json"),
                "m3u": str(category_dir / "movies.m3u"),
                "slug": "test",
            }
            movie = {
                "name": "Example Movie",
                "category": "Test",
                "year": "2026",
                "poster": "https://example.com/poster.jpg",
                "source_url": source_url,
                "res_list": [{"resolution": "HD 1080P", "link": dead_url}],
            }
            main_scraper.save_category_outputs("Test", config, [movie])

            async def fake_repair(category_name, repair_config, **kwargs):
                repaired_movie = {
                    **movie,
                    "source_url": moved_source_url,
                    "res_list": [{"resolution": "HD 1080P", "link": fresh_url}],
                }
                main_scraper.save_category_outputs(category_name, repair_config, [repaired_movie])

            def probe_urls(urls, concurrency=stream_guardian.PROBE_CONCURRENCY):
                return {
                    url: stream_guardian.ProbeResult(
                        url,
                        "dead" if url == dead_url else "alive",
                        "confirmed_http_404" if url == dead_url else "test",
                        404 if url == dead_url else 200,
                    )
                    for url in urls
                }

            repair = AsyncMock(side_effect=fake_repair)
            with (
                patch.dict(main_scraper.CATEGORIES_MAP, {"Test": config}),
                patch.object(stream_guardian, "ROOT", root),
                patch.object(stream_guardian, "STATE_FILE", root / "state.json"),
                patch.object(stream_guardian, "QUARANTINE_FILE", root / "quarantine.json"),
                patch.object(stream_guardian, "OUTAGE_CIRCUIT_BREAKER_RATIO", 1.1),
                patch.object(stream_guardian, "probe_many", probe_urls),
                patch.object(main_scraper, "repair_dead_links", repair),
            ):
                exit_code = await stream_guardian.run_guardian(["Test"], True, root / "report.json")

            self.assertEqual(exit_code, 0)
            kwargs = repair.await_args.kwargs
            self.assertEqual(kwargs["target_source_urls"], {source_url})
            result = json.loads(Path(config["json"]).read_text(encoding="utf-8"))["movies"][0]
            self.assertEqual(result["res_list"][0]["link"], fresh_url)
            self.assertEqual(result["source_url"], moved_source_url)

    async def test_mass_failure_circuit_breaker_blocks_repairs(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            category_dir = root / "category"
            category_dir.mkdir()
            config = {
                "dir": str(category_dir),
                "file": str(category_dir / "movies.txt"),
                "json": str(category_dir / "movies.json"),
                "m3u": str(category_dir / "movies.m3u"),
                "slug": "test",
            }
            movie = {
                "name": "Example Movie",
                "category": "Test",
                "year": "2026",
                "poster": "https://example.com/poster.jpg",
                "source_url": "https://example.com/movie",
                "res_list": [{"resolution": "HD 1080P", "link": "https://cdn.example.com/dead.mkv"}],
            }
            main_scraper.save_category_outputs("Test", config, [movie])
            repair = AsyncMock()

            def all_transient(urls, concurrency=stream_guardian.PROBE_CONCURRENCY):
                return {
                    url: stream_guardian.ProbeResult(url, "transient", "timeout", 0)
                    for url in urls
                }

            with (
                patch.dict(main_scraper.CATEGORIES_MAP, {"Test": config}),
                patch.object(stream_guardian, "ROOT", root),
                patch.object(stream_guardian, "STATE_FILE", root / "state.json"),
                patch.object(stream_guardian, "QUARANTINE_FILE", root / "quarantine.json"),
                patch.object(stream_guardian, "probe_many", all_transient),
                patch.object(main_scraper, "repair_dead_links", repair),
            ):
                exit_code = await stream_guardian.run_guardian(["Test"], True, root / "report.json")

            self.assertEqual(exit_code, 2)
            repair.assert_not_awaited()

    async def test_all_dead_movie_is_preserved_in_quarantine_not_publication(self):
        with tempfile.TemporaryDirectory() as root:
            category_dir = Path(root) / "category"
            category_dir.mkdir()
            source_url = "https://example.com/movie"
            dead_url = "https://cdn.example.com/dead.mkv"
            movie = {
                "name": "Example Movie",
                "category": "Test",
                "year": "2026",
                "poster": "https://example.com/poster.jpg",
                "source_url": source_url,
                "res_list": [{"resolution": "HD 1080P", "link": dead_url}],
            }
            config = {
                "dir": str(category_dir),
                "file": str(category_dir / "movies.txt"),
                "json": str(category_dir / "movies.json"),
                "m3u": str(category_dir / "movies.m3u"),
                "slug": "test",
            }
            main_scraper.save_category_outputs("Test", config, [movie])
            quarantine = {"version": 1, "entries": {}}
            report = {"quarantined_movies": 0, "dead_links_removed": 0}

            def all_dead(urls, concurrency=stream_guardian.PROBE_CONCURRENCY):
                return {
                    url: stream_guardian.ProbeResult(url, "dead", "confirmed_http_404", 404)
                    for url in urls
                }

            with (
                patch.dict(main_scraper.CATEGORIES_MAP, {"Test": config}),
                patch.object(stream_guardian, "probe_many", all_dead),
            ):
                changed = await stream_guardian.clean_after_targeted_repair(
                    "Test", {source_url}, quarantine, report
                )

            payload = json.loads(Path(config["json"]).read_text(encoding="utf-8"))
            self.assertTrue(changed)
            self.assertEqual(payload["movies"], [])
            self.assertEqual(report["quarantined_movies"], 1)
            entry = next(iter(quarantine["entries"].values()))
            self.assertEqual(entry["movie"]["name"], movie["name"])
            self.assertIn(source_url, (category_dir / "history_skipped.txt").read_text(encoding="utf-8"))

    async def test_confirmed_dead_quality_is_removed_while_transient_quality_is_retained(self):
        with tempfile.TemporaryDirectory() as root:
            category_dir = Path(root) / "category"
            category_dir.mkdir()
            source_url = "https://example.com/movie"
            dead_url = "https://cdn.example.com/dead.mkv"
            transient_url = "https://cdn.example.com/slow.mkv"
            config = {
                "dir": str(category_dir),
                "file": str(category_dir / "movies.txt"),
                "json": str(category_dir / "movies.json"),
                "m3u": str(category_dir / "movies.m3u"),
                "slug": "test",
            }
            movie = {
                "name": "Example Movie",
                "category": "Test",
                "year": "2026",
                "poster": "https://example.com/poster.jpg",
                "source_url": source_url,
                "res_list": [
                    {"resolution": "HEVC 1080P", "link": dead_url},
                    {"resolution": "HD 1080P", "link": transient_url},
                ],
            }
            main_scraper.save_category_outputs("Test", config, [movie])
            quarantine = {"version": 1, "entries": {}}
            report = {"quarantined_movies": 0, "dead_links_removed": 0}

            def mixed_results(urls, concurrency=stream_guardian.PROBE_CONCURRENCY):
                return {
                    url: stream_guardian.ProbeResult(
                        url,
                        "dead" if url == dead_url else "transient",
                        "confirmed_http_404" if url == dead_url else "timeout",
                        404 if url == dead_url else 0,
                    )
                    for url in urls
                }

            with (
                patch.dict(main_scraper.CATEGORIES_MAP, {"Test": config}),
                patch.object(stream_guardian, "probe_many", mixed_results),
            ):
                changed = await stream_guardian.clean_after_targeted_repair(
                    "Test", {source_url}, quarantine, report
                )

            result = json.loads(Path(config["json"]).read_text(encoding="utf-8"))["movies"][0]
            self.assertTrue(changed)
            self.assertEqual([item["link"] for item in result["res_list"]], [transient_url])
            self.assertEqual(quarantine["entries"], {})
            self.assertEqual(report["dead_links_removed"], 1)

    async def test_quarantined_movie_can_find_a_moved_page_and_restore(self):
        with tempfile.TemporaryDirectory() as root:
            category_dir = Path(root) / "category"
            category_dir.mkdir()
            old_source = "https://example.com/old-page"
            new_source = "https://example.com/new-page"
            fresh_link = "https://cdn.example.com/fresh.mkv"
            config = {
                "dir": str(category_dir),
                "file": str(category_dir / "movies.txt"),
                "json": str(category_dir / "movies.json"),
                "m3u": str(category_dir / "movies.m3u"),
                "slug": "test",
            }
            (category_dir / "movies.json").write_text(json.dumps({"movies": []}), encoding="utf-8")
            (category_dir / "history_skipped.txt").write_text(old_source + "\n", encoding="utf-8")
            old_movie = {
                "name": "Example Movie",
                "category": "Test",
                "year": "2026",
                "poster": "https://example.com/poster.jpg",
                "source_url": old_source,
                "res_list": [{"resolution": "HD 1080P", "link": "https://cdn.example.com/dead.mkv"}],
            }
            key = stream_guardian.quarantine_key("Test", old_source)
            quarantine = {
                "version": 1,
                "entries": {
                    key: {
                        "category": "Test",
                        "source_url": old_source,
                        "movie": old_movie,
                        "next_retry_at": "",
                        "attempt_count": 1,
                    }
                },
            }
            report = {"restored_movies": 0, "quarantine_retry_failed": 0}
            browser = SimpleNamespace(close=AsyncMock())
            playwright = SimpleNamespace(
                chromium=SimpleNamespace(launch=AsyncMock(return_value=browser))
            )

            class PlaywrightContext:
                async def __aenter__(self):
                    return playwright

                async def __aexit__(self, exc_type, exc, traceback):
                    return False

            def all_alive(urls, concurrency=stream_guardian.PROBE_CONCURRENCY):
                return {
                    url: stream_guardian.ProbeResult(url, "alive", "test", 200)
                    for url in urls
                }

            with (
                patch.dict(main_scraper.CATEGORIES_MAP, {"Test": config}),
                patch.object(main_scraper, "async_playwright", return_value=PlaywrightContext()),
                patch.object(main_scraper, "is_page_url_alive_sync", return_value=False),
                patch.object(main_scraper, "search_movie_on_site", AsyncMock(return_value=new_source)),
                patch.object(
                    main_scraper,
                    "process_movie_parallel_pipeline",
                    AsyncMock(return_value=(
                        new_source,
                        "Example Movie (2026)",
                        "Test",
                        [{"resolution": "HD 1080P", "link": fresh_link}],
                        "N/A",
                    )),
                ),
                patch.object(stream_guardian, "probe_many", all_alive),
            ):
                changed = await stream_guardian.restore_quarantined(quarantine, ["Test"], report)

            restored = json.loads(Path(config["json"]).read_text(encoding="utf-8"))["movies"][0]
            self.assertTrue(changed)
            self.assertEqual(restored["source_url"], new_source)
            self.assertEqual(restored["res_list"][0]["link"], fresh_link)
            self.assertEqual(quarantine["entries"], {})
            self.assertEqual(report["restored_movies"], 1)
            self.assertIn(old_source, (category_dir / "history_skipped.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
