"""Run archived F998 and current F in separately imported Python processes.

This CI-only gate reads source from pinned commit 998c5bc from full Git history.
It does not use Bybit, a trading account, PostgreSQL or a production API.
It compares the entire output JSON including replay_digest and checkpoint replay.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_COMMIT = "998c5bc89d1b35c9ad3047b1e50dcb1295120e31"
PIN = ROOT / "src/integration/F_SOURCE_PIN.json"
RUNNER = """
import json
from pathlib import Path
from src.backtest import Backtest, restore, run
from src.backtest.engine import ENGINE_VERSION
import src.backtest.engine as engine
assert Path(engine.__file__).resolve().is_relative_to(Path.cwd().resolve())
payload = json.load(__import__('sys').stdin)
result = run(payload['strategy'], payload['snapshot'])
assert ENGINE_VERSION == 'strategy-bar-1.1.0'
engine = Backtest(payload['strategy'], payload['snapshot'])
assert engine.step()
checkpoint = engine.checkpoint()
resumed = restore(payload['strategy'], payload['snapshot'], checkpoint)
while resumed.step():
    pass
assert resumed.result() == result
assert result['replay_digest'] == resumed.checkpoint()['prefix_digest']
print(json.dumps(result, sort_keys=True, separators=(',', ':'), allow_nan=False))
"""


def git_blob_at_old_commit(relative):
    command = ["git", "show", f"{HISTORICAL_COMMIT}:{relative}"]
    return subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        check=True,
        timeout=20,
    ).stdout


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def old_engine_tree(tmp, pin):
    """Reconstruct only known, independently pinned old F source, no current code."""
    for name, identity in pin["files"].items():
        if not name.startswith(("src/backtest/", "src/strategy/")):
            continue
        raw = git_blob_at_old_commit(name)
        if sha256(raw) != identity["sha256"]:
            raise AssertionError(f"historical file SHA256 differs: {name}")
        target = tmp / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        # Also run both source versions independently. SHA equality alone is
        # not our replay proof.
        if sha256((ROOT / name).read_bytes()) != identity["sha256"]:
            raise AssertionError(f"current historical F source changed: {name}")


def run_f(source_root, payload):
    env = os.environ.copy()
    for key in ("PYTHONHOME", "PYTHONSTARTUP", "PYTHONINSPECT"):
        env.pop(key, None)
    env["PYTHONPATH"] = str(source_root)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONHASHSEED"] = "0"
    output = subprocess.run(
        [sys.executable, "-c", RUNNER],
        cwd=source_root,
        env=env,
        input=json.dumps(payload, sort_keys=True),
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )
    return json.loads(output.stdout)


def main():
    pin = json.loads(PIN.read_text(encoding="utf-8"))
    if pin["source_sha"] != HISTORICAL_COMMIT:
        raise AssertionError("F source identity unexpectedly moved")
    if len(pin["files"]) != 18:
        raise AssertionError("F pin manifest must remain complete")

    spot = {}
    for name, key in (
        ("strategy", "example.strategy.json"),
        ("snapshot", "example.snapshot.json"),
    ):
        relative = f"src/integration/fixtures/f998/{key}"
        entry = pin["files"][relative]
        historical_bytes = git_blob_at_old_commit(entry["producer_path"])
        if sha256(historical_bytes) != entry["sha256"]:
            raise AssertionError(f"historic fixture source drift: {key}")
        if sha256((ROOT / relative).read_bytes()) != entry["sha256"]:
            raise AssertionError(f"packaged old fixture drift: {key}")
        spot[name] = json.loads(historical_bytes)

    linear_root = ROOT / "src/integration/fixtures/pc4-terminal-linear-v1"
    manifest = json.loads((linear_root / "manifest.json").read_text(encoding="utf-8"))
    if (
        manifest["engine_source_sha"] != HISTORICAL_COMMIT
        or manifest["real_market_coverage"] != "NOT_PROVEN"
    ):
        raise AssertionError(
            "linear fixture is not the independently pinned synthetic fixture"
        )
    linear = {}
    for name in ("strategy", "snapshot"):
        raw = (linear_root / f"{name}.json").read_bytes()
        if sha256(raw) != manifest["files"][f"{name}.json"]:
            raise AssertionError("linear fixture bytes differ from pinned manifest")
        linear[name] = json.loads(raw)

    with tempfile.TemporaryDirectory(prefix="tester-f998-legacy-") as folder:
        old_root = Path(folder)
        old_engine_tree(old_root, pin)
        for label, payload in (("spot_F998", spot), ("linear_PC4", linear)):
            older = run_f(old_root, payload)
            current = run_f(ROOT, payload)
            if older != current or older["replay_digest"] != current["replay_digest"]:
                raise AssertionError(
                    f"archived historical F/full output/replay mismatch: {label}"
                )
            if older["engine_version"] != "strategy-bar-1.1.0":
                raise AssertionError("legacy engine version unexpectedly moved")
            print(
                json.dumps(
                    {
                        "fixture": label,
                        "baseline_commit": HISTORICAL_COMMIT,
                        "engine_version": older["engine_version"],
                        "archived_replay_digest": older["replay_digest"],
                        "current_replay_digest": current["replay_digest"],
                        "full_output_sha256": sha256(
                            json.dumps(
                                older,
                                sort_keys=True,
                                separators=(",", ":"),
                                allow_nan=False,
                            ).encode()
                        ),
                        "result": "CROSS_VERSION_FULL_OUTPUT_EQUAL",
                        "historical_source": "SYNTHETIC_ONLY",
                    },
                    sort_keys=True,
                )
            )


if __name__ == "__main__":
    main()
