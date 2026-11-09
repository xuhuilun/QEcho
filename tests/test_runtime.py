import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from qecho.runtime import InstanceLock, protect_path

ROOT = Path(__file__).resolve().parents[1]
HOLDER = """
import sys
from pathlib import Path
from qecho.runtime import InstanceLock
lock = InstanceLock(sys.argv[1])
lock.acquire()
Path(sys.argv[2]).write_text('ready')
sys.stdin.read(1)
lock.release()
"""
CONTENDER = """
import sys
from qecho.runtime import InstanceLock
lock = InstanceLock(sys.argv[1])
try:
    lock.acquire()
except RuntimeError as exc:
    assert 'already in use' in str(exc)
    print('busy')
    sys.exit(23)
else:
    print('acquired')
    lock.release()
"""


@contextmanager
def holding_process(path):
    """Use a readiness marker so an unexpected child failure cannot hang pytest."""
    ready = path.with_suffix(".ready")
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", HOLDER, str(path), str(ready)],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists():
            if process.poll() is not None:
                stdout, stderr = process.communicate(timeout=5)
                pytest.fail(f"Lock holder exited: {stdout}\n{stderr}")
            if time.monotonic() >= deadline:
                pytest.fail("Lock holder did not become ready")
            time.sleep(0.02)
        yield process
    finally:
        if process.poll() is None:
            try:
                process.communicate(input="x", timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)
        else:
            process.communicate(timeout=5)


def contender(path):
    return subprocess.run(
        [sys.executable, "-c", CONTENDER, str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_two_processes_exclude_second_until_explicit_release(tmp_path):
    path = tmp_path / "instance.lock"
    with holding_process(path) as process:
        blocked = contender(path)
        assert blocked.returncode == 23, blocked.stderr
        assert blocked.stdout.strip() == "busy"
        assert path.stat().st_size >= 1
        process.communicate(input="x", timeout=5)
        assert process.returncode == 0
        accepted = contender(path)
        assert accepted.returncode == 0, accepted.stderr
        assert accepted.stdout.strip() == "acquired"
    assert path.exists(), "Removing the lock file can split competing processes across inodes"


def test_holder_process_exit_releases_lock(tmp_path):
    path = tmp_path / "instance.lock"
    with holding_process(path) as process:
        assert contender(path).returncode == 23
        process.kill()
        process.wait(timeout=5)
        accepted = contender(path)
        assert accepted.returncode == 0, accepted.stderr
        assert accepted.stdout.strip() == "acquired"
    assert path.exists()


def test_existing_empty_file_still_excludes_second_process(tmp_path):
    path = tmp_path / "instance.lock"
    path.touch()
    with holding_process(path):
        blocked = contender(path)
        assert blocked.returncode == 23, blocked.stderr
        assert path.stat().st_size >= 1


def test_release_is_idempotent_and_instance_can_be_reused(tmp_path):
    path = tmp_path / "instance.lock"
    lock = InstanceLock(path)
    lock.release()
    lock.acquire()
    lock.acquire()
    assert contender(path).returncode == 23
    lock.release()
    lock.release()
    lock.acquire()
    lock.release()
    assert contender(path).returncode == 0


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits do not establish Windows ACLs")
def test_protect_path_restricts_posix_mode(tmp_path):
    path = tmp_path / "secret"
    path.write_text("private")
    protect_path(path, 0o600)
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific ACL behavior")
def test_protect_path_does_not_claim_windows_acl_protection(tmp_path, monkeypatch):
    path = tmp_path / "secret"
    path.write_text("private")

    def unexpected_chmod(*args, **kwargs):
        pytest.fail("Windows ACL protection must not be replaced with chmod")

    monkeypatch.setattr(Path, "chmod", unexpected_chmod)
    protect_path(path, 0o600)
    assert path.read_text() == "private"
