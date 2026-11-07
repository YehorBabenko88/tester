"""Fail acceptance if the vendored shared contract differs from its reviewed hash pin."""

import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
pin = json.loads((root / "contracts/PIN.json").read_text(encoding="utf-8"))
for name, expected in pin["sha256"].items():
    if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
        raise SystemExit("Shared contract integrity failure: " + name)
print("PASS: shared contract hash pin " + pin["source_commit"])
