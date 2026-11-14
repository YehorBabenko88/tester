"""CI-only UI probe on a genuine OPS privately published source generation.

The source overlay is composed by the reviewed MASTER runner separately.
This driver NEVER edits the MASTER checkout or activates an installed launcher.
Authenticity of Git, the caller, and the runner is NOT independently attested.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

MASTER_SHA = "5ee448fa6e0c356034e33b6f54f2ddbf6bdbc1cd"
BASE_SHA = "3b2eef8a4139195db1a775545e83f1c1f58bf403"
PRODUCER_PINS = {
    "DATA": "d5bfdad445a1eefb9dc12bf2725a00f171e2c321",
    "RESEARCH": "7ec35810434e3c9159f36712a4186595ac637efa",
    "UI": "a61a2c3bceec8747d7b2424d91c9a4a6c3ec9d96",
    "OPS": "0d4649f3a7cd3595ea58757f2abaf2f1b0558667",
}
OPS_PUBLISHER_COMMIT = "113d7f3b97f47c86a19dc8590bdbb9809138c4d8"
OPS_PUBLISHER_BLOB = "1a28603452efaaf9d69e4446b5ee2bf8bf7a5d9e"
REVIEWED_ALLOWLIST_BLOB = "08482cd45a3f46b5b8168aab2b7b19f63acb30fb"
INCLUDE_PREFIXES = ("src/", "scripts/", "frontend/", "tests/")
INCLUDE_DOCS = (
    "docs/integration/",
    "docs/ingest/",
    "docs/proposals/",
    "docs/ui-capability-",
    "docs/coordination/master-reviewed-producer-files.json",
)
EXCLUDE = {"scripts/master_disposable_compose.py"}
HEX40 = re.compile(r"[a-f0-9]{40}\Z")


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def git_blob(raw):
    return hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()


def git(root, *args):
    return subprocess.check_output(("git", "-C", str(root), *args), stderr=subprocess.PIPE)


def selected(path):
    return path not in EXCLUDE and (
        path.startswith(INCLUDE_PREFIXES) or path.startswith(INCLUDE_DOCS)
    )


def load_publisher(path):
    source = Path(path).read_bytes()
    if git_blob(source) != OPS_PUBLISHER_BLOB:
        raise AssertionError("unreviewed OPS prototype code")
    spec = importlib.util.spec_from_file_location("ops_publisher_ui74", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def expected_mode_and_sha(root, commit, path):
    line = git(root, "ls-tree", commit, "--", path).decode().strip()
    match = re.fullmatch(r"(100644|100755) blob ([a-f0-9]{40})\t(.+)", line)
    if not match or match.group(3) != path:
        raise AssertionError("nonregular or unreviewed source path: " + path)
    return int(match.group(1), 8) & 0o777, match.group(2)


def reviewed_source(root):
    """Derive private source bytes directly from immutable Git objects.

    This bypasses the unapproved MASTER #77 checkout composer. It is NOT
    evidence that the MASTER reviewed allowlist currently matches its pins.
    No active or disposable checkout file is modified by this function.
    """
    if git(root, "rev-parse", "HEAD").decode().strip() != MASTER_SHA:
        raise AssertionError("incorrect MASTER staging checkout")
    for ref in (BASE_SHA, *PRODUCER_PINS.values(), OPS_PUBLISHER_COMMIT):
        if git(root, "rev-parse", ref + "^{commit}").decode().strip() != ref:
            raise AssertionError("unavailable pinned source commit")
    allowlist = root / "docs/coordination/master-reviewed-producer-files.json"
    if git_blob(allowlist.read_bytes()) != REVIEWED_ALLOWLIST_BLOB:
        raise AssertionError("MASTER reviewed allowlist changed")
    approved = json.loads(allowlist.read_bytes())["producers"]

    entries = {}
    for item in git(root, "ls-tree", "-r", "-z", BASE_SHA).split(b"\0"):
        if not item:
            continue
        metadata, name = item.split(b"\t", 1)
        parts = metadata.decode().split()
        path = name.decode()
        if not selected(path):
            continue
        if len(parts) != 3 or parts[1] != "blob" or parts[0] not in {"100644", "100755"}:
            raise AssertionError("special baseline source file: " + path)
        entries[path] = (BASE_SHA, int(parts[0], 8) & 0o777, parts[2], "BASE")

    all_paths = {}
    counts = Counter()
    allowlist_parity = {}
    for label, commit in PRODUCER_PINS.items():
        rows = (
            git(root, "diff", "--name-status", "--no-renames", BASE_SHA, commit)
            .decode()
            .splitlines()
        )
        observed = set()
        for row in rows:
            kind, path = row.split("\t", 1)
            if kind not in ("A", "M") or not (
                path.startswith((".github/", "docs/", "src/", "frontend/", "scripts/", "tests/"))
            ):
                raise AssertionError("unsupported or removed producer entry")
            if path in {"src/integration/pc4_runtime.py", "src/api/worker.py", "pyproject.toml"}:
                raise AssertionError("protected active-launcher mutation")
            if path in observed:
                raise AssertionError("duplicate source entry")
            observed.add(path)
            mode, blob = expected_mode_and_sha(root, commit, path)
            previous = all_paths.get(path)
            if previous and previous[1] != blob:
                raise AssertionError("conflicting producer blob: " + path)
            all_paths[path] = (label, blob)
            counts[label] += 1
            if selected(path):
                entries[path] = (commit, mode, blob, label)
        allowlist_parity[label] = observed == set(approved[label])
    if not any(label == "UI" for _, _, _, label in entries.values()):
        raise AssertionError("reviewed UI code missing")
    # No 'MASTER composer PASS' claim: the reviewed allowlist parity is a
    # separately reported prerequisite and may currently be false.
    audited = []
    for name in sorted(entries):
        commit, mode, blob, label = entries[name]
        raw = git(root, "show", commit + ":" + name)
        if git_blob(raw) != blob:
            raise AssertionError("immutable Git object mismatch: " + name)
        audited.append((name, raw, mode, label, blob))
    return audited, sum(counts.values()), allowlist_parity


def build(args):
    if os.getenv("CI") != "true" or os.getenv("GITHUB_ACTIONS") != "true":
        raise AssertionError("ephemeral GitHub Actions only")
    temp = Path(os.environ["RUNNER_TEMP"]).resolve()
    source = Path(args.source).resolve()
    root = Path(args.root).resolve()
    if not root.is_relative_to(temp) or source.is_relative_to(root):
        raise AssertionError("private workspace must be outside source checkout")
    ops = load_publisher(args.publisher)
    reviewed, changed_count, allowlist_parity = reviewed_source(source)
    blobs = [
        ops.ReviewedBlob(name, raw, sha256(raw), expected_sha, mode)
        for name, raw, mode, _, expected_sha in reviewed
    ]
    pins = {
        "BASE": BASE_SHA,
        "MASTER": MASTER_SHA,
        **PRODUCER_PINS,
        "OPS_PUBLISHER": OPS_PUBLISHER_COMMIT,
    }
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
    manifest_sha = sha256(manifest)
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    generation = ops.publish_generation(
        root,
        blobs=blobs,
        manifest=manifest,
        manifest_sha256=manifest_sha,
        source_pins=pins,
    )
    if ops.read_current(root, manifest=manifest, manifest_sha256=manifest_sha) != generation:
        raise AssertionError("CURRENT was not verified")
    if (
        ops.read_pinned(root, generation, manifest=manifest, manifest_sha256=manifest_sha)
        != generation
    ):
        raise AssertionError("published pin was not verified")
    receipt = json.loads((root / "generations" / generation / "generation.json").read_bytes())
    counts = dict(Counter(label for _, _, _, label, _ in reviewed))
    record = dict(
        status="PRIVATE_GENERATION_PROTOTYPE_ONLY",
        source_git_attestation="NOT_EXTERNALLY_ATTESTED",
        committed_combined_head="NOT_CREATED",
        trust_boundary="SAME_UID_AND_PARENT_RELOCATION_BLOCKED",
        crash_durability="NOT_ATTESTED",
        manifest_sha256=manifest_sha,
        generation=generation,
        tree_sha256=receipt["tree_sha256"],
        source_pins=pins,
        reviewed_producer_changed_files=changed_count,
        master_reviewed_allowlist_parity=allowlist_parity,
        source_publication_method="DIRECT_PINNED_GIT_BLOBS_PRIVATE_0700",
        generation_source_files=len(blobs),
        source_counts=counts,
        ui_git_blobs={name: b for name, _, _, label, b in reviewed if label == "UI"},
    )
    Path(args.manifest_out).write_bytes(manifest)
    Path(args.receipt_out).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print("UI74_PRIVATE_GENERATION_PUBLISHED", generation)
    print("UI74_PRIVATE_TREE_SHA256", receipt["tree_sha256"])
    print("UI74_PRIVATE_GENERATION_SOURCE_FILES", len(blobs))
    print("UI74_PRIVATE_GENERATION_UI_FILES", counts.get("UI", 0))


def verify(args):
    ops = load_publisher(args.publisher)
    root = Path(args.root).resolve()
    record = json.loads(Path(args.receipt_out).read_text())
    manifest = Path(args.manifest_out).read_bytes()
    if sha256(manifest) != record["manifest_sha256"]:
        raise AssertionError("private manifest mutated")
    if (
        ops.read_current(root, manifest=manifest, manifest_sha256=record["manifest_sha256"])
        != record["generation"]
    ):
        raise AssertionError("CURRENT moved or became invalid")
    if (
        ops.read_pinned(
            root, record["generation"], manifest=manifest, manifest_sha256=record["manifest_sha256"]
        )
        != record["generation"]
    ):
        raise AssertionError("pinned generation invalid")
    receipt = json.loads(
        (root / "generations" / record["generation"] / "generation.json").read_bytes()
    )
    if receipt["tree_sha256"] != record["tree_sha256"]:
        raise AssertionError("source generation digest changed")
    print("UI74_PRIVATE_GENERATION_STILL_PINNED", record["tree_sha256"])


def serve(args):
    root = Path(args.root).resolve()
    record = json.loads(Path(args.receipt_out).read_text())
    generation = root / "generations" / record["generation"]
    if not generation.is_dir():
        raise AssertionError("unavailable generation")
    sys.path.insert(0, str(generation))
    import uvicorn

    import src.api.main as main_module
    from scripts.capability_fixture_server import CiReferenceReader
    from src.api.config import Settings
    from src.api.main import create_app

    if not Path(main_module.__file__).resolve().is_relative_to(generation):
        raise AssertionError("FastAPI application imported outside private generation")
    runtime = Path(args.runtime).resolve()
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    app = create_app(
        Settings(
            provider_factory="src.integration.reference_prices:create_reader",
            market_catalog_enabled=False,
            frontend_dir=generation / "frontend",
            data_dir=runtime,
        ),
        provider=CiReferenceReader(),
    )
    print("UI74_PRIVATE_FASTAPI_SOURCE", main_module.__file__, flush=True)
    uvicorn.run(app, host="127.0.0.1", port=18874, access_log=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "verify", "serve"))
    parser.add_argument("--source")
    parser.add_argument("--root", required=True)
    parser.add_argument("--publisher", required=True)
    parser.add_argument("--composed-manifest")
    parser.add_argument("--manifest-out", required=True)
    parser.add_argument("--receipt-out", required=True)
    parser.add_argument("--runtime")
    args = parser.parse_args()
    {"build": build, "verify": verify, "serve": serve}[args.action](args)


if __name__ == "__main__":
    main()
