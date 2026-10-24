import hashlib
import hmac
import importlib
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

from starlette.requests import Request

import supervisor


class SelfUpdateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        config = types.ModuleType("config")
        config.Target = dict
        config.TargetCondition = dict
        config.dot = types.SimpleNamespace(github_token=None)
        config.settings = {
            "this": {
                "repo": "owner/updater",
                "secret": "secret",
                "deploy": "git",
                "conditions": [{"eventType": "push"}],
            },
            "targets": {},
        }
        with patch.dict(sys.modules, {"config": config}):
            sys.modules.pop("main", None)
            self.main = importlib.import_module("main")
        self.main.server = Mock(should_exit=False)

    def request(self, signature=True, repo="owner/updater", event="push"):
        body = json.dumps({"repository": {"full_name": repo}}).encode()
        digest = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        headers = [
            (b"x-github-event", event.encode()),
            (
                b"x-hub-signature-256",
                ("sha256=" + digest if signature else "bad").encode(),
            ),
        ]

        async def receive():
            return {"type": "http.request", "body": body}

        return Request({"type": "http", "headers": headers}, receive)

    async def test_success_restarts_only_after_response(self):
        with patch.object(self.main, "git_pull", return_value=True) as pull:
            response = await self.main.hook("this", self.request())
        self.assertEqual(response.status_code, 200)
        pull.assert_called_once_with(str(Path(self.main.__file__).resolve().parent))
        self.assertFalse(self.main.server.should_exit)
        await response.background()
        self.assertTrue(self.main.server.should_exit)
        self.assertTrue(self.main.restart_requested)

    async def test_failed_pull_does_not_restart(self):
        for outcome in (False, OSError("git failed")):
            with patch.object(
                self.main,
                "git_pull",
                side_effect=outcome if isinstance(outcome, Exception) else None,
                return_value=False,
            ):
                response = await self.main.hook("this", self.request())
            self.assertEqual(response.status_code, 500)
            self.assertIsNone(response.background)
            self.assertFalse(self.main.server.should_exit)

    async def test_rejected_webhooks_do_not_pull(self):
        for request, status in (
            (self.request(signature=False), 403),
            (self.request(repo="other/repo"), 404),
            (self.request(event="ping"), 200),
            (self.request(event="release"), 200),
        ):
            with patch.object(self.main, "git_pull") as pull:
                response = await self.main.hook("this", request)
            self.assertEqual(response.status_code, status)
            pull.assert_not_called()
            self.assertIsNone(response.background)

    async def test_unmanaged_server_does_not_pull(self):
        self.main.server = None
        with patch.object(self.main, "git_pull") as pull:
            response = await self.main.hook("this", self.request())
        self.assertEqual(response.status_code, 503)
        pull.assert_not_called()

    def test_git_exit_status(self):
        for code in (0, 1):
            process = Mock(stdout=None)
            process.wait.return_value = code
            with patch.object(self.main, "Popen", return_value=process) as start:
                self.assertEqual(self.main.git_pull("."), code == 0)
            self.assertEqual(start.call_args.args[0][1:], ["pull", "--ff-only"])


class SupervisorTests(unittest.TestCase):
    @patch.object(supervisor, "which", return_value="uv")
    def test_sync_then_restart(self, _):
        with patch.object(
            supervisor.subprocess,
            "run",
            side_effect=[Mock(returncode=75), Mock(returncode=0), Mock(returncode=0)],
        ) as run:
            self.assertEqual(supervisor.run(), 0)
        self.assertEqual(run.call_count, 3)
        self.assertEqual(run.call_args_list[1].args[0], ["uv", "sync", "--locked"])

    @patch.object(supervisor, "which", return_value="uv")
    def test_sync_failure_stops(self, _):
        with patch.object(
            supervisor.subprocess,
            "run",
            side_effect=[Mock(returncode=75), Mock(returncode=1)],
        ) as run:
            self.assertEqual(supervisor.run(), 1)
        self.assertEqual(run.call_count, 2)

    @patch.object(supervisor, "which", return_value="uv")
    def test_normal_exit_does_not_sync(self, _):
        with patch.object(
            supervisor.subprocess, "run", return_value=Mock(returncode=0)
        ) as run:
            self.assertEqual(supervisor.run(), 0)
        self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
