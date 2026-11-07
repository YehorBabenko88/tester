import time

import pytest

from src.api.config import Settings
from src.api.maintenance import cleanup_temp, storage_status


def test_cleanup_only_old_owned_parts_never_other_files(tmp_path):
    owned = tmp_path / "tmp"
    owned.mkdir()
    stale = owned / "orphan.part"
    stale.write_text("abandoned")
    valuable = owned / "keep.csv"
    valuable.write_text("keep")
    nested = owned / "nested"
    nested.mkdir()
    (nested / "keep.part").write_text("keep")
    assert cleanup_temp(tmp_path, older_than_seconds=3600) == 0
    assert cleanup_temp(tmp_path, older_than_seconds=0, now=time.time() + 1) == 1
    assert valuable.exists()
    assert (nested / "keep.part").exists()


def test_quota_and_config_guardrails(tmp_path):
    (tmp_path / "market.bin").write_bytes(b"x" * 100)
    status = storage_status(Settings(data_dir=tmp_path, min_free_bytes=0, storage_quota_bytes=99))
    assert status["storage"] == "quota_exceeded"
    assert status["used_bytes"] >= 100
    with pytest.raises(ValueError):
        Settings(host="0.0.0.0")
    with pytest.raises(ValueError):
        Settings(database_url="postgresql://localhost/postgres")


def test_cleanup_does_not_follow_link(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    valuable = outside / "keep.part"
    valuable.write_text("keep")
    try:
        (tmp_path / "tmp").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Windows symlink privilege not available")
    assert cleanup_temp(tmp_path, older_than_seconds=0, now=time.time() + 1) == 0
    assert valuable.exists()


def test_independent_processes_rotate_logs_without_loss(tmp_path):
    import json
    import subprocess
    import sys

    code = """
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from uuid import UUID
from src.api.logging_setup import configure
logger = configure(Path(sys.argv[1]), sys.argv[2])
for handler in logger.handlers:
    if isinstance(handler, RotatingFileHandler):
        handler.maxBytes = 600
for i in range(12):
    logger.info('record', extra={'job_id': str(UUID(int=i))})
"""
    children = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(tmp_path), name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for name in ("api", "worker")
    ]
    for child in children:
        _, errors = child.communicate(timeout=15)
        assert child.returncode == 0, errors.decode()
        assert b"Logging error" not in errors
    for name in ("api", "worker"):
        records = [
            json.loads(line)
            for path in (tmp_path / "logs").glob(name + ".jsonl*")
            for line in path.read_text().splitlines()
        ]
        from uuid import UUID

        assert sorted(UUID(record["job_id"]).int for record in records) == list(range(12))
