"""Fail-closed, SHA-bound mandatory pytest evidence. No raw logs in retained artifacts."""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

GUARD_CLASS = "tester.ci.evidence_gate"
GUARD_NAME = "test_process_completed_and_verified"


def expected_case_identity(node):
    parts = node.split("::")
    if len(parts) not in (2, 3) or not parts[0].startswith("tests/") or not parts[0].endswith(".py"):
        raise ValueError("Invalid mandatory node")
    module = parts[0][:-3].replace("/", ".")
    return module + ("." + parts[1] if len(parts) == 3 else ""), parts[-1]


def _totals(element, expected_count, *, mandatory=True):
    for attr, expected in (("tests", expected_count), ("failures", 0), ("errors", 0), ("skipped", 0)):
        actual = element.get(attr)
        if actual is None:
            if mandatory:
                raise ValueError("Missing JUnit total: " + attr)
            continue
        if not actual.isdecimal() or int(actual) != expected:
            raise ValueError("Wrong JUnit total: " + attr)


def check_junit(root, expected_nodes):
    """Validate real pytest xunit2: testsuites > testsuite > direct testcase."""
    required = [expected_case_identity(n) for n in expected_nodes]
    if not required or len(required) != len(set(required)):
        raise ValueError("Empty or duplicate expected test identities")
    if root.tag != "testsuites":
        raise ValueError("JUnit root must be testsuites")
    children = list(root)
    if len(children) != 1 or children[0].tag != "testsuite":
        raise ValueError("Exactly one direct JUnit testsuite required")
    suite = children[0]
    _totals(root, len(required), mandatory=False)
    _totals(suite, len(required))
    if any(x.tag not in ("testcase", "properties", "system-out", "system-err") for x in suite):
        raise ValueError("Unknown or nested testsuite child")
    cases = [x for x in suite if x.tag == "testcase"]
    if len(cases) != len(list(root.iter("testcase"))):
        raise ValueError("Nested or detached JUnit testcase")
    identities = [(x.get("classname"), x.get("name")) for x in cases]
    if (len(identities) != len(required) or len(set(identities)) != len(identities)
            or set(identities) != set(required)):
        raise ValueError("JUnit classname/name inventory mismatch")
    for case in cases:
        if case.get("status") not in (None, "PASS"):
            raise ValueError("Non-PASS JUnit status attribute")
        if any(tag.tag in ("failure", "error", "skipped") for tag in case.iter() if tag is not case):
            raise ValueError("Mandatory JUnit testcase has non-PASS status")
        if any("xpass" in str(v).lower() or "xfail" in str(v).lower() for v in case.attrib.values()):
            raise ValueError("XPASS/XFAIL in testcase attribute")
        if any("xpass" in str(v).lower() or "xfail" in str(v).lower()
               for el in case for v in el.attrib.values()):
            raise ValueError("XPASS/XFAIL in testcase annotation")
        if any(x.tag not in ("properties", "system-out", "system-err") for x in case):
            raise ValueError("Unexpected JUnit testcase child")
    return identities


def reset_artifact(artifact):
    """Prevent prior PASS surviving a new attempt, including timeout before XML."""
    artifact = Path(artifact)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.unlink(missing_ok=True)
    artifact.with_suffix(".json").unlink(missing_ok=True)


def _write_json_atomic(path, payload):
    path = Path(path)
    stage = path.with_name(path.name + ".pending")
    stage.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(stage, path)


def validate_receipt(artifact):
    """Compare retained sanitized XML, JSON, case identities, totals and SHA256."""
    artifact = Path(artifact)
    receipt = json.loads(artifact.with_suffix(".json").read_text(encoding="utf-8"))
    xmlbytes = artifact.read_bytes()
    if hashlib.sha256(xmlbytes).hexdigest() != receipt["sanitized_xml_sha256"]:
        raise ValueError("Retained XML checksum does not match JSON receipt")
    root = ET.fromstring(xmlbytes)
    if root.tag != "testsuites" or len(root) != 1 or root[0].tag != "testsuite":
        raise ValueError("Retained XML has invalid root/suite shape")
    suite = root[0]
    cases = list(suite)
    if any(x.tag != "testcase" for x in cases) or len(cases) != len(list(root.iter("testcase"))):
        raise ValueError("Retained XML case membership mismatch")
    observed = []
    statuses = {"PASS": 0, "FAIL": 0}
    for case in cases:
        classname, name, status = case.get("classname"), case.get("name"), case.get("status")
        if not classname or not name or status not in statuses:
            raise ValueError("Invalid sanitized testcase identity or status")
        statuses[status] += 1
        children = list(case)
        if status == "PASS" and children:
            raise ValueError("PASS testcase contains failure marker")
        if status == "FAIL" and (len(children) != 1 or children[0].tag != "failure"):
            raise ValueError("FAIL testcase must have exactly one redacted failure")
        observed.append([classname, name, status])
    if observed != receipt["observed"]:
        raise ValueError("XML and JSON testcase inventory/status mismatch")
    if any(suite.get(k) != str(v) for k, v in (("tests", len(cases)), ("failures", statuses["FAIL"]), ("errors", 0), ("skipped", 0))):
        raise ValueError("Sanitized suite counters disagree with cases")
    if any(root.get(k) != suite.get(k) for k in ("tests", "failures", "errors", "skipped")):
        raise ValueError("Sanitized root counters disagree with suite")
    is_complete = (receipt["status"] == "PASS" and statuses["FAIL"] == 0 and
                   receipt["process_returncode"] == 0 and receipt["timed_out"] is False and
                   len(cases) == len(receipt["expected"]) and
                   [[a, b] for a, b, _ in observed] == receipt["expected"])
    if receipt["status"] == "PASS" and not is_complete:
        raise ValueError("False PASS in JSON receipt")
    if receipt["status"] == "NOT_VERIFIED" and statuses["FAIL"] == 0:
        raise ValueError("NOT_VERIFIED receipt lacks gate failure")
    if receipt["status"] not in ("PASS", "NOT_VERIFIED"):
        raise ValueError("Unknown receipt status")
    if receipt.get("schema") != "tester.ci.named-junit.v2":
        raise ValueError("Unknown evidence receipt schema")
    if not re.fullmatch(r"[0-9a-f]{40}", receipt["head_sha"]):
        raise ValueError("Missing source identity")
    if receipt["postgres_major"] not in ("16", "18"):
        raise ValueError("Unverified database major")
    return receipt


