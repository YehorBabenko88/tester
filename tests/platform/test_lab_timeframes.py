import importlib.util
from datetime import UTC, datetime
from pathlib import Path


def test_timeframe_windows_align_closed_utc_bars_and_bound_calendar_jobs(monkeypatch):
    path = Path("scripts/lab_timeframes.py")
    assert path.is_file(), "published timeframe harness missing"
    monkeypatch.syspath_prepend(str(path.parent.absolute()))
    spec = importlib.util.spec_from_file_location("lab_timeframes", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    now = int(datetime(2026, 11, 8, 12, tzinfo=UTC).timestamp() * 1000)
    windows = m.windows(now)
    assert len(windows) == 13
    for interval, start, end, expected in windows:
        assert start < end <= now and 1 <= expected <= 3
        assert end - start <= (93 if interval in ("W", "M") else 1) * 86400000
        if interval == "W":
            assert datetime.fromtimestamp(end / 1000, UTC).weekday() == 0
        if interval == "M":
            assert datetime.fromtimestamp(start / 1000, UTC).day == 1
