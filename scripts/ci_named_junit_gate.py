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
# Immutable contract, shared by the runner and the accepting manifest.
CANONICAL_DATA_RESEARCH_NODES = (
    "tests/test_public_auxiliary_postgres.py::AuxiliaryPostgresTests::test_store_transaction_fault_after_checkpoint_rolls_back_all_pg_publication",
    "tests/platform/test_analytics_readback_integration_round3.py::test_disposable_pg_persisted_original_http_to_actual_api_and_no_f",
    "tests/platform/test_analytics_readback_integration_round3.py::test_round7_actual_postgresql_cancels_bounded_store_statement",
    "tests/platform/test_analytics_readback_integration_round3.py::test_round13_two_real_postgresql_readers_overlap_and_release",
    "tests/api/test_analytics_readback_round3.py::test_round8_two_real_parallel_requests_hold_slots_third_denied_and_released",
)


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
    """Reparse retained evidence; UNKNOWN/NOT_EXECUTED are not successes."""
    artifact = Path(artifact)
    receipt = json.loads(artifact.with_suffix(".json").read_text(encoding="utf-8"))
    raw = artifact.read_bytes()
    if hashlib.sha256(raw).hexdigest() != receipt["sanitized_xml_sha256"]:
        raise ValueError("Retained XML SHA256 disagrees with receipt")
    root = ET.fromstring(raw)
    if root.tag != "testsuites" or len(root) != 1 or root[0].tag != "testsuite":
        raise ValueError("Retained XML hierarchy invalid")
    suite = root[0]
    cases = list(suite)
    if any(c.tag != "testcase" for c in cases) or len(list(root.iter("testcase"))) != len(cases):
        raise ValueError("Retained XML has detached/hidden case")
    if suite.get("name") != receipt["suite"]:
        raise ValueError("Suite identity differs between JSON and XML")
    if suite.get("attempt_id") != (receipt.get("attempt_id") or ""):
        raise ValueError("Attempt ID differs between JSON and XML")
    if suite.get("repeat_ordinal") != (str(receipt.get("repeat_ordinal") or "")):
        raise ValueError("Repeat ordinal differs between JSON and XML")
    if receipt.get("attempt_id") is not None and not re.fullmatch(r"[0-9a-f]{32}", receipt["attempt_id"]):
        raise ValueError("Invalid attempt identity")
    if receipt.get("repeat_ordinal") not in (None, 1, 2):
        raise ValueError("Invalid repeat identity")
    statuses = ("PASS", "FAIL", "UNKNOWN", "NOT_EXECUTED")
    observed = []
    fail_count = 0
    skipped_count = 0
    for case in cases:
        classname, name, status = (case.get("classname"), case.get("name"), case.get("status"))
        if not classname or not name or status not in statuses:
            raise ValueError("Invalid case identity/status")
        children = list(case)
        if status == "FAIL":
            if len(children) != 1 or children[0].tag != "failure":
                raise ValueError("FAIL must have one redacted marker")
            fail_count += 1
        elif status in ("UNKNOWN", "NOT_EXECUTED"):
            if (len(children) != 1 or children[0].tag != "skipped" or
                    children[0].get("message") != "mandatory evidence not verified" or
                    list(children[0])):
                raise ValueError("Unverified case requires one redacted skipped marker")
            skipped_count += 1
        elif children:
            raise ValueError("PASS case cannot have outcome children")
        observed.append([classname, name, status])
    if observed != receipt["observed"]:
        raise ValueError("XML/JSON observed case mismatch")
    for key, count in (("tests", len(cases)), ("failures", fail_count),
                       ("errors", 0), ("skipped", skipped_count)):
        if root.get(key) != str(count) or suite.get(key) != str(count):
            raise ValueError("XML aggregate mismatch")
    expected = receipt["expected"]
    if (len(expected) == 0 or len(set(tuple(x) for x in expected)) != len(expected)
            or [[a, b] for a, b, _ in observed[:len(expected)]] != expected):
        raise ValueError("Expected cases and observed cases disagree")
    complete = (type(receipt["process_returncode"]) is int and receipt["process_returncode"] == 0 and receipt["timed_out"] is False
                and len(observed) == len(expected) and skipped_count == 0
                and all(x[2] == "PASS" for x in observed))
    if receipt["status"] == "PASS":
        if not complete:
            raise ValueError("False global PASS")
    elif receipt["status"] == "NOT_VERIFIED":
        if (len(observed) != len(expected) + 1 or
                observed[-1] != [GUARD_CLASS, GUARD_NAME, "FAIL"] or
                any(x[2] == "PASS" for x in observed[:len(expected)])):
            raise ValueError("NOT_VERIFIED has unproven PASS or missing guard")
    else:
        raise ValueError("Unexpected overall evidence status")
    if receipt.get("schema") != "tester.ci.named-junit.v3":
        raise ValueError("Unrecognized receipt schema")
    if not re.fullmatch(r"[0-9a-f]{40}", receipt["head_sha"]):
        raise ValueError("Invalid checkout identity")
    if receipt["postgres_major"] not in ("16", "18"):
        raise ValueError("Unverified PostgreSQL major")
    return receipt