def safe_artifact(xml_source, artifact, expected_nodes, *, head_sha, postgres_major,
                  suite_name, process_returncode, timed_out):
    """Write redacted evidence. PASS requires completed process exit 0 and valid XML."""
    reset_artifact(artifact)
    artifact = Path(artifact)
    expected = [list(expected_case_identity(node)) for node in expected_nodes]
    if not expected:
        raise ValueError("Empty mandatory inventory")
    verified = False
    reason = ""
    try:
        if not re.fullmatch(r"[0-9a-f]{40}", head_sha) or str(postgres_major) not in ("16", "18"):
            raise ValueError("Invalid source SHA or actual PG major")
        if timed_out is not False:
            raise ValueError("ProcessTimedOut")
        if type(process_returncode) is not int or process_returncode != 0:
            raise ValueError("ProcessNonzeroOrNotCompleted")
        check_junit(ET.parse(xml_source).getroot(), expected_nodes)
        verified = True
    except (ValueError, ET.ParseError, OSError, TypeError) as exc:
        reason = type(exc).__name__ + ("_" + str(exc)[:60] if isinstance(exc, ValueError) else "")
    rows = [[*identity, "PASS"] for identity in expected]
    if not verified:
        rows.append([GUARD_CLASS, GUARD_NAME, "FAIL"])
    fail_count = 0 if verified else 1
    attrs = {"tests": str(len(rows)), "failures": str(fail_count), "errors": "0", "skipped": "0"}
    root = ET.Element("testsuites", attrs)
    suite = ET.SubElement(root, "testsuite", {"name": suite_name, **attrs})
    for classname, name, status in rows:
        case = ET.SubElement(suite, "testcase", {"classname": classname, "name": name, "status": status})
        if status == "FAIL":
            ET.SubElement(case, "failure", {"message": "mandatory CI evidence not verified; details redacted"})
    artifact.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    receipt = {
        "schema": "tester.ci.named-junit.v2", "head_sha": head_sha,
        "postgres_major": str(postgres_major), "suite": suite_name,
        "expected": expected, "observed": rows,
        "status": "PASS" if verified else "NOT_VERIFIED",
        "reason_class": "" if verified else "EvidenceNotVerified",
        "process_returncode": process_returncode,
        "timed_out": timed_out,
        "sanitized_xml_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
    }
    _write_json_atomic(artifact.with_suffix(".json"), receipt)
    validate_receipt(artifact)
    if not verified:
        raise ValueError(f"{suite_name}: mandatory evidence NOT_VERIFIED ({reason})")
    return receipt


def initialize_repetition_manifest(path, *, head_sha, postgres_major, expected_repetitions):
    if expected_repetitions != 2:
        raise ValueError("Both mandatory DB repetitions required")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(path, {"head_sha": head_sha, "postgres_major": str(postgres_major),
                              "expected_repetitions": 2, "status": "NOT_VERIFIED", "repetitions": []})


def complete_repetition_manifest(path, artifacts, *, head_sha, postgres_major):
    """Only a pair of independently validated retained XML+JSON can become PASS."""
    path = Path(path)
    prior = json.loads(path.read_text(encoding="utf-8"))
    if (prior.get("status") != "NOT_VERIFIED" or prior.get("head_sha") != head_sha or
            prior.get("postgres_major") != str(postgres_major) or prior.get("expected_repetitions") != 2 or
            len(artifacts) != 2 or len(set(map(str, artifacts))) != 2):
        raise ValueError("Invalid two-repeat manifest transition")
    rows = [validate_receipt(p) for p in artifacts]
    if any(x["status"] != "PASS" or x["head_sha"] != head_sha or
           x["postgres_major"] != str(postgres_major) or len(x["expected"]) != 5 for x in rows):
        raise ValueError("One or more mandatory database repetitions not verified")
    _write_json_atomic(path, {"head_sha": head_sha, "postgres_major": str(postgres_major),
                              "expected_repetitions": 2, "status": "PASS",
                              "repetitions": [{"suite": x["suite"], "sanitized_xml_sha256": x["sanitized_xml_sha256"]} for x in rows]})


def assert_ci_source(expected_head_sha):
    if os.getenv("GITHUB_ACTIONS") != "true":
        raise RuntimeError("Mandatory CI requires GitHub Actions")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if not expected_head_sha or head != expected_head_sha or not re.fullmatch(r"[0-9a-f]{40}", head):
        raise RuntimeError("Expected source SHA does not equal checked out commit")
    return head
