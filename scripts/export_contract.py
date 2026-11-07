"""Regenerate the reviewable, versioned OpenAPI document."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.api.config import Settings
from src.api.main import create_app

path = Path(__file__).resolve().parents[1] / "contracts/v1/openapi.json"
content = json.dumps(create_app(Settings()).openapi(), indent=2, sort_keys=True) + "\n"
if "--check" in sys.argv:
    if not path.exists() or path.read_text(encoding="utf-8") != content:
        raise SystemExit("Contract drift: run python scripts/export_contract.py")
else:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
