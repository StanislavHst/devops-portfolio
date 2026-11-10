"""Read-only integration tests against the running Wishlist Docker project.

Enable with WISHLIST_LIVE=1. Default test runs remain independent of Docker.
"""
import json
import logging
import os
import unittest
from pathlib import Path
from urllib.request import urlopen

import api_monitor


@unittest.skipUnless(os.getenv("WISHLIST_LIVE") == "1", "requires running Wishlist and WISHLIST_LIVE=1")
class WishlistLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config = Path(__file__).resolve().parents[1] / "config/api-monitor.json"
        cls.targets = {t["name"]: t for t in api_monitor.load_targets(config)}

    def result(self, name):
        return api_monitor.check_target(self.targets[name], logging.getLogger(self.id()))

    def test_health_includes_healthy_redis(self):
        self.assertEqual(self.result("wishlist-health")["status"], "healthy")

    def test_public_wishlists_is_json_array(self):
        item = self.targets["wishlist-list-postgres-redis"]
        with urlopen(item["url"], timeout=item["timeout_seconds"]) as response:
            self.assertEqual(response.status, 200)
            self.assertIsInstance(json.load(response), list)

    def test_openapi_describes_real_wishlist_routes(self):
        item = self.targets["wishlist-openapi"]
        with urlopen(item["url"], timeout=item["timeout_seconds"]) as response:
            schema = json.load(response)
        self.assertEqual(schema["info"]["title"], "Wish List API")
        self.assertIn("/wishlists", schema["paths"])
        self.assertIn("/health", schema["paths"])

    def test_swagger_available(self):
        self.assertEqual(self.result("wishlist-swagger")["status"], "healthy")

    def test_user_profile_requires_authentication(self):
        result = self.result("wishlist-auth-guard")
        self.assertEqual(result["http_status"], 401)
        self.assertEqual(result["status"], "healthy")
