import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from resource_review_bridge.appserver import AppServerClient, AppServerError
from resource_review_bridge.browser import BrowserAcquirer, BrowserAcquisitionError
from resource_review_bridge.reviewer import ResourceReviewer, acquisition_prompt
from resource_review_bridge.schema import PacketValidationError, validate_packet
from resource_review_bridge.server import handle
from resource_review_bridge.state import BusyError, JobStore
from resource_review_bridge.url_policy import URLPolicyError, validate_public_url


URL = "https://www.instagram.com/p/example/"


def packet(url=URL):
    return {
        "status": "completed",
        "source": {"original_url": url, "canonical_url": url, "platform": "instagram"},
        "read_state": {
            "auth_prompt": "completed",
            "original_content": "completed",
            "carousel": "not_applicable",
            "comments": "completed",
            "outbound_links": "not_applicable",
        },
        "summary": "Summary",
        "claims": [{"claim": "Claim", "evidence_ids": ["e1"]}],
        "evidence": [{
            "id": "e1", "kind": "original_post", "text": "Evidence", "url": url,
            "author": None, "published_at": None, "importance": "primary",
        }],
        "limitations": [],
    }


class FakeAppServer:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def run(self, prompt, cwd, output_schema, local_images=None):
        self.calls += 1
        self.prompt = prompt
        return self.value


class FailingAppServer:
    def __init__(self, error):
        self.error = error
        self.calls = 0

    def run(self, prompt, cwd, output_schema, local_images=None):
        self.calls += 1
        raise self.error


class FakeBrowser:
    def acquire(self, url, output_directory):
        return {
            "platform": "instagram",
            "requested_url": url,
            "final_url": url,
            "auth_prompt": "completed",
            "original_content": "completed",
            "carousel": "not_applicable",
            "comments": "partial",
            "outbound_links": "not_applicable",
            "pages": [{"text": "Evidence", "links": [], "screenshot": str(Path(output_directory) / "page-1.png")}],
            "outbound": [],
            "limitations": ["Only visible comments were inspected."],
        }


class URLPolicyTests(unittest.TestCase):
    def test_normalizes_and_removes_fragment(self):
        resolver = lambda *args, **kwargs: [(None, None, None, None, ("93.184.216.34", 443))]
        self.assertEqual(
            validate_public_url("HTTPS://Example.COM/path?q=1#secret", resolver=resolver),
            "https://example.com/path?q=1",
        )

    def test_rejects_private_targets_and_credentials(self):
        for value in ("http://127.0.0.1/x", "http://[::1]/", "file:///tmp/x", "https://u:p@example.com/"):
            with self.subTest(value=value), self.assertRaises(URLPolicyError):
                validate_public_url(value, resolve_dns=False)

    def test_rejects_dns_rebinding_to_private_address(self):
        resolver = lambda *args, **kwargs: [(None, None, None, None, ("10.0.0.2", 443))]
        with self.assertRaises(URLPolicyError):
            validate_public_url("https://example.test/", resolver=resolver)


class SchemaTests(unittest.TestCase):
    def test_accepts_packet_and_checks_references(self):
        self.assertEqual(validate_packet(packet(), URL)["status"], "completed")
        broken = packet()
        broken["claims"][0]["evidence_ids"] = ["missing"]
        with self.assertRaises(PacketValidationError):
            validate_packet(broken, URL)

    def test_completed_requires_original_content(self):
        broken = packet()
        broken["read_state"]["original_content"] = "partial"
        with self.assertRaises(PacketValidationError):
            validate_packet(broken, URL)

    def test_rejects_empty_partial_packet(self):
        broken = packet()
        broken["status"] = "partial"
        broken["read_state"]["original_content"] = "not_attempted"
        broken["claims"] = []
        broken["evidence"] = []
        with self.assertRaises(PacketValidationError):
            validate_packet(broken, URL)

    def test_public_example_matches_schema(self):
        example_path = Path(__file__).parent.parent / "examples/evidence-packet.example.json"
        example = json.loads(example_path.read_text())
        self.assertEqual(validate_packet(example, example["source"]["original_url"])["status"], "completed")


