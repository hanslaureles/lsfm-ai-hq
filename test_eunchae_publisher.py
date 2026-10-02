"""
Tests for the Eunchae status aggregator (4B-4). Hermetic: temp dirs, fixed clock,
injected vitals. Run: python -m pytest -q test_eunchae_publisher.py
"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import eunchae_publisher as pub

NOW = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
VITALS = lambda: {"cpu_pct": 12.5, "ram_pct": 61.0, "disk_pct": 70.2}  # noqa: E731


class Fixture(unittest.TestCase):

    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.health, self.results, self.memory = root / "health", root / "results", root / "memory"
        for d in (self.health, self.results, self.memory):
            d.mkdir()
        self.enterContext(mock.patch("builtins.print"))

    def beat(self, bot, age_s, status="online", **extra):
        ts = (NOW - timedelta(seconds=age_s)).isoformat(timespec="seconds")
        (self.health / f"{bot}.json").write_text(json.dumps({"bot": bot, "status": status, "timestamp_utc": ts,
                                                             "pid": 4242, **extra}), encoding="utf-8")

    def bench(self, name, p50, p95, n, mock_run=False):
        report = {"date": name[:10], "mock": mock_run, "overall": {"n_ok": n, "total_ms": {"p50": p50, "p95": p95}}}
        (self.results / name).write_text(json.dumps(report), encoding="utf-8")

    def payload(self):
        return pub.generate_status_payload(now=NOW, health_dir=self.health, results_dir=self.results,
                                           applications_log=self.memory / "applications_log.md", vitals=VITALS)


class TestSwarm(Fixture):

    def test_fresh_stale_missing_not_ready_and_unreadable(self):
        self.beat("sakura", 30)
        self.beat("chaewon", pub.STALE_AFTER_S + 1)          # online in the file, but too old
        self.beat("yunjin", 30, status="not_ready")
        (self.health / "kazuha.json").write_text("{not json", encoding="utf-8")
        swarm = self.payload()["swarm"]                       # eunchae has no file at all
        self.assertEqual({a: s["status"] for a, s in swarm.items()},
                         {"sakura": "online", "chaewon": "stale", "yunjin": "not_ready",
                          "kazuha": "unknown", "eunchae": "offline"})
        self.assertEqual(swarm["sakura"]["last_heartbeat_utc"], "2026-10-02T11:59:30+00:00")
        self.assertIsNone(swarm["eunchae"]["last_heartbeat_utc"])

    def test_a_heartbeat_from_the_future_is_never_online(self):
        # Heartbeats are written on this machine with seconds precision (truncated), so a real
        # one is never ahead of the publisher's clock. Any future time is skew or a forged file.
        for ahead in (1, 30, 600):
            with self.subTest(ahead=ahead):
                self.beat("sakura", -ahead)
                self.assertEqual(self.payload()["swarm"]["sakura"]["status"], "unknown")

    def test_a_timestamp_without_a_timezone_is_unknown_not_a_crash(self):
        (self.health / "sakura.json").write_text(json.dumps({"status": "online",
                                                             "timestamp_utc": "2026-10-02T11:59:30"}), encoding="utf-8")
        self.assertEqual(self.payload()["swarm"]["sakura"]["status"], "unknown")

    def test_only_allowlisted_fields_leave_the_heartbeat_file(self):
        self.beat("sakura", 10, last_event_type="D:\\private\\notes", pid=1, token="abc")
        self.assertEqual(set(self.payload()["swarm"]["sakura"]), {"status", "last_heartbeat_utc"})


class TestMetrics(Fixture):

    def test_latency_comes_from_the_newest_real_bench_run(self):
        self.bench("2026-09-30-telemetry.json", 400.0, 900.0, 50)
        self.bench("2026-10-02-telemetry.json", 312.0, 1200.0, 50)
        self.bench("2026-10-03-telemetry.json", 1.0, 1.0, 10, mock_run=True)  # never published
        lat = self.payload()["llm_latency"]
        self.assertEqual(lat, {"p50_ms": 312.0, "p95_ms": 1200.0, "sample_count": 50, "measured_on": "2026-10-02",
                               "source": "bench/results/2026-10-02-telemetry.json"})

    def test_no_bench_run_means_null_not_zero(self):
        self.assertIsNone(self.payload()["llm_latency"])

    def test_application_packages_counted_over_seven_days(self):
        rows = ["# Job Applications Tracker", "", "| # | Date | Company |", "|---|---|---|", "| - | - | - |",
                "| 2026-10-01 | Acme | Role |", "| 2026-09-26 | Beta | Role |", "| 2026-09-20 | Old | Role |"]
        (self.memory / "applications_log.md").write_text("\n".join(rows), encoding="utf-8")
        missions = self.payload()["missions"]
        self.assertEqual((missions["application_packages"], missions["window_days"]), (2, 7))

    def test_missing_applications_log_means_null(self):
        self.assertIsNone(self.payload()["missions"]["application_packages"])

    def test_vitals_are_three_rounded_percentages(self):
        self.assertEqual(self.payload()["vitals"], {"cpu_pct": 12.5, "ram_pct": 61.0, "disk_pct": 70.2,
                                                    "sampled_at": "2026-10-02T12:00:00+00:00"})


class TestValidation(Fixture):

    def test_a_real_payload_passes_and_is_small(self):
        self.beat("sakura", 10)
        self.bench("2026-10-02-telemetry.json", 312.0, 1200.0, 50)
        payload = self.payload()
        pub.validate_status(payload)
        self.assertLess(len(json.dumps(payload)), pub.MAX_BYTES)

    def test_rejects_leaks_extra_keys_and_bad_statuses(self):
        good = self.payload()
        latency = {"p50_ms": 1.0, "p95_ms": 2.0, "sample_count": 3, "measured_on": "2026-10-02"}
        bad_cases = {
            # Structurally valid, so only the leak scan can catch these three.
            "windows path": lambda p: p.update(llm_latency={**latency, "source": "C:\\bench\\x.json"}),
            "user name": lambda p: p.update(llm_latency={**latency, "source": "bench/results/hansl.json"}),
            "secret word": lambda p: p["missions"].update(source="memory/token.txt"),
            # Codex 4B FIX: free text and a POSIX path passed the key and leak checks.
            "free-text latency": lambda p: p.update(llm_latency={**latency, "p50_ms": "Jane Doe",
                                                                 "source": "bench/results/2026-10-02-telemetry.json"}),
            "posix source path": lambda p: p.update(llm_latency={**latency, "source": "/home/private/report.json"}),
            "other bench file": lambda p: p.update(llm_latency={**latency, "source": "bench/results/notes.json"}),
            "negative latency": lambda p: p.update(llm_latency={**latency, "p50_ms": -1,
                                                                "source": "bench/results/2026-10-02-telemetry.json"}),
            "p50 above p95": lambda p: p.update(llm_latency={**latency, "p50_ms": 9.0,
                                                             "source": "bench/results/2026-10-02-telemetry.json"}),
            "bool count": lambda p: p.update(llm_latency={**latency, "sample_count": True,
                                                          "source": "bench/results/2026-10-02-telemetry.json"}),
            "bad date": lambda p: p.update(llm_latency={**latency, "measured_on": "yesterday",
                                                        "source": "bench/results/2026-10-02-telemetry.json"}),
            "text count": lambda p: p["missions"].update(application_packages="many"),
            "negative count": lambda p: p["missions"].update(application_packages=-2),
            "other window": lambda p: p["missions"].update(window_days=30),
            "other mission source": lambda p: p["missions"].update(source="/var/log/x"),
            "vitals over 100": lambda p: p["vitals"].update(cpu_pct=250),
            "vitals as text": lambda p: p["vitals"].update(ram_pct="high"),
            "free-text heartbeat": lambda p: p["swarm"]["sakura"].update(last_heartbeat_utc="Jane Doe"),
            "naive timestamp": lambda p: p.update(generated_at="2026-10-02T12:00:00"),
            "extra key": lambda p: p.update(extra=1),
            "bad status": lambda p: p["swarm"]["sakura"].update(status="ONLINE 100%"),
            "unknown agent": lambda p: p["swarm"].update(ciel={"status": "online", "last_heartbeat_utc": None}),
        }
        for name, corrupt in bad_cases.items():
            with self.subTest(name):
                p = json.loads(json.dumps(good))
                corrupt(p)
                with self.assertRaises(ValueError):
                    pub.validate_status(p)


class TestPublish(Fixture):

    def test_publish_writes_valid_json_and_dry_run_writes_nothing(self):
        target = self.memory / "out" / "status.json"
        payload = self.payload()
        with mock.patch.object(pub, "generate_status_payload", lambda: payload):
            pub.main(["--dry-run", "--target", str(target)])
            self.assertFalse(target.exists())
            pub.main(["--target", str(target)])
        data = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], pub.SCHEMA_VERSION)
        self.assertEqual([p.name for p in target.parent.iterdir()], ["status.json"])  # no temp file left


if __name__ == "__main__":
    unittest.main()
