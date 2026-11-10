"""Validation, orchestration, reports, notifications and real HTTP transport."""
import contextlib
import io
import json
import logging
import socket
import ssl
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import api_monitor as monitor
from test_api_monitor import FakeResponse, target


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "targets.json"

    def load(self, value):
        self.path.write_text(json.dumps(value), encoding="utf-8")
        return monitor.load_targets(self.path)

    def invalid(self, **changes):
        with self.assertRaises(ValueError):
            self.load({"targets": [target(**changes)]})

    def test_valid_config(self):
        self.assertEqual(self.load({"targets": [target()]}), [target()])

    def test_missing_file(self):
        with self.assertRaisesRegex(ValueError, "не існує"):
            monitor.load_targets(self.path)

    def test_malformed_json(self):
        self.path.write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "JSON"):
            monitor.load_targets(self.path)

    def test_invalid_encoding(self):
        self.path.write_bytes(b"\xff")
        with self.assertRaises(ValueError):
            monitor.load_targets(self.path)

    def test_missing_targets(self):
        with self.assertRaises(ValueError):
            self.load({})

    def test_empty_targets(self):
        with self.assertRaises(ValueError):
            self.load({"targets": []})

    def test_non_object_target(self):
        with self.assertRaises(ValueError):
            self.load({"targets": ["bad"]})

    def test_missing_required_fields(self):
        for field in target():
            with self.subTest(field=field), self.assertRaises(ValueError):
                value = target()
                del value[field]
                self.load({"targets": [value]})

    def test_blank_name(self):
        self.invalid(name=" ")

    def test_invalid_urls(self):
        for url in [None, "ftp://host/file", "http://", "https:///health",
                    "http://host:abc", "http://host:99999", "http://host:0",
                    "http://name:secret@host/"]:
            with self.subTest(url=url):
                self.invalid(url=url)

    def test_status_out_of_range(self):
        for status in [99, 600, "200", True, 200.5]:
            with self.subTest(status=status):
                self.invalid(expected_status=status)

    def test_invalid_timeouts(self):
        for value in [0, -1, True, "5", None, float("nan"), float("inf")]:
            with self.subTest(value=value):
                self.invalid(timeout_seconds=value)

    def test_invalid_thresholds(self):
        for value in [0, -1, True, "5", None, float("nan"), float("inf")]:
            with self.subTest(value=value):
                self.invalid(response_time_threshold_seconds=value)

    def test_duplicate_names(self):
        with self.assertRaises(ValueError):
            self.load({"targets": [target(), target()]})

    def test_expected_json_must_be_object(self):
        for value in [[], "healthy", None]:
            with self.subTest(value=value):
                self.invalid(expected_json=value)

    def test_wishlist_config_has_health_dependencies(self):
        config = Path(__file__).resolve().parents[1] / "config/api-monitor.json"
        targets = monitor.load_targets(config)
        self.assertEqual(len(targets), 5)
        health = next(t for t in targets if t["name"] == "wishlist-health")
        self.assertEqual(health["expected_json"], {"status": "healthy", "redis": "healthy"})
        self.assertTrue(all(t["url"].startswith("http://127.0.0.1:8000/") for t in targets))


