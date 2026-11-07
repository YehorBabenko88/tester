"""Fail acceptance if the vendored shared contract differs from its reviewed hash pin."""

import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
for manifest in ["contracts/PIN.json", "src/integration/POLICY_PIN.json"]:
    pin = json.loads((root / manifest).read_text(encoding="utf-8"))
    for name, expected in pin["sha256"].items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            raise SystemExit("Shared contract/policy integrity failure: " + name)
    print("PASS: shared contract/policy hash pin " + pin["source_commit"])