def safe_artifact(xml_source, artifact, expected_nodes, *, head_sha, postgres_major,
                  suite_name, process_returncode, timed_out, attempt_id=None,
                  repeat_ordinal=None):
    """Only a clean completed JUnit run may assert named PASS; otherwise UNKNOWN."""
    reset_artifact(artifact)
    artifact = Path(artifact)
    expected = [list(expected_case_identity(node)) for node in expected_nodes]
    if not expected or len(set(map(tuple, expected))) != len(expected):
        raise ValueError("Invalid expected inventory")
    if attempt_id is not None and not re.fullmatch(r"[0-9a-f]{32}", attempt_id):
        raise ValueError("Invalid attempt identity")
    if repeat_ordinal not in (None, 1, 2):
        raise ValueError("Invalid repeat ordinal")
    verified = False
    try:
        if not re.fullmatch(r"[0-9a-f]{40}", head_sha) or str(postgres_major) not in ("16", "18"):
            raise ValueError("Invalid source / actual PG major")
        if timed_out is not False:
            raise ValueError("TimedOut")
        if type(process_returncode) is not int or process_returncode != 0:
            raise ValueError("UnsuccessfulProcess")
        check_junit(ET.parse(xml_source).getroot(), expected_nodes)
        verified = True
    except (ValueError, ET.ParseError, OSError, TypeError):
        verified = False
    rows = [[*identity, "PASS" if verified else
             ("NOT_EXECUTED" if process_returncode is None and timed_out is False else "UNKNOWN")]
            for identity in expected]
    if not verified:
        # Only explicit failing testcases become FAIL; all other outcomes remain unproven.
        try:
            raw = ET.parse(xml_source).getroot()
            for case in raw.iter("testcase"):
                ident = (case.get("classname"), case.get("name"))
                if any(x.tag in ("failure", "error") for x in case):
                    for row in rows:
                        if tuple(row[:2]) == ident:
                            row[2] = "FAIL"
        except (OSError, ET.ParseError, TypeError):
            pass
        rows.append([GUARD_CLASS, GUARD_NAME, "FAIL"])
    failures = sum(row[2] == "FAIL" for row in rows)
    skipped = sum(row[2] in ("UNKNOWN", "NOT_EXECUTED") for row in rows)
    attrs = {"tests": str(len(rows)), "failures": str(failures), "errors": "0", "skipped": str(skipped)}
    root = ET.Element("testsuites", attrs)
    suite = ET.SubElement(root, "testsuite", {
        "name": suite_name, "attempt_id": attempt_id or "",
        "repeat_ordinal": str(repeat_ordinal or ""), **attrs,
    })
    for classname, name, status in rows:
        case = ET.SubElement(suite, "testcase", {"classname": classname, "name": name, "status": status})
        if status == "FAIL":
            ET.SubElement(case, "failure", {"message": "test or evidence check failed; details redacted"})
        elif status in ("UNKNOWN", "NOT_EXECUTED"):
            ET.SubElement(case, "skipped", {"message": "mandatory evidence not verified"})
    xml_stage = artifact.with_name(artifact.name + ".pending")
    xml_stage.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    os.replace(xml_stage, artifact)
    receipt = {
        "schema": "tester.ci.named-junit.v3", "head_sha": head_sha,
        "postgres_major": str(postgres_major), "suite": suite_name,
        "expected": expected, "observed": rows,
        "status": "PASS" if verified else "NOT_VERIFIED",
        "reason_class": "" if verified else "EvidenceNotVerified",
        "attempt_id": attempt_id, "repeat_ordinal": repeat_ordinal,
        "process_returncode": process_returncode, "timed_out": timed_out,
        "sanitized_xml_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
    }
    _write_json_atomic(artifact.with_suffix(".json"), receipt)
    validate_receipt(artifact)
    if not verified:
        raise ValueError(f"{suite_name}: mandatory evidence NOT_VERIFIED")
    return receipt


