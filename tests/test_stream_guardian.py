import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import main_scraper
import stream_guardian


class StreamProbeTests(unittest.TestCase):
    def test_final_verdict_separates_dead_repairs_from_quarantine_restores(self):
        report = {
            "mode": "apply",
            "confirmed_dead": 0,
            "catalogue_updates": 0,
            "dead_links_removed": 0,
            "repair_batch_selected": 0,
            "restored_movies": 1,
            "restored_movie_details": [{
                "category": "Hindi Movies",
                "movie": "Dug Dug",
                "year": 2026,
                "source": "cinefreak.net/dug-dug-2026-full-movie-download",
                "validated_streams": 1,
            }],
            "changed_files": [
                "categories/Hindi_Movies/hindi_movies.json",
                "history/link_guardian_state.json",
            ],
        }

        verdict = stream_guardian.build_final_verdict(report)

        self.assertEqual(verdict["dead_links_found"], 0)
        self.assertEqual(verdict["dead_link_repair"], "not_needed")
        self.assertEqual(verdict["quarantine_restores"], 1)
        self.assertEqual(verdict["restored_movies"][0]["movie"], "Dug Dug")
        self.assertEqual(verdict["category_files_changed"], 1)
        self.assertEqual(verdict["state_files_changed"], 1)
        self.assertEqual(verdict["publish_handoff"], "pending_workflow_publish_step")

    def test_category_scan_results_include_files_and_per_category_health(self):
        alive_url = "https://cdn.example.com/alive.mkv"
        dead_url = "https://cdn.example.com/dead.mkv"
        config = {
            "dir": "categories/Test",
            "file": "categories/Test/movies.txt",
            "json": "categories/Test/movies.json",
            "m3u": "categories/Test/movies.m3u",
            "slug": "test",
        }
        catalog = {
            "Test": {
                "config": config,
                "movies": [{
                    "name": "Example Movie",
                    "source_url": "https://example.com/movie",
                    "res_list": [
                        {"link": alive_url},
                        {"link": dead_url},
                    ],
                }],
            }
        }
        probes = {
            alive_url: stream_guardian.ProbeResult(alive_url, "alive", "media_bytes_ok", 206),
            dead_url: stream_guardian.ProbeResult(dead_url, "dead", "http_404", 404),
        }

        results = stream_guardian.build_category_scan_results(
            catalog,
            probes,
            {dead_url: "http_404"},
        )
        details = stream_guardian.build_dead_link_details(
            {dead_url: "http_404"},
            probes,
            {
                dead_url: [{
                    "category": "Test",
                    "movie_name": "Example Movie",
                    "source_url": "https://example.com/movie",
                }]
            },
        )

        self.assertEqual(results[0]["files"]["scan_input"], "categories/Test/movies.json")
        self.assertEqual(results[0]["alive"], 1)
        self.assertEqual(results[0]["confirmed_dead"], 1)
        self.assertEqual(results[0]["affected_movies"], 1)
        self.assertEqual(details[0]["category"], "Test")
        self.assertEqual(details[0]["reason"], "http_404")
        self.assertNotIn(dead_url, json.dumps(details))

    def test_dead_link_batch_is_capped_at_fifteen_and_excludes_refresh_work(self):
        queue = {
            "test::one": {
                "category": "Test",
                "source_url": "https://example.com/one",
                "kind": "dead",
                "dead_links": [f"https://cdn.example.com/one-{index}.mkv" for index in range(10)],
                "discovered_at": "2026-08-31T00:00:00+00:00",
                "attempt_count": 0,
            },
            "test::two": {
                "category": "Test",
                "source_url": "https://example.com/two",
                "kind": "dead",
                "dead_links": [f"https://cdn.example.com/two-{index}.mkv" for index in range(10)],
                "discovered_at": "2026-08-31T00:01:00+00:00",
                "attempt_count": 0,
            },
            "test::refresh": {
                "category": "Test",
                "source_url": "https://example.com/refresh",
                "kind": "refresh",
                "dead_links": [],
                "discovered_at": "2026-08-31T00:02:00+00:00",
                "attempt_count": 0,
            },
        }

        batch = stream_guardian.select_repair_batch(queue)

        self.assertEqual(sum(len(entry.get("dead_links", [])) for entry in batch.values()), 15)
        self.assertTrue(all(entry["kind"] == "dead" for entry in batch.values()))
        self.assertEqual(len(batch["test::one"]["dead_links"]), 10)
        self.assertEqual(len(batch["test::two"]["dead_links"]), 5)

    def test_repair_batch_only_selects_requested_categories(self):
        queue = {
            "other::dead": {
                "category": "Other",
                "source_url": "https://example.com/other",
                "kind": "dead",
                "dead_links": ["https://cdn.example.com/dead.mkv"],
                "discovered_at": "2026-08-30T00:00:00+00:00",
                "attempt_count": 0,
            },
            "test::dead": {
                "category": "Test",
                "source_url": "https://example.com/test",
                "kind": "dead",
                "dead_links": ["https://cdn.example.com/test-dead.mkv"],
                "discovered_at": "2026-08-31T00:00:00+00:00",
                "attempt_count": 0,
            },
        }

        batch = stream_guardian.select_repair_batch(queue, category_names=["Test"])

        self.assertEqual(set(batch), {"test::dead"})


    def test_unchanged_source_gap_backs_off_but_changed_counts_retry_immediately(self):
        now = datetime(2026, 8, 31, tzinfo=timezone.utc)
        entry = stream_guardian.schedule_source_gap(
            "Test",
            "https://example.com/movie",
            advertised_count=4,
            stored_count=1,
            now=now,
        )

        self.assertFalse(
            stream_guardian.source_gap_retry_due(entry, 4, 1, now=now + timedelta(hours=1))
        )
        self.assertTrue(
            stream_guardian.source_gap_retry_due(entry, 5, 1, now=now + timedelta(hours=1))
        )
        self.assertTrue(
            stream_guardian.source_gap_retry_due(entry, 4, 2, now=now + timedelta(hours=1))
        )
        self.assertTrue(
            stream_guardian.source_gap_retry_due(entry, 4, 1, now=now + timedelta(hours=6))
        )

    def test_source_html_extractor_counts_only_target_watch_buttons(self):
        html = """
        <h4 class="movie-title">Alpha HEVC 720p</h4>
        <div><a class="dlbtn dlbtn-watch" href="https://example.com/720">Watch Online</a></div>
        <h4 class="movie-title">Alpha HEVC 1080p</h4>
        <div><a class="dlbtn dlbtn-watch" href="https://example.com/hevc">Watch Online</a></div>
        <h4 class="movie-title">Alpha HD 1080p</h4>
        <div><a class="dlbtn dlbtn-download" href="https://example.com/download">Download</a>
        <a class="dlbtn dlbtn-watch" href="https://example.com/hd">Watch Online</a></div>
        <h4 class="movie-title">Alpha 4K-2160p SDR HEVC</h4>
        <div><a class="dlbtn dlbtn-watch" href="https://example.com/4k">Watch Online</a></div>
        """
        self.assertEqual(
            stream_guardian.target_option_urls_from_html(html),
            ["https://example.com/hevc", "https://example.com/hd", "https://example.com/4k"],
        )

    def test_source_html_extractor_counts_series_watch_grid_not_download_grid(self):
        html = """
        <div class="ep-card">
          <div class="quality-box watch-links"><div class="quality-grid">
            <a href="/generate.php?id=watch720">HD 720p</a>
            <a href="/generate.php?id=watch1080">HD 1080p</a>
          </div></div>
          <div class="quality-box download-links"><div class="quality-grid">
            <a href="/generate.php?id=download1080">HD 1080p</a>
          </div></div>
        </div>
        """
        self.assertEqual(
            stream_guardian.target_option_urls_from_html(html),
            ["/generate.php?id=watch1080"],
        )

    def test_playable_media_probe_is_alive(self):
        with patch.object(
            main_scraper,
            "probe_stream_link_sync",
            return_value={"status": "alive", "reason": "media_bytes_ok", "http_status": 206},
        ):
            result = stream_guardian.probe_stream_once("https://cdn.example.com/movie.mkv")
        self.assertEqual(result.status, "alive")

    def test_403_is_dead_not_alive_for_a_cdn_url(self):
        with patch.object(
            main_scraper,
            "probe_stream_link_sync",
            return_value={"status": "dead", "reason": "http_403", "http_status": 403},
        ):
            result = stream_guardian.probe_stream_once("https://cdn.example.com/movie.mkv")
        self.assertEqual(result.status, "dead")
        self.assertEqual(result.http_status, 403)

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
    async def test_selected_category_repairs_its_work_and_preserves_other_queue(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            category_dir = root / "category"
            category_dir.mkdir()
            state_path = root / "state.json"
            source_url = "https://example.com/test-movie"
            stream_url = "https://cdn.example.com/dead.mkv"
            config = {
                "dir": str(category_dir),
                "file": str(category_dir / "movies.txt"),
                "json": str(category_dir / "movies.json"),
                "m3u": str(category_dir / "movies.m3u"),
                "slug": "test",
            }
            movie = {
                "name": "Test Movie",
                "category": "Test",
                "year": "2026",
                "poster": "N/A",
                "source_url": source_url,
                "res_list": [{"resolution": "HD 1080P", "link": stream_url}],
            }
            main_scraper.save_category_outputs("Test", config, [movie])
            other_queue = {
                f"other::{index}": {
                    "category": "Other",
                    "source_url": f"https://example.com/other-{index}",
                    "kind": "dead",
                    "expected_count": 0,
                    "dead_links": [f"https://cdn.example.com/other-{index}.mkv"],
                    "discovered_at": f"2026-08-30T00:0{index}:00+00:00",
                    "last_seen": f"2026-08-30T00:0{index}:00+00:00",
                    "attempt_count": 0,
                    "last_attempt": "",
                }
                for index in range(3)
            }
            state_path.write_text(
                json.dumps(
                    {
                        "version": 3,
                        "suspects": {},
                        "source_gaps": {},
                        "repair_queue": other_queue,
                    }
                ),
                encoding="utf-8",
            )

            def all_dead(urls, concurrency=stream_guardian.PROBE_CONCURRENCY):
                return {
                    url: stream_guardian.ProbeResult(url, "dead", "http_404", 404)
                    for url in urls
                }

            repair = AsyncMock(return_value={
                "attempted": 1,
                "updated": 0,
                "unchanged": 1,
                "failed": 0,
                "skipped": 0,
                "outcomes": {source_url: "unchanged"},
            })
            with (
                patch.dict(main_scraper.CATEGORIES_MAP, {"Test": config}),
                patch.object(stream_guardian, "ROOT", root),
                patch.object(stream_guardian, "STATE_FILE", state_path),
                patch.object(stream_guardian, "QUARANTINE_FILE", root / "quarantine.json"),
                patch.object(stream_guardian, "probe_many", all_dead),
                patch.object(
                    stream_guardian,
                    "audit_source_completeness",
                    return_value=(
                        {"Test": {source_url: 4}},
                        {"Test": {source_url: 4}},
                        {
                            "source_pages_checked": 1,
                            "source_pages_failed": 0,
                            "source_incomplete_movies": 1,
                        },
                    ),
                ),
                patch.object(main_scraper, "repair_dead_links", repair),
            ):
                exit_code = await stream_guardian.run_guardian(
                    ["Test"], True, root / "report.json"
                )

            report = json.loads((root / "report.json").read_text(encoding="utf-8"))
            state = json.loads(state_path.read_text(encoding="utf-8"))
            repair.assert_awaited_once()
            self.assertEqual(exit_code, 0)
            self.assertEqual(report["repair_batch_selected"], 1)
            self.assertEqual(report["dead_links_batch_selected"], 1)
            self.assertEqual(report["source_refresh_checked"], 0)
            self.assertEqual(state["repair_queue"], other_queue)

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
                patch.object(
                    stream_guardian,
                    "audit_source_completeness",
                    return_value=({}, {}, {"source_pages_checked": 0, "source_pages_failed": 0, "source_incomplete_movies": 0}),
                ),
            ):
                exit_code = await stream_guardian.run_guardian(
                    list(main_scraper.CATEGORIES_LIST), False, report_path
                )

            report = json.loads(report_path.read_text(encoding="utf-8"))
            expected_movies = 0
            expected_stream_entries = 0
            for category in main_scraper.CATEGORIES_LIST:
                payload = json.loads(Path(main_scraper.CATEGORIES_MAP[category]["json"]).read_text(encoding="utf-8"))
                expected_movies += len(payload["movies"])
                expected_stream_entries += sum(len(movie.get("res_list", [])) for movie in payload["movies"])
            self.assertEqual(exit_code, 0)
            self.assertEqual(report["movies"], expected_movies)
            self.assertEqual(report["stream_entries"], expected_stream_entries)
            self.assertEqual(report["alive"], report["unique_streams"])
            self.assertEqual(len(report["category_results"]), len(main_scraper.CATEGORIES_LIST))
            self.assertEqual(len(report["scanned_files"]), len(main_scraper.CATEGORIES_LIST))
            self.assertTrue(all(path.endswith(".json") for path in report["scanned_files"]))
            self.assertEqual(report["changed_files"], [])
            self.assertFalse(report["state_saved"])
            self.assertEqual(report["final_verdict"]["dead_link_repair"], "not_needed")
            self.assertEqual(report["final_verdict"]["publish_handoff"], "not_requested_dry_run")
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
                patch.object(
                    stream_guardian,
                    "audit_source_completeness",
                    return_value=({}, {}, {"source_pages_checked": 0, "source_pages_failed": 0, "source_incomplete_movies": 0}),
                ),
                patch.object(main_scraper, "repair_dead_links", repair),
            ):
                exit_code = await stream_guardian.run_guardian(["Test"], True, root / "report.json")

            self.assertEqual(exit_code, 0)
            kwargs = repair.await_args.kwargs
            self.assertEqual(kwargs["target_source_urls"], {source_url})
            self.assertEqual(kwargs["force_refresh_source_urls"], set())
            self.assertEqual(kwargs["expected_source_counts"], {})
            self.assertEqual(kwargs["target_stream_urls"], {source_url: {dead_url}})
            result = json.loads(Path(config["json"]).read_text(encoding="utf-8"))["movies"][0]
            self.assertEqual(result["res_list"][0]["link"], fresh_url)
            self.assertEqual(result["source_url"], moved_source_url)

    async def test_apply_repairs_fifteen_then_persists_five_for_the_next_run(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            category_dir = root / "category"
            category_dir.mkdir()
            source_url = "https://example.com/twenty-dead-links"
            dead_urls = [f"https://cdn.example.com/dead-{index}.mkv" for index in range(20)]
            config = {
                "dir": str(category_dir),
                "file": str(category_dir / "movies.txt"),
                "json": str(category_dir / "movies.json"),
                "m3u": str(category_dir / "movies.m3u"),
                "slug": "test",
            }
            movie = {
                "name": "Twenty Dead Links",
                "category": "Test",
                "year": "2026",
                "poster": "https://example.com/poster.jpg",
                "source_url": source_url,
                "res_list": [
                    {"resolution": f"Quality {index}", "link": url}
                    for index, url in enumerate(dead_urls)
                ],
            }
            main_scraper.save_category_outputs("Test", config, [movie])

            def all_dead(urls, concurrency=stream_guardian.PROBE_CONCURRENCY):
                return {
                    url: stream_guardian.ProbeResult(url, "dead", "confirmed_http_404", 404)
                    for url in urls
                }

            def failed_summary(category_name, repair_config, **kwargs):
                return {
                    "attempted": 1,
                    "updated": 0,
                    "unchanged": 0,
                    "failed": 1,
                    "skipped": 0,
                    "outcomes": {source_url: "failed"},
                }

            repair = AsyncMock(side_effect=failed_summary)
            with (
                patch.dict(main_scraper.CATEGORIES_MAP, {"Test": config}),
                patch.object(stream_guardian, "ROOT", root),
                patch.object(stream_guardian, "STATE_FILE", root / "state.json"),
                patch.object(stream_guardian, "QUARANTINE_FILE", root / "quarantine.json"),
                patch.object(stream_guardian, "OUTAGE_CIRCUIT_BREAKER_RATIO", 1.1),
                patch.object(stream_guardian, "probe_many", all_dead),
                patch.object(
                    stream_guardian,
                    "audit_source_completeness",
                    return_value=(
                        {},
                        {},
                        {
                            "source_pages_checked": 0,
                            "source_pages_failed": 0,
                            "source_incomplete_movies": 0,
                        },
                    ),
                ),
                patch.object(main_scraper, "repair_dead_links", repair),
            ):
                first_exit = await stream_guardian.run_guardian(
                    ["Test"], True, root / "first-report.json"
                )

            first_targets = repair.await_args.kwargs["target_stream_urls"][source_url]
            first_report = json.loads((root / "first-report.json").read_text(encoding="utf-8"))
            first_state = json.loads((root / "state.json").read_text(encoding="utf-8"))
            remaining_public = json.loads(Path(config["json"]).read_text(encoding="utf-8"))["movies"][0]["res_list"]

            self.assertEqual(first_exit, 0)
            self.assertEqual(len(first_targets), 15)
            self.assertEqual(first_report["dead_links_batch_selected"], 15)
            self.assertEqual(first_report["dead_links_queue_remaining"], 5)
            self.assertEqual(len(remaining_public), 5)
            queued_after_first = next(iter(first_state["repair_queue"].values()))
            self.assertEqual(set(queued_after_first["dead_links"]), set(dead_urls) - first_targets)

            repair.reset_mock()
            with (
                patch.dict(main_scraper.CATEGORIES_MAP, {"Test": config}),
                patch.object(stream_guardian, "ROOT", root),
                patch.object(stream_guardian, "STATE_FILE", root / "state.json"),
                patch.object(stream_guardian, "QUARANTINE_FILE", root / "quarantine.json"),
                patch.object(stream_guardian, "OUTAGE_CIRCUIT_BREAKER_RATIO", 1.1),
                patch.object(stream_guardian, "probe_many", all_dead),
                patch.object(
                    stream_guardian,
                    "audit_source_completeness",
                    return_value=(
                        {},
                        {},
                        {
                            "source_pages_checked": 0,
                            "source_pages_failed": 0,
                            "source_incomplete_movies": 0,
                        },
                    ),
                ),
                patch.object(main_scraper, "repair_dead_links", repair),
            ):
                second_exit = await stream_guardian.run_guardian(
                    ["Test"], True, root / "second-report.json"
                )

            second_targets = repair.await_args.kwargs["target_stream_urls"][source_url]
            second_report = json.loads((root / "second-report.json").read_text(encoding="utf-8"))
            second_state = json.loads((root / "state.json").read_text(encoding="utf-8"))

            self.assertEqual(second_exit, 0)
            self.assertEqual(second_targets, set(dead_urls) - first_targets)
            self.assertEqual(len(second_targets), 5)
            self.assertEqual(second_report["dead_links_batch_selected"], 5)
            self.assertEqual(second_report["dead_links_queue_remaining"], 0)
            self.assertEqual(second_state["repair_queue"], {})
            self.assertEqual(
                json.loads(Path(config["json"]).read_text(encoding="utf-8"))["movies"],
                [],
            )

    async def test_alive_movie_button_gap_does_not_queue_or_repair(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            category_dir = root / "category"
            category_dir.mkdir()
            source_url = "https://example.com/movie"
            stream_url = "https://cdn.example.com/alive.mkv"
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
                "poster": "N/A",
                "source_url": source_url,
                "res_list": [{"resolution": "HD 1080P", "link": stream_url}],
            }
            main_scraper.save_category_outputs("Test", config, [movie])

            def all_alive(urls, concurrency=stream_guardian.PROBE_CONCURRENCY):
                return {
                    url: stream_guardian.ProbeResult(url, "alive", "media_bytes_ok", 206)
                    for url in urls
                }

            repair = AsyncMock()
            with (
                patch.dict(main_scraper.CATEGORIES_MAP, {"Test": config}),
                patch.object(stream_guardian, "ROOT", root),
                patch.object(stream_guardian, "STATE_FILE", root / "state.json"),
                patch.object(stream_guardian, "QUARANTINE_FILE", root / "quarantine.json"),
                patch.object(stream_guardian, "probe_many", all_alive),
                patch.object(
                    stream_guardian,
                    "audit_source_completeness",
                    side_effect=AssertionError("dead-only Guardian must not audit button counts"),
                ),
                patch.object(main_scraper, "repair_dead_links", repair),
            ):
                exit_code = await stream_guardian.run_guardian(["Test"], True, root / "report.json")

            self.assertEqual(exit_code, 0)
            repair.assert_not_awaited()
            report = json.loads((root / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["repair_contract"], "confirmed_dead_only")
            self.assertEqual(report["repair_batch_selected"], 0)
            self.assertEqual(report["source_refresh_due"], 0)
            self.assertEqual(report["source_refresh_checked"], 0)
            self.assertFalse((root / "state.json").exists())

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
                patch.object(
                    stream_guardian,
                    "audit_source_completeness",
                    return_value=({}, {}, {"source_pages_checked": 0, "source_pages_failed": 0, "source_incomplete_movies": 0}),
                ),
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
            self.assertEqual(report["restored_movie_details"][0]["movie"], "Example Movie")
            self.assertEqual(report["restored_movie_details"][0]["category"], "Test")
            self.assertEqual(report["restored_movie_details"][0]["validated_streams"], 1)
            self.assertIn(old_source, (category_dir / "history_skipped.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
