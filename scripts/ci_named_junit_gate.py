"""Shared fail-closed JUnit identity and sanitized artifact contract.

Never accept stdout PASS banners, skipped/xfail/xpass or an incomplete suite.
This verifier has no network/DB interactions and writes only CI-local evidence.
"""
import hashlib
import json
import os
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET


def expected_case_identity(node):
    bits = node.split("::")
    if len(bits) not in (2, 3) or not bits[0].startswith("tests/") or not bits[0].endswith(".py"):
        raise ValueError("Unexpected mandatory test node specification")
    module = bits[0][:-3].replace("/", ".")
    classname = module + ("." + bits[1] if len(bits) == 3 else "")
    return classname, bits[-1]


def check_junit(root, expected_nodes):
    """Require exact (classname, name), suite totals and purely PASS results."""
    required = [expected_case_identity(n) for n in expected_nodes]
    if len(required) != len(set(required)) or not required:
        raise ValueError("Duplicate or empty mandatory JUnit specification")
    suites = list(root.iter("testsuite"))
    cases = list(root.iter("testcase"))
    if len(suites) != 1:
        raise ValueError("Expected exactly one JUnit testsuite")
    suite = suites[0]
    observed = [(c.get("classname"), c.get("name")) for c in cases]
    if len(observed) != len(required) or len(set(observed)) != len(observed) or set(observed) != set(required):
        raise ValueError(f"JUnit classname+name mismatch: expected={required!r}; got={observed!r}")
    if any(not a or not b for a, b in observed):
        raise ValueError("Empty JUnit classname/name")
    for key, want in (("tests", len(required)), ("skipped", 0), ("failures", 0), ("errors", 0)):
        if suite.get(key) is None or int(suite.get(key)) != want:
            raise ValueError(f"JUnit suite {key} mismatch: {suite.get(key)!r} != {want}")
    for case in cases:
        if any(case.find(x) is not None for x in ("skipped", "failure", "error")):
            raise ValueError("JUnit mandatory testcase not PASS")
        # A non-strict xpass can otherwise masquerade as a PASS case.
        if any("xpass" in str(v).lower() or "xfail" in str(v).lower()
               for v in case.attrib.values()):
            raise ValueError("XPASS/XFAIL marker in JUnit testcase")
        if any("xpass" in str(c.get("message", "")).lower() or "xfail" in str(c.get("message", "")).lower()
               for c in case):
            raise ValueError("XPASS/XFAIL marker in JUnit child")
    return observed


def safe_artifact(xml_source, artifact, expected_nodes, *, head_sha, postgres_major, suite_name):
    """Persist a SANITIZED XML identity/status receipt, never raw pytest traceback or DSNs."""
    artifact = Path(artifact)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    cases = []
    outcome = "NOT_VERIFIED"
    error = ""
    try:
        root = ET.parse(xml_source).getroot()
        for c in root.iter("testcase"):
            status = "PASS"
            if c.find("skipped") is not None:
                status = "SKIP_OR_XFAIL"
            elif c.find("failure") is not None:
                status = "FAIL_OR_XPASS"
            elif c.find("error") is not None:
                status = "ERROR"
            cases.append((c.get("classname", ""), c.get("name", ""), status))
        check_junit(root, expected_nodes)
        outcome = "PASS"
    except (OSError, ET.ParseError, ValueError, TypeError) as exc:
        error = type(exc).__name__
    safe = ET.Element("testsuites")
    suite = ET.SubElement(safe, "testsuite", {
        "name": suite_name,
        "tests": str(len(cases)),
        "failures": str(sum(c[2] == "FAIL_OR_XPASS" for c in cases)),
        "errors": str(sum(c[2] == "ERROR" for c in cases)),
        "skipped": str(sum(c[2] == "SKIP_OR_XFAIL" for c in cases)),
    })
    for classname, name, status in cases:
        row = ET.SubElement(suite, "testcase", {
            "classname": classname, "name": name, "status": status,
        })
        if status != "PASS":
            ET.SubElement(row, "failure", {"message": "details redacted; review original CI logs"})
    ET.ElementTree(safe).write(artifact, encoding="utf-8", xml_declaration=True)
    receipt = {
        "schema": "tester.ci.named-junit.v1",
        "head_sha": head_sha,
        "postgres_major": str(postgres_major),
        "suite": suite_name,
        "expected": [list(expected_case_identity(x)) for x in expected_nodes],
        "observed": [list(x) for x in cases],
        "status": outcome,
        "reason_class": error,
        "sanitized_xml_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
    }
    artifact.with_suffix(".json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if outcome != "PASS":
        raise ValueError(f"{suite_name}: mandatory JUnit not PASS ({error})")
    return receipt


def assert_ci_source(expected_head_sha):
    if os.getenv("GITHUB_ACTIONS") != "true":
        raise RuntimeError("Mandatory evidence runner requires GitHub Actions")
    checkout = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip()
    if not expected_head_sha or len(expected_head_sha) != 40 or checkout != expected_head_sha:
        raise RuntimeError("Checked-out source SHA does not match expected PR head SHA")
    return checkout
