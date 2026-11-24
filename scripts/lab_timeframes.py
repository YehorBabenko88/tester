"""Published nonadmin closed-candle smoke for all thirteen terminal intervals."""

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from lab_soak import assert_identity, atomic_report, bind, http, plain


def windows(now_ms):
    result = []
    for interval in ("1", "3", "5", "15", "30", "60", "120", "240", "360", "720", "D"):
        step = 86400000 if interval == "D" else int(interval) * 60000
        count = min(3, 86400000 // step)
        end = now_ms // step * step
        result.append((interval, end - count * step, end, count))
    day = datetime.fromtimestamp(now_ms / 1000, UTC)
    midnight = day.replace(hour=0, minute=0, second=0, microsecond=0)
    monday = int(midnight.timestamp() * 1000) - day.weekday() * 86400000
    result.append(("W", monday - 2 * 604800000, monday, 2))
    end = midnight.replace(day=1)
    index = end.year * 12 + end.month - 1 - 3
    start = end.replace(year=index // 12, month=index % 12 + 1)
    result.append(("M", int(start.timestamp() * 1000), int(end.timestamp() * 1000), 3))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("C:/TesterLab"))
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = plain(args.out)
    root = plain(args.root)
    if not out.is_absolute() or root in out.parents:
        raise ValueError("Output must be outside lab")
    identity, _, _ = bind(root, args.expected_sha)
    report = dict(
        kind="THIRTEEN_TIMEFRAME_PUBLIC_DATA_SMOKE", status="RUNNING", identity=identity, results=[]
    )
    atomic_report(out, report, initial=True)
    try:
        for interval, start, end, expected in windows(time.time_ns() // 1000000):
            request = dict(
                category="linear",
                symbol="BTCUSDT",
                source="bybit",
                kind="candles",
                interval=interval,
                start_ms=start,
                end_ms=end,
            )
            job = http("/api/downloads", request)["data"]
            deadline = time.monotonic() + 150
            while job["state"] not in ("completed", "failed"):
                if time.monotonic() > deadline:
                    raise RuntimeError("Bounded job timeout")
                time.sleep(0.5)
                job = http("/api/downloads/" + job["id"])["data"]
            if job["state"] != "completed":
                raise RuntimeError("Public interval download failed")
            telemetry = job["checkpoint"]["telemetry"]
            if (
                telemetry["records_verified"] != expected
                or telemetry["records_ingested"] != expected
                or telemetry["work_complete"] is not True
            ):
                raise RuntimeError("Record acknowledgment mismatch")
            data = http(
                f"/api/candles?category=linear&symbol=BTCUSDT&interval={interval}&start_ms={start}&end_ms={end}&limit=1000"
            )
            if len(data["data"]) != expected or any(
                not row["is_closed"] or not row["provenance"] or row["kind"] != "official_trade"
                for row in data["data"]
            ):
                raise RuntimeError("Closed/provenance/range mismatch")
            report["results"].append(
                dict(
                    interval=interval,
                    start_ms=start,
                    end_ms=end,
                    expected=expected,
                    rows=len(data["data"]),
                    job_id=job["id"],
                    telemetry=telemetry,
                    coverage=data["coverage"]["status"],
                )
            )
            atomic_report(out, report)
        assert_identity(http("/api/lab/identity"), identity)
        report["status"] = "THIRTEEN_TIMEFRAME_PUBLIC_DATA_PASS_NOT_BROWSER"
    except Exception as exc:
        report["status"] = "FAILED"
        report["error_type"] = type(exc).__name__
    finally:
        atomic_report(out, report)
    print(json.dumps(dict(status=report["status"], intervals=len(report["results"]))))
    return 0 if report["status"].endswith("NOT_BROWSER") else 1


if __name__ == "__main__":
    raise SystemExit(main())
