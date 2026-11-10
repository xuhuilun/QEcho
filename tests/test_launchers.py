"""Exercise real launch scripts with a fake uv; never install or start services."""

import json
import os
import shutil
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from qecho.config import load_config

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_NAMES = ("start.sh", "start.command", "start.bat", "qecho.example.toml")
FAKE_UV = """
import json
import os
import sys
from pathlib import Path
args = sys.argv[1:]
with Path(os.environ['QECHO_LAUNCHER_LOG']).open('a', encoding='utf-8') as handle:
    handle.write(json.dumps({'args': args, 'cwd': str(Path.cwd())}) + '\\n')
if args[0] == os.environ.get('QECHO_LAUNCHER_FAIL_STAGE'):
    raise SystemExit(7)
"""


@pytest.fixture
def launcher(tmp_path):
    project = tmp_path / "QEcho path with spaces"
    project.mkdir()
    for name in SCRIPT_NAMES:
        shutil.copy2(ROOT / name, project / name)
    bin_dir = tmp_path / "fake tools with spaces"
    bin_dir.mkdir()
    fake = bin_dir / "fake_uv.py"
    fake.write_text(FAKE_UV, encoding="utf-8")
    if os.name == "nt":
        (bin_dir / "uv.cmd").write_text(
            f'@echo off\n"{sys.executable}" "{fake}" %*\nexit /b %errorlevel%\n',
            encoding="utf-8",
            newline="\r\n",
        )
    else:
        import shlex

        command = bin_dir / "uv"
        command.write_text(
            f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(fake))} "$@"\n',
            encoding="utf-8",
        )
        command.chmod(0o755)
    log = tmp_path / "uv-calls.jsonl"
    env = os.environ | {
        "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
        "QECHO_LAUNCHER_LOG": str(log),
    }
    return project, env, log


def run_launcher(project, env, *, mac_wrapper=False):
    if os.name == "nt":
        # Preserve cmd's quoting around a batch path containing spaces. The fake
        # uv.cmd also verifies that CALL returns to the launcher after each step.
        command = f'cmd.exe /d /s /c ""{project / "start.bat"}""'
    else:
        command = ["/bin/bash", str(project / ("start.command" if mac_wrapper else "start.sh"))]
    return subprocess.run(
        command,
        cwd=project.parent,
        env=env,
        input="\n",
        capture_output=True,
        text=True,
        timeout=15,
    )


def calls(log):
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def test_sample_is_valid_toml_and_does_not_auto_start():
    sample = tomllib.loads((ROOT / "qecho.example.toml").read_text(encoding="utf-8"))
    assert set(sample) == {"server", "model", "qq", "agent"}
    assert sample["agent"]["auto_start"] is False
    assert sample["agent"]["friends"] == []
    assert sample["server"]["admin_token"] == sample["qq"]["event_token"] == ""
    parsed = load_config(ROOT / "qecho.example.toml")
    assert parsed.agent.auto_start is False
    assert parsed.data_directory == (ROOT / ".data").resolve()


def test_first_run_only_copies_config_before_running_uv(launcher):
    project, env, log = launcher
    result = run_launcher(project, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (project / "qecho.toml").read_bytes() == (project / "qecho.example.toml").read_bytes()
    assert "Edit qecho.toml" in result.stdout
    assert "not started" in result.stdout
    assert not log.exists()
    if os.name != "nt":
        assert stat.S_IMODE((project / "qecho.toml").stat().st_mode) == 0o600


def test_existing_config_is_kept_and_steps_run_from_project_directory(launcher):
    project, env, log = launcher
    config = project / "qecho.toml"
    config.write_text("# user configuration must stay intact\n", encoding="utf-8")
    original = config.read_bytes()
    result = run_launcher(project, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert config.read_bytes() == original
    recorded = calls(log)
    assert [entry["args"] for entry in recorded] == [
        ["python", "install", "3.12"],
        ["sync", "--locked", "--no-dev"],
        ["run", "--no-sync", "qecho", "--config", str(config)],
    ]
    assert all(Path(entry["cwd"]) == project for entry in recorded)


@pytest.mark.parametrize("stage,expected_calls", [("python", 1), ("sync", 2), ("run", 3)])
def test_failed_step_preserves_error_and_stops_later_steps(launcher, stage, expected_calls):
    project, env, log = launcher
    (project / "qecho.toml").write_text("# existing configuration\n", encoding="utf-8")
    env["QECHO_LAUNCHER_FAIL_STAGE"] = stage
    result = run_launcher(project, env)
    assert result.returncode == 7, result.stdout + result.stderr
    assert len(calls(log)) == expected_calls
    assert (
        "failed" in (result.stdout + result.stderr).lower()
        or "error" in (result.stdout + result.stderr).lower()
    )


def test_missing_uv_explains_installation_without_starting(launcher):
    project, env, log = launcher
    (project / "qecho.toml").write_text("# existing configuration\n", encoding="utf-8")
    env["PATH"] = (
        str(Path(os.environ["SystemRoot"]) / "System32") if os.name == "nt" else os.defpath
    )
    result = run_launcher(project, env)
    assert result.returncode != 0
    assert "uv was not found" in result.stdout + result.stderr
    assert "docs.astral.sh" in result.stdout + result.stderr
    assert not log.exists()


@pytest.mark.skipif(os.name == "nt", reason="macOS wrapper uses a Unix shell")
def test_mac_wrapper_delegates_and_does_not_wait_on_noninteractive_input(launcher):
    project, env, log = launcher
    (project / "qecho.toml").write_text("# existing configuration\n", encoding="utf-8")
    result = run_launcher(project, env, mac_wrapper=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(calls(log)) == 3


def test_missing_sample_fails_before_dependency_preparation(launcher):
    project, env, log = launcher
    (project / "qecho.example.toml").unlink()
    result = run_launcher(project, env)
    assert result.returncode != 0
    assert "Cannot create qecho.toml" in result.stdout + result.stderr
    assert not log.exists()


def test_launch_scripts_have_their_required_line_endings():
    for name in ("start.sh", "start.command"):
        assert b"\r" not in (ROOT / name).read_bytes()
    batch = (ROOT / "start.bat").read_bytes()
    assert b"\r\n" in batch and b"\n" not in batch.replace(b"\r\n", b"")


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable bits are not Windows permissions")
def test_unix_launchers_are_executable():
    for name in ("start.sh", "start.command"):
        assert os.access(ROOT / name, os.X_OK)