class BehaviorTests(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger(self.id())
        self.logger.addHandler(logging.NullHandler())

    def test_generic_network_error_classification(self):
        self.assertEqual(monitor.classify_network_error(URLError(OSError("reset"))), "network_error")

    def test_exception_chain_handles_cycle(self):
        error = URLError("cycle")
        error.__cause__ = error
        self.assertEqual(monitor.exception_chain(error), [error])

    def test_nested_certificate_cause(self):
        outer = OSError("outer")
        outer.__cause__ = ssl.SSLCertVerificationError("expired")
        self.assertEqual(monitor.classify_network_error(outer), "certificate")

    def test_recovery_after_network_failure(self):
        with patch("api_monitor.urlopen", side_effect=[URLError(socket.timeout()), FakeResponse(200)]) as request:
            result = monitor.check_target(target(), self.logger)
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(request.call_count, 2)

    def test_all_network_failures_retry_once(self):
        for error in [socket.gaierror(), ConnectionRefusedError(), TimeoutError(),
                      ssl.SSLCertVerificationError("expired"), OSError("reset")]:
            with self.subTest(error=type(error).__name__), patch("api_monitor.urlopen", side_effect=URLError(error)) as request:
                result = monitor.check_target(target(), self.logger)
                self.assertEqual(request.call_count, 2)
                self.assertEqual(result["attempts"], 2)

    def test_expected_401_is_healthy(self):
        error = HTTPError("http://host/users/me", 401, "Unauthorized", {}, None)
        with patch("api_monitor.urlopen", side_effect=error):
            result = monitor.check_target(target(expected_status=401), self.logger)
        self.assertEqual(result["status"], "healthy")

    def test_threshold_boundary_is_healthy(self):
        with patch("api_monitor.urlopen", return_value=FakeResponse(200)), patch("api_monitor.perf_counter", side_effect=[0, 1]):
            result = monitor.request_once(target(), 1)
        self.assertEqual(result["status"], "healthy")

    def test_status_error_takes_priority_over_slow(self):
        with patch("api_monitor.urlopen", return_value=FakeResponse(500)), patch("api_monitor.perf_counter", side_effect=[0, 2]):
            result = monitor.request_once(target(), 1)
        self.assertEqual(result["failure_type"], "unexpected_status")

    def test_report_contains_summary_and_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested/report.json"
            records = [monitor.result_record(target(), status="healthy", response_time_ms=1.234, attempts=1, message="ok")]
            monitor.write_report(output, "start", records)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["summary"], {"total": 1, "healthy": 1, "problems": 0})
            self.assertIn("run_finished_at", report)
            self.assertIn("timestamp", report["results"][0])
            self.assertEqual(report["results"][0]["response_time_ms"], 1.23)
            self.assertFalse(output.with_suffix(".json.tmp").exists())

    def test_report_replaces_previous_result(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            output.write_text("old", encoding="utf-8")
            monitor.write_report(output, "start", [])
            self.assertEqual(json.loads(output.read_text())["results"], [])

    def test_discord_http_failure(self):
        with patch("api_monitor.urlopen", side_effect=HTTPError("http://secret", 403, "Forbidden", {}, None)):
            self.assertFalse(monitor.send_discord_notification("https://discord.example/hook", self.problem(), self.logger))

    def test_discord_network_failure(self):
        with patch("api_monitor.urlopen", side_effect=URLError(TimeoutError())):
            self.assertFalse(monitor.send_discord_notification("https://discord.example/hook", self.problem(), self.logger))

    def test_discord_invalid_url_does_not_crash(self):
        self.assertFalse(monitor.send_discord_notification("bad", self.problem(), self.logger))

    def problem(self):
        return monitor.result_record(target(), status="problem", response_time_ms=1, attempts=2, failure_type="dns", message="DNS")

    def test_discord_payload_and_headers(self):
        with patch("api_monitor.urlopen", return_value=FakeResponse(204)) as request:
            self.assertTrue(monitor.send_discord_notification("https://discord.example/hook", self.problem(), self.logger))
        req = request.call_args.args[0]
        payload = json.loads(req.data)
        self.assertIn("Тип збою: dns", payload["content"])
        self.assertEqual(payload["allowed_mentions"], {"parse": []})
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("User-agent"), "DevOpsLabMonitor/1.0")

    def test_discord_limits_message_size(self):
        problem = self.problem()
        problem["message"] = "x" * 3000
        with patch("api_monitor.urlopen", return_value=FakeResponse(204)) as request:
            monitor.send_discord_notification("https://discord.example/hook", problem, self.logger)
        self.assertLessEqual(len(json.loads(request.call_args.args[0].data)["content"]), 2000)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.config = root / "config.json"
        self.output = root / "report.json"
        self.log = root / "monitor.log"
        self.config.write_text(json.dumps({"targets": [target()]}), encoding="utf-8")
        self.args = ["--config", str(self.config), "--output", str(self.output), "--log-file", str(self.log)]
        self.env_patch = patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": ""})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.addCleanup(self.close_logger)

    @staticmethod
    def close_logger():
        logger = logging.getLogger("api_monitor")
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()

    def test_cli_success(self):
        with patch("api_monitor.urlopen", return_value=FakeResponse(200)):
            self.assertEqual(monitor.main(self.args), 0)
        self.assertEqual(json.loads(self.output.read_text(encoding="utf-8"))["summary"]["healthy"], 1)
        self.assertIn("action=monitor_finish result=success", self.log.read_text(encoding="utf-8"))

    def test_cli_problem_and_no_webhook(self):
        with patch("api_monitor.urlopen", return_value=FakeResponse(500)):
            self.assertEqual(monitor.main(self.args), 1)
        self.assertIn("notification=skipped", self.log.read_text(encoding="utf-8"))

    def test_cli_missing_config(self):
        self.config.unlink()
        self.assertEqual(monitor.main(self.args), 2)
        self.assertIn("action=config_load result=error", self.log.read_text(encoding="utf-8"))

    def test_cli_output_error(self):
        with patch("api_monitor.urlopen", return_value=FakeResponse(200)), patch("api_monitor.write_report", side_effect=OSError("disk full")):
            self.assertEqual(monitor.main(self.args), 3)

    def test_cli_log_error(self):
        with patch("api_monitor.setup_logger", side_effect=OSError("permission denied")), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(monitor.main(self.args), 3)

    def test_cli_sends_each_problem(self):
        self.config.write_text(json.dumps({"targets": [target(name="one"), target(name="two")]}))
        with patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": "https://discord.example/hook"}), patch("api_monitor.urlopen", return_value=FakeResponse(503)), patch("api_monitor.send_discord_notification", return_value=True) as send:
            self.assertEqual(monitor.main(self.args), 1)
        self.assertEqual(send.call_count, 2)

    def test_cli_healthy_does_not_notify(self):
        with patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": "https://discord.example/hook"}), patch("api_monitor.urlopen", return_value=FakeResponse(200)), patch("api_monitor.send_discord_notification") as send:
            self.assertEqual(monitor.main(self.args), 0)
        send.assert_not_called()

    def test_cli_no_notify_option(self):
        with patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": "https://discord.example/hook"}), patch("api_monitor.urlopen", return_value=FakeResponse(500)), patch("api_monitor.send_discord_notification") as send:
            self.assertEqual(monitor.main(self.args + ["--no-notify"]), 1)
        send.assert_not_called()
        self.assertIn("notification=disabled", self.log.read_text(encoding="utf-8"))

    def test_help_contains_example_and_parameters(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as exc:
            monitor.main(["--help"])
        self.assertEqual(exc.exception.code, 0)
        for flag in ["--config", "--output", "--log-file", "--webhook-env", "--no-notify", "Приклад"]:
            self.assertIn(flag, output.getvalue())


class HttpTransportTests(unittest.TestCase):
    """Real loopback HTTP: no external services and no Discord side effects."""

    @classmethod
    def setUpClass(cls):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                status = 401 if self.path == "/users/me" else 200
                if self.path == "/missing":
                    status = 404
                if self.path == "/slow":
                    time.sleep(0.15)
                self.send_response(status)
                self.end_headers()
                body = b'{"status":"healthy","redis":"healthy"}'
                if self.path == "/redis-down":
                    body = b'{"status":"healthy","redis":"unhealthy"}'
                if self.path == "/invalid-json":
                    body = b'not json'
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def check(self, path="/health", **changes):
        return monitor.check_target(target(url=f"http://127.0.0.1:{self.server.server_port}{path}", **changes), logging.getLogger(self.id()))

    def test_real_http_healthy(self):
        self.assertEqual(self.check()["status"], "healthy")

    def test_real_http_expected_auth_rejection(self):
        self.assertEqual(self.check("/users/me", expected_status=401)["status"], "healthy")

    def test_real_http_unexpected_status(self):
        self.assertEqual(self.check("/missing")["failure_type"], "unexpected_status")

    def test_real_http_slow_response(self):
        result = self.check("/slow", response_time_threshold_seconds=0.05)
        self.assertEqual(result["failure_type"], "slow_response")
        self.assertEqual(result["attempts"], 1)

    def test_real_http_timeout(self):
        result = self.check("/slow", timeout_seconds=0.02)
        self.assertEqual(result["failure_type"], "timeout")
        self.assertEqual(result["attempts"], 2)

    def test_real_http_json_health(self):
        self.assertEqual(self.check(expected_json={"redis": "healthy"})["status"], "healthy")

    def test_real_http_detects_unhealthy_redis_despite_200(self):
        result = self.check("/redis-down", expected_json={"redis": "healthy"})
        self.assertEqual(result["failure_type"], "unexpected_body")
        self.assertEqual(result["http_status"], 200)

    def test_real_http_invalid_json(self):
        self.assertEqual(self.check("/invalid-json", expected_json={})["failure_type"], "unexpected_body")

    def test_real_http_missing_json_field(self):
        self.assertEqual(self.check(expected_json={"missing": True})["failure_type"], "unexpected_body")


if __name__ == "__main__":
    unittest.main()
