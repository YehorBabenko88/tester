"""Read-only baseline contract drift diagnosis; NEVER regenerates pinned schemas."""

import difflib
import hashlib
import json
from pathlib import Path

from src.api.config import Settings
from src.api.financial_query import FinancialRequest
from src.api.main import create_app

ROOT = Path(__file__).resolve().parents[1]


def _check(path, proposed):
    pinned = (ROOT / path).read_text(encoding="utf-8")
    before = pinned.splitlines()
    after = proposed.splitlines()
    change = list(
        difflib.unified_diff(
            before,
            after,
            fromfile="pinned/" + path,
            tofile="current-runtime/" + path,
            lineterm="",
        )
    )
    return {
        "path": path,
        "matches_pinned": pinned == proposed,
        "pinned_sha256": hashlib.sha256(pinned.encode()).hexdigest(),
        "runtime_sha256": hashlib.sha256(proposed.encode()).hexdigest(),
        "first_changed_lines": change[:50],
        "full_diff_line_count": len(change),
    }


def diagnose():
    openapi = json.dumps(create_app(Settings()).openapi(), indent=2, sort_keys=True) + "\n"
    request = FinancialRequest.model_json_schema()
    request["$id"] = "urn:tester:requests:financial-query:1.0"
    request["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    financial = json.dumps(request, indent=2, sort_keys=True) + "\n"
    return {
        "read_only": True,
        "no_contract_regeneration": True,
        "checks": [
            _check("contracts/v1/openapi.json", openapi),
            _check("requests/v1/financial-query.schema.json", financial),
        ],
    }


if __name__ == "__main__":
    print(json.dumps(diagnose(), indent=2, sort_keys=True))
