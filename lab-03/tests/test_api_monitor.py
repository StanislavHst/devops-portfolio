import errno
import json
import logging
import socket
import ssl
import unittest
from unittest.mock import patch
from urllib.error import URLError

import api_monitor


class FakeResponse:
    def __init__(self, status: int):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def getcode(self):
        return self.status


def target(**overrides):
    value = {
        "name": "test",
        "url": "https://example.com/health",
        "expected_status": 200,
        "timeout_seconds": 1,
        "response_time_threshold_seconds": 1,
    }
    value.update(overrides)
    return value


class ApiMonitorTests(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger("api_monitor_test")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())

    def test_classifies_dns_error(self):
        error = URLError(socket.gaierror(socket.EAI_NONAME, "name not known"))
        self.assertEqual(api_monitor.classify_network_error(error), "dns")

    def test_classifies_connection_refused(self):
        error = URLError(ConnectionRefusedError(errno.ECONNREFUSED, "refused"))
        self.assertEqual(
            api_monitor.classify_network_error(error), "connection_refused"
        )

    def test_classifies_timeout(self):
        self.assertEqual(
            api_monitor.classify_network_error(URLError(socket.timeout("timed out"))),
            "timeout",
        )

    def test_classifies_certificate_error(self):
        error = URLError(ssl.SSLCertVerificationError("certificate verify failed"))
        self.assertEqual(api_monitor.classify_network_error(error), "certificate")

    @patch("api_monitor.urlopen", return_value=FakeResponse(503))
    def test_unexpected_status_is_not_retried(self, mocked_urlopen):
        result = api_monitor.check_target(target(), self.logger)
        self.assertEqual(result["failure_type"], "unexpected_status")
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(mocked_urlopen.call_count, 1)

    @patch("api_monitor.urlopen")
    def test_network_failure_is_retried_once(self, mocked_urlopen):
        mocked_urlopen.side_effect = URLError(
            socket.gaierror(socket.EAI_NONAME, "name not known")
        )
        result = api_monitor.check_target(target(), self.logger)
        self.assertEqual(result["failure_type"], "dns")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(mocked_urlopen.call_count, 2)

    @patch("api_monitor.perf_counter", side_effect=[0.0, 1.5])
    @patch("api_monitor.urlopen", return_value=FakeResponse(200))
    def test_slow_response_is_separate_failure(self, _urlopen, _clock):
        result = api_monitor.request_once(
            target(response_time_threshold_seconds=1), attempt=1
        )
        self.assertEqual(result["failure_type"], "slow_response")
        self.assertEqual(result["http_status"], 200)

    @patch("api_monitor.urlopen", return_value=FakeResponse(204))
    def test_discord_notification_contains_failure_type(self, mocked_urlopen):
        result = api_monitor.result_record(
            target(),
            status="problem",
            response_time_ms=10,
            attempts=2,
            failure_type="dns",
            message="DNS failure",
        )
        sent = api_monitor.send_discord_notification(
            "https://discord.example/webhook", result, self.logger
        )
        self.assertTrue(sent)
        request = mocked_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertIn("Тип збою: dns", payload["content"])


if __name__ == "__main__":
    unittest.main()
