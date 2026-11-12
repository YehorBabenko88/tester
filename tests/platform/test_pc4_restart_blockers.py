"""Pure evidence for current fail-closed behavior; NOT restart acceptance."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.integration import pc4_runtime


@pytest.mark.parametrize(
    "retained",
    [
        ".pc4-api.json",
        ".market-api.json",
        ".market-worker.json",
        "runtime/stop.request",
        "pc4-shutdown.request",
    ],
)
def test_retained_generation_zero_records_refuse_before_side_effects(
    tmp_path, monkeypatch, retained
):
    # Import dependencies before replacing their OS calls. No server is started.
    import psutil
    import uvicorn  # noqa: F401

    root = tmp_path / "owned-root"
    path = root / retained
    path.parent.mkdir(parents=True)
    original = b"retained immutable evidence\n"
    path.write_bytes(original)
    source = Path.cwd().resolve()
    settings = SimpleNamespace(data_dir=root / "runtime")
    monkeypatch.setattr(
        pc4_runtime, "context", lambda *a, **k: (source, root, {}, settings, {}, object())
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("refusal must precede port/process/worker side effects")

    monkeypatch.setattr(pc4_runtime.socket, "socket", forbidden)
    monkeypatch.setattr(pc4_runtime.subprocess, "Popen", forbidden)
    monkeypatch.setattr(psutil, "Process", forbidden)
    with pytest.raises(ValueError, match="restart/adoption refused"):
        pc4_runtime.serve(root, "a" * 40, "b" * 40)
    assert path.read_bytes() == original
    assert [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()] == [retained]