class BrowserConfigurationTests(unittest.TestCase):
    def test_explicit_runtime_paths_are_honored(self):
        environment = {
            "RESOURCE_REVIEW_NODE": "/opt/example/node",
            "RESOURCE_REVIEW_PLAYWRIGHT_PATH": "/opt/example/playwright",
        }
        with patch.dict(os.environ, environment, clear=False):
            browser = BrowserAcquirer()
        self.assertEqual(browser.node, environment["RESOURCE_REVIEW_NODE"])
        self.assertEqual(browser.playwright, environment["RESOURCE_REVIEW_PLAYWRIGHT_PATH"])


class StateAndReviewerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = JobStore(str(Path(self.temp.name) / "state.sqlite"))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_one_active_job_and_idempotent_replay(self):
        job_id, existing = self.store.claim("r1", "hash1")
        self.assertIsNone(existing)
        with self.assertRaises(BusyError):
            self.store.claim("r2", "hash2")
        self.store.finish(job_id, "failed", None, "test")
        same_id, existing = self.store.claim("r1", "hash1")
        self.assertEqual(same_id, job_id)
        self.assertEqual(existing["status"], "failed")

    def test_stale_job_is_recovered_before_new_admission(self):
        old_id, _ = self.store.claim("old", "hash-old", now=1000)
        new_id, existing = self.store.claim("new", "hash-new", now=2000)
        self.assertNotEqual(old_id, new_id)
        self.assertIsNone(existing)
        latest = self.store.latest_terminal()
        self.assertEqual(latest["job_id"], old_id)
        self.assertEqual(latest["error_code"], "stale_interrupted")

    def test_review_caches_result_and_overlay_rules_are_present(self):
        fake = FakeAppServer(packet())
        reviewer = ResourceReviewer(self.store, fake, cwd=self.temp.name, browser=FakeBrowser())
        original_validate = __import__("resource_review_bridge.reviewer", fromlist=["validate_public_url"]).validate_public_url
        import resource_review_bridge.reviewer as module
        module.validate_public_url = lambda value: URL
        try:
            first = reviewer.review(URL, "req-1")
            second = reviewer.review(URL, "req-1")
        finally:
            module.validate_public_url = original_validate
        self.assertEqual(first["status"], "completed")
        self.assertTrue(second["cached"])
        self.assertEqual(fake.calls, 1)
        self.assertIn("Close or X", fake.prompt)
        self.assertIn("backdrop", fake.prompt)

    def test_mcp_surface_exposes_only_review_resource(self):
        fake = FakeAppServer(packet())
        reviewer = ResourceReviewer(self.store, fake, cwd=self.temp.name, browser=FakeBrowser())
        listed = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, reviewer)
        self.assertEqual([tool["name"] for tool in listed["result"]["tools"]], ["review_resource"])

    def test_appserver_failure_is_terminal_and_retryable(self):
        fake = FailingAppServer(AppServerError("unavailable"))
        reviewer = ResourceReviewer(self.store, fake, cwd=self.temp.name, browser=FakeBrowser())
        import resource_review_bridge.reviewer as module
        original_validate = module.validate_public_url
        module.validate_public_url = lambda value: URL
        try:
            result = reviewer.review(URL, "req-appserver-failure")
            next_job, existing = self.store.claim("req-after-failure", "another-hash")
        finally:
            module.validate_public_url = original_validate
        self.assertEqual(result["error_code"], "evidence_generation_failed")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(fake.calls, 2)
        self.assertIsNone(existing)
        self.store.finish(next_job, "failed", None, "test")

    def test_unexpected_failure_does_not_leave_running_job(self):
        fake = FailingAppServer(RuntimeError("unexpected"))
        reviewer = ResourceReviewer(self.store, fake, cwd=self.temp.name, browser=FakeBrowser())
        import resource_review_bridge.reviewer as module
        original_validate = module.validate_public_url
        module.validate_public_url = lambda value: URL
        try:
            result = reviewer.review(URL, "req-unexpected-failure")
            next_job, existing = self.store.claim("req-after-unexpected", "another-hash")
        finally:
            module.validate_public_url = original_validate
        self.assertEqual(result["error_code"], "internal_error")
        self.assertEqual(result["attempts"], 1)
        self.assertIsNone(existing)
        self.store.finish(next_job, "failed", None, "test")


class AppServerTests(unittest.TestCase):
    def test_missing_executable_has_stable_error(self):
        client = AppServerClient(command="/definitely/missing/codex")
        with self.assertRaisesRegex(AppServerError, "executable is unavailable"):
            client.run("prompt", "/tmp", {})


if __name__ == "__main__":
    unittest.main()
