"""CI-only current-head probe: separate from the frozen UI74 historical replay.

The historical scripts/ui74_private_generation_ci.py and its workflow are
intentionally untouched. This file pins a NEW four-producer tuple and refuses
runtime promotion while MASTER admission remains pending.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

from scripts import ui74_private_generation_ci as frozen

BASE_SHA = "3b2eef8a4139195db1a775545e83f1c1f58bf403"
MASTER_SHA = "de95aef6ed46842063fc7c2ef766b7da75fe540e"
PRODUCER_PINS = {
    "DATA": "9d7e45be0872c4c5efa17c06bc107e1dc6a0d2fb",
    "RESEARCH": "af143bb73277e7bffea8ae76d84fd0ed7a707ab0",
    "UI": "2de6428b48e2b468d15aa8c9e40e4e47719e168f",
    "OPS": "9c0ee3a44e78d16d8beb34fbaf2973ee2e126386",
}
OPS_PUBLISHER_COMMIT = PRODUCER_PINS["OPS"]
OPS_PUBLISHER_BLOB = "d8e42103c15824d7ff316314127074ee983804b3"
REVIEWED_ALLOWLIST_BLOB = "08482cd45a3f46b5b8168aab2b7b19f63acb30fb"
AUDIT = Path(__file__).resolve().parents[1] / "docs/integration/ui74-current-head-audit.json"


def check_audit(source: Path) -> dict:
    record = json.loads(AUDIT.read_text(encoding="utf-8"))
    assert record["status"] == "CI_ONLY_UNAPPROVED_PROTOTYPE"
    assert record["pins"]["MASTER"] == MASTER_SHA
    assert record["pins"]["BASE"] == BASE_SHA
    assert record["pins"]["producers"] == PRODUCER_PINS
    assert record["pins"]["ops_publisher_blob"] == OPS_PUBLISHER_BLOB
    assert record["pins"]["enforced_allowlist_blob"] == REVIEWED_ALLOWLIST_BLOB
    assert record["scope"]["master_approval"] is False
    assert record["scope"]["real_f_admission"] is False
    assert record["scope"]["historical_coverage"] == "UNKNOWN"
    assert record["scope"]["committed_combined_sha"] is None
    assert len(record["ui_pending_paths"]) == 4
    for item in record["ui_pending_paths"]:
        name = item["path"]
        mode, blob = frozen.expected_mode_and_sha(source, PRODUCER_PINS["UI"], name)
        assert mode == 0o644
        assert blob == item["git_blob_sha1"]
        assert item["approved"] is False
    return record


def load_current_ops(path: Path):
    raw = path.read_bytes()
    if frozen.git_blob(raw) != OPS_PUBLISHER_BLOB:
        raise AssertionError("unreviewed current OPS publisher blob")
    spec = importlib.util.spec_from_file_location("ops_publisher_ui74_current", str(path))
    if spec is None or spec.loader is None:
        raise AssertionError("OPS publisher unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def source(root: Path):
    if frozen.git(root, "rev-parse", "HEAD").decode().strip() != MASTER_SHA:
        raise AssertionError("wrong current MASTER source checkout")
    for ref in (BASE_SHA, *PRODUCER_PINS.values()):
        if frozen.git(root, "rev-parse", ref + "^{commit}").decode().strip() != ref:
            raise AssertionError("missing exact current producer commit")
    allow = root / "docs/coordination/master-reviewed-producer-files.json"
    if frozen.git_blob(allow.read_bytes()) != REVIEWED_ALLOWLIST_BLOB:
        raise AssertionError("changed enforced allowlist")
    approved = json.loads(allow.read_text(encoding="utf-8"))["producers"]
    included = {}
    for line in frozen.git(root, "ls-tree", "-r", "-z", BASE_SHA).split(b"\0"):
        if not line:
            continue
        meta, name = line.split(b"\t", 1)
        parts = meta.decode().split()
        path = name.decode()
        if not frozen.selected(path):
            continue
        if len(parts) != 3 or parts[1] != "blob" or parts[0] not in ("100644", "100755"):
            raise AssertionError("unsafe baseline path " + path)
        included[path] = (BASE_SHA, int(parts[0], 8) & 0o777, parts[2], "BASE")
    overlaps = {}
    parity = {}
    for label, head in PRODUCER_PINS.items():
        paths = set()
        rows = frozen.git(root, "diff", "--name-status", "--no-renames", BASE_SHA, head).decode().splitlines()
        for row in rows:
            kind, path = row.split("\t", 1)
            if kind not in ("A", "M") or not path.startswith(
                (".github/", "docs/", "src/", "frontend/", "scripts/", "tests/")
            ):
                raise AssertionError("unexpected producer change")
            if path in {"src/integration/pc4_runtime.py", "src/api/worker.py", "pyproject.toml"}:
                raise AssertionError("protected native or package file changed")
            mode, blob = frozen.expected_mode_and_sha(root, head, path)
            previous = overlaps.get(path)
            if previous and previous[1] != blob:
                raise AssertionError("conflicting producer blob: " + path)
            overlaps[path] = (label, blob)
            paths.add(path)
            if frozen.selected(path):
                included[path] = (head, mode, blob, label)
        parity[label] = paths == set(approved[label])
    if not all(label in (*PRODUCER_PINS, "BASE") for _, _, _, label in included.values()):
        raise AssertionError("unknown producer label")
    audited = []
    for path in sorted(included):
        head, mode, sha, label = included[path]
        raw = frozen.git(root, "show", head + ":" + path)
        if frozen.git_blob(raw) != sha:
            raise AssertionError("Git blob bytes mismatch " + path)
        audited.append((path, raw, mode, label, sha))
    return audited, parity, {k: sum(v[3] == k for v in included.values()) for k in PRODUCER_PINS}


def build(args):
    if os.getenv("GITHUB_ACTIONS") != "true" or os.getenv("CI") != "true":
        raise AssertionError("disposable GitHub Actions only")
    root = Path(args.source).resolve()
    private = Path(args.root).resolve()
    temp = Path(os.environ["RUNNER_TEMP"]).resolve()
    if not private.is_relative_to(temp) or root.is_relative_to(private) or private.is_relative_to(root):
        raise AssertionError("private root is not isolated")
    check_audit(root)
    ops = load_current_ops(Path(args.publisher))
    audited, parity, counts = source(root)
    blobs = [ops.ReviewedBlob(p, raw, frozen.sha256(raw), sha, mode) for p, raw, mode, _, sha in audited]
    pins = {"BASE": BASE_SHA, "MASTER": MASTER_SHA, **PRODUCER_PINS, "OPS_PUBLISHER": OPS_PUBLISHER_COMMIT}
    manifest = ops.canonical(
        dict(
            schema=ops.SCHEMA,
            source_pins=pins,
            files=[
                dict(path=b.path, sha256=b.sha256, git_blob_sha1=b.git_blob_sha1, mode=b.mode)
                for b in blobs
            ],
        )
    )
    private.mkdir(mode=0o700)
    private.chmod(0o700)
    generation = ops.publish_generation(
        private, blobs=blobs, manifest=manifest,
        manifest_sha256=frozen.sha256(manifest), source_pins=pins,
    )
    if ops.read_current(private, manifest=manifest, manifest_sha256=frozen.sha256(manifest)) != generation:
        raise AssertionError("failed CURRENT verification")
    if ops.read_pinned(private, generation, manifest=manifest, manifest_sha256=frozen.sha256(manifest)) != generation:
        raise AssertionError("failed generation pin")
    receipt = json.loads((private / "generations" / generation / "generation.json").read_bytes())
    record = dict(
        status="CI_ONLY_CURRENT_HEAD_NOT_MASTER_PASS",
        generation=generation, tree_sha256=receipt["tree_sha256"],
        manifest_sha256=frozen.sha256(manifest), source_pins=pins,
        source_file_count=len(blobs), counts=counts,
        master_approved_allowlist_parity=parity,
        master_approval=False, external_attestation=False,
        real_market_coverage="UNKNOWN", admitted_to_f=False,
        committed_combined_sha=None,
    )
    Path(args.manifest_out).write_bytes(manifest)
    Path(args.receipt_out).write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
    print("UI74_CURRENT_PRIVATE_GENERATION", generation, receipt["tree_sha256"])
    print("UI74_MASTER_ALLOWLIST_PARITY", json.dumps(parity, sort_keys=True))
    print("UI74_CURRENT_FILES", len(blobs))


def verify(args):
    record = json.loads(Path(args.receipt_out).read_text())
    manifest = Path(args.manifest_out).read_bytes()
    if frozen.sha256(manifest) != record["manifest_sha256"]:
        raise AssertionError("manifest mutated")
    ops = load_current_ops(Path(args.publisher))
    root = Path(args.root).resolve()
    for read in (
        ops.read_current(root, manifest=manifest, manifest_sha256=record["manifest_sha256"]),
        ops.read_pinned(root, record["generation"], manifest=manifest, manifest_sha256=record["manifest_sha256"]),
    ):
        if read != record["generation"]:
            raise AssertionError("generation pin invalid")
    receipt = json.loads((root / "generations" / record["generation"] / "generation.json").read_bytes())
    if receipt["tree_sha256"] != record["tree_sha256"]:
        raise AssertionError("private generation tree changed")
    print("UI74_CURRENT_GENERATION_VERIFIED", record["tree_sha256"])


def serve(args):
    verify(args)
    record = json.loads(Path(args.receipt_out).read_text())
    generation = (Path(args.root) / "generations" / record["generation"]).resolve()
    sys.path.insert(0, str(generation))
    import uvicorn

    import src.api.main as app_module
    from scripts.capability_fixture_server import CiReferenceReader
    from src.api.config import Settings
    from src.api.main import create_app

    if not Path(app_module.__file__).resolve().is_relative_to(generation):
        raise AssertionError("API not imported from sealed generation")
    runtime = Path(args.runtime).resolve()
    runtime.mkdir(parents=True, mode=0o700, exist_ok=True)
    app = create_app(
        Settings(provider_factory="src.integration.reference_prices:create_reader",
                 market_catalog_enabled=False, frontend_dir=generation / "frontend", data_dir=runtime),
        provider=CiReferenceReader(),
    )
    print("UI74_CURRENT_API_SOURCE", app_module.__file__, flush=True)
    uvicorn.run(app, host="127.0.0.1", port=18874, access_log=False)


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("build", "verify", "serve"))
    for flag in ("source", "root", "publisher", "manifest-out", "receipt-out", "runtime"):
        parser.add_argument("--" + flag, required=flag in {"root", "publisher", "manifest-out", "receipt-out"})
    args = parser.parse_args()
    {"build": build, "verify": verify, "serve": serve}[args.action](args)


if __name__ == "__main__":
    main()
