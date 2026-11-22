import io
import json
import os
import shlex
import shutil
import sqlite3
import stat
import unittest
import zipfile
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from subprocess import run
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

from agent_cook.cli import main
from agent_cook.managed_launch import installed_revisions, version_lease
from agent_cook.state.local import LocalSession, LocalSessions
from agent_cook.updates import _register_path, manage_source_install


class ManagedUpdateTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.install = self.root / "Agent Cook 安装"
        self.input = self.root / "资料 文件夹"
        self.input.mkdir()
        (self.input / "selected file.txt").touch()
        self.config = self.root / "用户 配置.json"
        self.config.write_text('{"api_keys":{"openrouter":"fixture-only"},"workspace_dir":"shared"}', encoding="utf-8")
        self.latest = "a" * 40
        self.downloads = []
        self.fail_config = False
        self.bad_archive = False
        self.archive = None
        self.stdout = self.enterContext(redirect_stdout(io.StringIO()))
        self.enterContext(redirect_stderr(io.StringIO()))
        self.enterContext(patch("agent_cook.updates.installation_directory", return_value=self.install))
        self.enterContext(patch("agent_cook.updates.uv_executable", return_value=None))
        self.enterContext(patch("agent_cook.updates._register_path"))
        client = httpx.Client
        self.enterContext(patch(
            "agent_cook.updates.httpx.Client",
            side_effect=lambda **options: client(transport=httpx.MockTransport(self._github), **options),
        ))

    def _github(self, request):
        if request.url.host == "api.github.com":
            self.assertTrue(request.url.path.endswith("/commits/main"))
            return httpx.Response(200, json={"sha": self.latest})
        self.assertEqual(request.url.host, "codeload.github.com")
        self.assertTrue(request.url.path.endswith("/zip/" + self.latest))
        self.downloads.append(self.latest)
        if self.archive is not None:
            return httpx.Response(200, content=self.archive)
        prefix = f"let-the-agent-cook-{self.latest}/agent/"
        shell = f"""#!/bin/sh
case "$1" in
    setup) exit 0 ;;
    --list-models)
        test "$2" = '--config' && test -f "$3" || exit 3
        echo 'Invalid configuration at $.workspace_dir.' >&2
        exit {int(self.fail_config)}
        ;;
esac
test -f 'selected file.txt' || exit 2
printf '%s\n' {shlex.quote(self.latest)}
"""
        powershell = f"""
if ($args[0] -eq "setup") {{ exit 0 }}
if ($args[0] -eq "--list-models") {{
    if ($args[1] -ne "--config" -or !(Test-Path -LiteralPath $args[2])) {{ exit 3 }}
    [Console]::Error.WriteLine("Invalid configuration at workspace_dir.")
    exit {int(self.fail_config)}
}}
if (!(Test-Path -LiteralPath "selected file.txt")) {{ exit 2 }}
Write-Output "{self.latest}"
exit 0
"""
        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, mode="w") as archive:
            script = zipfile.ZipInfo(prefix + "cook")
            script.external_attr = (stat.S_IFREG | 0o755) << 16
            archive.writestr(script, shell)
            archive.writestr(prefix + "cook.ps1", powershell)
            archive.writestr(prefix + "uv.lock", "")
            archive.writestr(prefix + "src/agent_cook/managed_launch.py", "")
            archive.writestr(prefix.removesuffix("agent/") + "site/index.html", "Portfolio")
            if self.bad_archive:
                archive.writestr(prefix + "../../outside", "Invalid archive entry")
        return httpx.Response(200, content=bundle.getvalue())

    def _launch(self, arguments: str = "probe", *, stdin=None, env=None):
        if os.name == "nt":
            shim = self.install / "bin" / "agent-cook.cmd"
            command = f'"{os.environ["COMSPEC"]}" /d /s /c ""{shim}" {arguments}"'
        else:
            command = [str(self.install / "bin" / "agent-cook"), *shlex.split(arguments)]
        process = run(
            command, cwd=self.input, input=stdin, env=env,
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        return process.stdout.strip()

    def _versions(self):
        return {project.name for project in (self.install / "versions").iterdir() if project.is_dir()}

    def test_launch_update_check_and_rollback_keep_at_most_three_versions(self):
        original_config = self.config.read_bytes()
        first, second, third, fourth = (letter * 40 for letter in "abcd")
        manage_source_install(install=True, config=self.config)
        self.assertEqual(self._launch(), first)
        manage_source_install(check=True)
        self.assertEqual(self.downloads, [first])
        self.assertFalse((self.install / "versions" / first / "site").exists())
        with version_lease(self.install, first):
            for self.latest in (second, third):
                manage_source_install(config=self.config)
                self.assertEqual(self._launch(), self.latest)
            self.assertEqual(self._versions(), {first, second, third})
            self.latest = fourth
            with self.assertRaisesRegex(ValueError, "Could not release version"):
                manage_source_install(config=self.config)
            self.assertEqual(self._launch(), third)
            self.assertNotIn(fourth, self.downloads)
        manage_source_install(config=self.config)
        self.assertEqual(self._launch(), fourth)
        self.assertEqual(self._versions(), {third, fourth})
        manage_source_install(rollback=True, config=self.config)
        self.assertEqual(self._launch(), third)
        self.assertEqual(self.config.read_bytes(), original_config)

    def test_failed_download_and_incompatible_config_leave_the_active_version(self):
        manage_source_install(install=True, config=self.config)
        first = self.latest
        self.latest = "b" * 40
        self.bad_archive = True
        with self.assertRaises(ValueError):
            manage_source_install(config=self.config)
        self.assertEqual(self._versions(), {first})
        self.assertFalse((self.install / "outside").exists())
        self.bad_archive = False
        self.fail_config = True
        with self.assertRaisesRegex(ValueError, "could not start with the current configuration"):
            manage_source_install(config=self.config)
        self.assertEqual(installed_revisions(self.install), (first, None))
        self.assertEqual(self._launch(), first)
        self.assertNotIn("fixture-only", self.stdout.getvalue())

    @unittest.skipUnless(os.environ.get("AGENT_COOK_TEST_MANAGED") == "1", "Requires the locked native runtime.")
    def test_install_and_launch_the_locked_git_archive(self):
        checkout = Path(__file__).resolve().parents[2]
        prefix = f"let-the-agent-cook-{self.latest}/"
        self.archive = run(
            ["git", "archive", "--format=zip", f"--prefix={prefix}", "HEAD", "agent"],
            cwd=checkout, check=True, capture_output=True,
        ).stdout
        self.config.write_text(json.dumps({
            "models": {"chat": {"default": "fixture/model", "available": ["fixture/model"]}},
            "workspace_dir": "shared",
        }), encoding="utf-8")
        manage_source_install(install=True, config=self.config)
        self.assertEqual(json.loads(self._launch(f'--list-models --config "{self.config}"'))["chat"]["default"], "fixture/model")
        self.assertEqual(installed_revisions(self.install), (self.latest, None))
        workspace = self.root / "输出 工作区"
        (self.install / "versions/.env").write_text(
            f'AGENT_COOK_WORKSPACE="{workspace.as_posix()}"\n', encoding="utf-8",
        )
        env = {**os.environ, "PYTHONHOME": str(self.root / "missing Python")}
        env.pop("AGENT_COOK_WORKSPACE", None)
        config = self._launch(f'--config "{self.config}"', stdin="/config\n/exit\n", env=env)
        self.assertIn(f"Workspace  {workspace.resolve()}", config)


class GlobalPathTest(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix" and shutil.which("zsh") and shutil.which("uv"), "Requires uv and zsh.")
    def test_path_registration_quotes_a_spaced_install_in_a_new_zsh(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "Library/Application Support/Agent Cook's 安装"
            launcher = root / "bin/agent-cook"
            launcher.parent.mkdir(parents=True)
            launcher.write_text("#!/bin/sh\nprintf 'ready\\n'\n", encoding="utf-8")
            launcher.chmod(0o700)
            profiles = Path(directory) / "shell profiles"
            profiles.mkdir()
            env = {**os.environ, "SHELL": shutil.which("zsh"), "ZDOTDIR": str(profiles)}
            with patch.dict(os.environ, env), redirect_stdout(io.StringIO()) as messages:
                _register_path(root, "a" * 40)
            command = "Start now with "
            direct = next(line.removeprefix(command) for line in messages.getvalue().splitlines() if line.startswith(command))
            for launch in ("agent-cook", direct):
                result = run([env["SHELL"], "-ilc", launch], env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "ready")


class LocalWorkspaceTest(unittest.TestCase):
    def test_interactive_update_uses_the_selected_configuration(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            config = root / "task-config.json"
            config.write_text('{"workspace_dir":"shared"}', encoding="utf-8")
            with (
                patch.dict(os.environ, AGENT_COOK_WORKSPACE=""),
                patch("agent_cook.cli.manage_source_install") as update,
                patch("sys.stdin", io.StringIO("/update --check\n/exit\n")),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(main(["-C", directory, "--config", config.name]), 0)
            update.assert_called_once_with(check=True, rollback=False, config=config, models=None)
            self.assertTrue((root / "shared/.sessions").is_dir())
            self.assertFalse((root / ".agent-cook").exists())

    def test_old_sessions_are_copied_and_remain_scoped_to_the_launch_directory(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, different, workspace = (root / name for name in ("input", "other", "workspace"))
            different.mkdir()
            legacy = source / ".agent-cook" / "sessions.sqlite3"
            first = LocalSessions(legacy).save(LocalSession(inputs={"old.txt": b"old"}))
            with closing(sqlite3.connect(legacy)) as db, db:
                record = json.loads(db.execute("SELECT record FROM sessions").fetchone()[0])
                record.pop("created_on")
                db.execute("UPDATE sessions SET record = ?", (json.dumps(record),))
            imported = LocalSessions.for_directory(workspace, source).load(None)
            self.assertEqual((imported.id, imported.inputs), (first.id, first.inputs))
            with self.assertRaisesRegex(ValueError, "No matching session"):
                LocalSessions.for_directory(workspace, different).load(None)
            self.assertEqual(LocalSessions(legacy).load(None).revision, first.revision)
