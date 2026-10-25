import hashlib
import hmac
import importlib
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, mock_open, patch

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


class DownloadFileRetryTests(unittest.TestCase):
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
            "base": {"tmp": None},
        }
        with patch.dict(sys.modules, {"config": config}):
            sys.modules.pop("main", None)
            self.main = importlib.import_module("main")
        self.main.RELEASE_ASSET_RETRY_ATTEMPTS = 3
        self.main.RELEASE_ASSET_RETRY_BASE_DELAY_SECONDS = 1
        self.target_setting = {
            "filename": "release.tar.gz",
            "path": "/tmp/webhook-updater-test-extract",
            "repo": "owner/site",
        }

    def _meta(self, assets, status=200):
        res = Mock()
        res.status_code = status
        res.json.return_value = {"assets": assets}
        return res

    def _asset(self, name="release.tar.gz"):
        return {
            "name": name,
            "content_type": "application/x-gtar",
            "browser_download_url": "https://example.com/release.tar.gz",
        }

    def _file_res(self, status=200):
        res = Mock()
        res.status_code = status
        res.iter_content.return_value = [b"data"]
        return res

    def test_retries_until_asset_appears(self):
        meta_empty = self._meta([])
        meta_ready = self._meta([self._asset()])
        with (
            patch.object(
                self.main.requests,
                "get",
                side_effect=[meta_empty, meta_ready, self._file_res()],
            ) as get,
            patch.object(self.main.time, "sleep") as sleep,
            patch.object(self.main.tarfile, "open") as tar_open,
            patch.object(self.main.os, "remove"),
            patch.object(self.main.os.path, "exists", return_value=True),
            patch("builtins.open", mock_open()),
        ):
            tar_open.return_value.__enter__.return_value = Mock()
            ok = self.main.download_file(
                "https://api.github.com/repos/owner/site/releases/1",
                self.target_setting,
                "site",
            )
        self.assertTrue(ok)
        self.assertEqual(get.call_count, 3)
        sleep.assert_called_once_with(1)

    def test_gives_up_when_asset_never_appears(self):
        with (
            patch.object(
                self.main.requests, "get", return_value=self._meta([])
            ) as get,
            patch.object(self.main.time, "sleep") as sleep,
        ):
            ok = self.main.download_file(
                "https://api.github.com/repos/owner/site/releases/1",
                self.target_setting,
                "site",
            )
        self.assertFalse(ok)
        self.assertEqual(get.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_retries_on_meta_5xx_then_succeeds(self):
        meta_err = self._meta([], status=502)
        meta_ready = self._meta([self._asset()])
        with (
            patch.object(
                self.main.requests,
                "get",
                side_effect=[meta_err, meta_ready, self._file_res()],
            ) as get,
            patch.object(self.main.time, "sleep") as sleep,
            patch.object(self.main.tarfile, "open") as tar_open,
            patch.object(self.main.os, "remove"),
            patch.object(self.main.os.path, "exists", return_value=True),
            patch("builtins.open", mock_open()),
        ):
            tar_open.return_value.__enter__.return_value = Mock()
            ok = self.main.download_file(
                "https://api.github.com/repos/owner/site/releases/1",
                self.target_setting,
                "site",
            )
        self.assertTrue(ok)
        self.assertEqual(get.call_count, 3)
        sleep.assert_called_once_with(1)

    def test_does_not_retry_on_meta_4xx(self):
        with (
            patch.object(
                self.main.requests, "get", return_value=self._meta([], status=404)
            ) as get,
            patch.object(self.main.time, "sleep") as sleep,
        ):
            ok = self.main.download_file(
                "https://api.github.com/repos/owner/site/releases/1",
                self.target_setting,
                "site",
            )
        self.assertFalse(ok)
        get.assert_called_once()
        sleep.assert_not_called()

    def test_retries_on_meta_timeout(self):
        meta_ready = self._meta([self._asset()])
        with (
            patch.object(
                self.main.requests,
                "get",
                side_effect=[
                    self.main.requests.Timeout("timed out"),
                    meta_ready,
                    self._file_res(),
                ],
            ) as get,
            patch.object(self.main.time, "sleep") as sleep,
            patch.object(self.main.tarfile, "open") as tar_open,
            patch.object(self.main.os, "remove"),
            patch.object(self.main.os.path, "exists", return_value=True),
            patch("builtins.open", mock_open()),
        ):
            tar_open.return_value.__enter__.return_value = Mock()
            ok = self.main.download_file(
                "https://api.github.com/repos/owner/site/releases/1",
                self.target_setting,
                "site",
            )
        self.assertTrue(ok)
        self.assertEqual(get.call_count, 3)
        sleep.assert_called_once_with(1)


class SupervisorTests(unittest.TestCase):
    @patch.object(supervisor, "resolve_uv", return_value="/usr/bin/uv")
    def test_sync_then_restart(self, _):
        with patch.object(
            supervisor.subprocess,
            "run",
            side_effect=[Mock(returncode=75), Mock(returncode=0), Mock(returncode=0)],
        ) as run:
            self.assertEqual(supervisor.run(), 0)
        self.assertEqual(run.call_count, 3)
        self.assertEqual(
            run.call_args_list[1].args[0], ["/usr/bin/uv", "sync", "--locked"]
        )

    @patch.object(supervisor, "resolve_uv", return_value="/usr/bin/uv")
    def test_sync_failure_stops(self, _):
        with patch.object(
            supervisor.subprocess,
            "run",
            side_effect=[Mock(returncode=75), Mock(returncode=1)],
        ) as run:
            self.assertEqual(supervisor.run(), 1)
        self.assertEqual(run.call_count, 2)

    @patch.object(supervisor, "resolve_uv", return_value="/usr/bin/uv")
    def test_normal_exit_does_not_sync(self, _):
        with patch.object(
            supervisor.subprocess, "run", return_value=Mock(returncode=0)
        ) as run:
            self.assertEqual(supervisor.run(), 0)
        self.assertEqual(run.call_count, 1)

    @patch.object(supervisor, "resolve_uv", return_value=None)
    def test_missing_uv_exits_before_run(self, _):
        with patch.object(supervisor.subprocess, "run") as run:
            self.assertEqual(supervisor.run(), 1)
        run.assert_not_called()


class ResolveUvTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.tmp = Path(self._tmpdir.name) / "uv"
        self.tmp.write_text("#!/bin/sh\n")
        self.tmp.chmod(0o755)

    def test_prefers_webhook_updater_uv_env(self):
        other = Path(self.tmp).parent / "other-uv"
        other.write_text("#!/bin/sh\n")
        other.chmod(0o755)
        self.addCleanup(lambda: other.unlink(missing_ok=True))
        found = supervisor.resolve_uv(
            environ={
                "WEBHOOK_UPDATER_UV": str(self.tmp),
                "UV": str(other),
            },
            which_fn=lambda _: str(other),
            parent_exe_fn=lambda _: other,
        )
        self.assertEqual(found, str(self.tmp))

    def test_uses_uv_env_when_webhook_unset(self):
        found = supervisor.resolve_uv(
            environ={"UV": str(self.tmp)},
            which_fn=lambda _: None,
            parent_exe_fn=lambda _: None,
        )
        self.assertEqual(found, str(self.tmp))

    def test_ignores_env_when_not_executable(self):
        missing = Path(self.tmp).parent / "missing-uv"
        found = supervisor.resolve_uv(
            environ={"WEBHOOK_UPDATER_UV": str(missing), "UV": str(missing)},
            which_fn=lambda _: str(self.tmp),
            parent_exe_fn=lambda _: None,
        )
        self.assertEqual(found, str(self.tmp))

    def test_uses_which_when_env_absent(self):
        found = supervisor.resolve_uv(
            environ={},
            which_fn=lambda _: str(self.tmp),
            parent_exe_fn=lambda _: None,
        )
        self.assertEqual(found, str(self.tmp))

    def test_uses_parent_exe_named_uv(self):
        found = supervisor.resolve_uv(
            environ={},
            which_fn=lambda _: None,
            get_ppid=lambda: 1,
            parent_exe_fn=lambda ppid: self.tmp if ppid == 1 else None,
        )
        self.assertEqual(found, str(self.tmp))

    def test_ignores_parent_exe_not_named_uv(self):
        other = Path(self.tmp).parent / "python"
        other.write_text("#!/bin/sh\n")
        other.chmod(0o755)
        self.addCleanup(lambda: other.unlink(missing_ok=True))
        found = supervisor.resolve_uv(
            environ={},
            which_fn=lambda _: None,
            get_ppid=lambda: 1,
            parent_exe_fn=lambda _: other,
        )
        self.assertIsNone(found)

    def test_returns_none_when_unresolved(self):
        found = supervisor.resolve_uv(
            environ={},
            which_fn=lambda _: None,
            parent_exe_fn=lambda _: None,
        )
        self.assertIsNone(found)


if __name__ == "__main__":
    unittest.main()
