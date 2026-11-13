"""Read-only, narrow OpenAPI Kind drift diagnosis and proposed v1.1 delta.

The pinned v1 contract is immutable. The candidate does not register new
routes or write files and must be explicitly approved by the contract owner.
"""

import json
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINNED = ROOT / "contracts/v1/openapi.json"
PROPOSAL = ROOT / "docs/proposals/download-kind-v1_1.json"


def compare_contracts(pinned=None, proposal=None, runtime=None):
    if pinned is None:
        pinned = json.loads(PINNED.read_text(encoding="utf-8"))
    if proposal is None:
        proposal = json.loads(PROPOSAL.read_text(encoding="utf-8"))
    if runtime is None:
        from src.api.config import Settings
        from src.api.main import create_app

        runtime = create_app(Settings()).openapi()

    current = pinned["components"]["schemas"]["DownloadRequest"]["properties"]["kind"]["enum"]
    assert proposal["status"] == "CONTRACT_OWNER_REVIEW_REQUIRED"
    assert proposal["activated"] is False
    assert proposal["target"] == "additive_opt_in_v1_1_only"
    assert current == proposal["legacy_kinds"]
    assert proposal["proposed_kinds"] == [*current, "mark", "index"]
    candidate = deepcopy(pinned)
    candidate["components"]["schemas"]["DownloadRequest"]["properties"]["kind"]["enum"] = proposal[
        "proposed_kinds"
    ]
    # The sole drift allowed by this diagnosis is the expanded Kind enum.
    return {
        "legacy_v1_unchanged": pinned["components"]["schemas"]["DownloadRequest"]["properties"][
            "kind"
        ]["enum"]
        == proposal["legacy_kinds"],
        "runtime_equals_single_enum_delta": runtime == candidate,
        "runtime_kind_enum": runtime["components"]["schemas"]["DownloadRequest"]["properties"][
            "kind"
        ]["enum"],
        "pinned_kind_enum": current,
        "new_v1_1_endpoint_activated": False,
        "approval_required": True,
    }


if __name__ == "__main__":
    print(json.dumps(compare_contracts(), indent=2, sort_keys=True))