def initialize_repetition_manifest(path, *, head_sha, postgres_major, expected_repetitions):
    if expected_repetitions != 2:
        raise ValueError("Both database repetitions required")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(path, {"schema": "tester.ci.two-repetitions.v2",
        "head_sha": head_sha, "postgres_major": str(postgres_major),
        "expected_repetitions": 2, "expected": [list(expected_case_identity(n))
                                                for n in CANONICAL_DATA_RESEARCH_NODES],
        "status": "NOT_VERIFIED", "attempts": [], "repetitions": []})


def begin_repetition_attempt(path, *, head_sha, postgres_major, repeat_ordinal, attempt_id):
    """Reserve unique attempt identity DURABLY before launching pytest."""
    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (payload.get("status") != "NOT_VERIFIED" or payload.get("head_sha") != head_sha or
            payload.get("postgres_major") != str(postgres_major) or
            repeat_ordinal != len(payload.get("attempts", [])) + 1 or
            repeat_ordinal not in (1, 2) or not re.fullmatch(r"[0-9a-f]{32}", attempt_id or "") or
            any(x["attempt_id"] == attempt_id for x in payload["attempts"])):
        raise ValueError("Invalid pre-run repetition reservation")
    payload["attempts"].append({"repeat_ordinal": repeat_ordinal, "attempt_id": attempt_id,
        "suite": f"data-research-pg{postgres_major}-repeat{repeat_ordinal}"})
    _write_json_atomic(path, payload)


def complete_repetition_manifest(path, artifacts, *, head_sha, postgres_major):
    """Accept exact canonical tests from two pre-reserved unique attempts."""
    path = Path(path)
    prior = json.loads(path.read_text(encoding="utf-8"))
    expected = [list(expected_case_identity(n)) for n in CANONICAL_DATA_RESEARCH_NODES]
    if (prior.get("schema") != "tester.ci.two-repetitions.v2" or
            prior.get("status") != "NOT_VERIFIED" or prior.get("head_sha") != head_sha or
            prior.get("postgres_major") != str(postgres_major) or
            prior.get("expected") != expected or prior.get("expected_repetitions") != 2 or
            len(prior.get("attempts", [])) != 2 or len(artifacts) != 2 or
            len(set(map(str, artifacts))) != 2):
        raise ValueError("Invalid aggregate identity")
    rows = []
    for ordinal, artifact in enumerate(artifacts, 1):
        if Path(artifact).name != f"pg{postgres_major}-mandatory-{ordinal}.xml":
            raise ValueError("Wrong required attempt filename")
        receipt = validate_receipt(artifact)
        reserved = prior["attempts"][ordinal - 1]
        suite_name = f"data-research-pg{postgres_major}-repeat{ordinal}"
        if (reserved.get("repeat_ordinal") != ordinal or reserved.get("suite") != suite_name or
                receipt.get("repeat_ordinal") != ordinal or receipt.get("suite") != suite_name or
                receipt.get("attempt_id") != reserved.get("attempt_id") or
                receipt["status"] != "PASS" or receipt["expected"] != expected or
                receipt["head_sha"] != head_sha or receipt["postgres_major"] != str(postgres_major) or
                receipt["process_returncode"] != 0 or receipt["timed_out"] is not False):
            raise ValueError("Incorrect canonical receipt/attempt identity")
        rows.append(receipt)
    if prior["attempts"][0]["attempt_id"] == prior["attempts"][1]["attempt_id"]:
        raise ValueError("Repeated attempt identity")
    # Do not require different output hashes: independent runs may be byte-equivalent.
    prior["status"] = "PASS"
    prior["repetitions"] = [
        {"repeat_ordinal": x["repeat_ordinal"], "attempt_id": x["attempt_id"],
         "suite": x["suite"], "sanitized_xml_sha256": x["sanitized_xml_sha256"]}
        for x in rows]
    _write_json_atomic(path, prior)


def assert_ci_source(expected_head_sha):
    if os.getenv("GITHUB_ACTIONS") != "true":
        raise RuntimeError("Mandatory CI requires GitHub Actions")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if not expected_head_sha or head != expected_head_sha or not re.fullmatch(r"[0-9a-f]{40}", head):
        raise RuntimeError("Expected source SHA does not equal checked out commit")
    return head
