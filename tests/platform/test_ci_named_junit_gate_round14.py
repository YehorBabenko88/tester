"""Negative mutation tests for strict JUnit identity and artifact sanitization."""
import ast
import json
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from scripts.ci_named_junit_gate import (
    check_junit,
    complete_repetition_manifest,
    expected_case_identity,
    initialize_repetition_manifest,
    reset_artifact,
    safe_artifact,
    validate_receipt,
)

NODE = "tests/ingest/test_pipeline.py::PipelineTests::test_canonical_rest_deadline_during_stream_closes_response"
CLASS, NAME = expected_case_identity(NODE)


def xml_fixture(*, classname=CLASS, name=NAME, count=1, marker=None,
                status=None, suite_skipped=0, suite_failed=0):
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite", {
        "tests": str(count), "skipped": str(suite_skipped),
        "failures": str(suite_failed), "errors": "0",
    })
    case = ET.SubElement(suite, "testcase", {"classname": classname, "name": name})
    if status is not None:
        case.set("status", status)
    if marker is not None:
        ET.SubElement(case, marker, {"message": "test secret password=REDACT-ME"})
    return root


def test_exact_classname_and_method_pass():
    assert check_junit(xml_fixture(), (NODE,)) == [(CLASS, NAME)]


@pytest.mark.parametrize("mutate", [
    lambda x: x.find(".//testcase").set("classname", "tests.other.PipelineTests"),
    lambda x: x.find(".//testcase").set("name", "test_other"),
    lambda x: x.find(".//testsuite").set("tests", "2"),
    lambda x: x.find(".//testsuite").set("failures", "1"),
    lambda x: x.find(".//testsuite").set("errors", "1"),
    lambda x: x.find(".//testsuite").set("skipped", "1"),
    lambda x: x.find(".//testcase").set("status", "XPASS"),
    lambda x: x.find(".//testcase").set("status", "FAIL"),
    lambda x: x.find(".//testcase").set("status", "XFAIL"),
    lambda x: x.find(".//testcase").append(ET.Element("skipped")),
    lambda x: x.find(".//testcase").append(ET.Element("failure")),
    lambda x: x.find(".//testcase").append(ET.Element("error")),
    lambda x: x.find(".//testsuite").remove(x.find(".//testcase")),
    lambda x: x.find(".//testsuite").append(ET.Element("testcase", {
        "classname": CLASS, "name": NAME,
    })),
])
def test_corrupt_status_identity_counts_or_duplicates_rejected(mutate):
    x = xml_fixture()
    mutate(x)
    with pytest.raises(ValueError):
        check_junit(x, (NODE,))


def test_duplicate_expected_node_rejected():
    with pytest.raises(ValueError):
        check_junit(xml_fixture(), (NODE, NODE))


def test_sanitized_junit_and_source_receipt_no_sensitive_traceback(tmp_path):
    root = xml_fixture()
    path = tmp_path / "unsanitized.xml"
    ET.ElementTree(root).write(path)
    artifact = tmp_path / "evidence" / "safe.xml"
    receipt = safe_artifact(
        path, artifact, (NODE,), head_sha="a" * 40,
        postgres_major="18", suite_name="E six-case smoke",
        process_returncode=0, timed_out=False,
    )
    assert receipt["status"] == "PASS"
    assert receipt["head_sha"] == "a" * 40
    assert receipt["postgres_major"] == "18"
    assert "REDACT-ME" not in artifact.read_text()
    assert "PASS" == json.loads(artifact.with_suffix(".json").read_text())["status"]


def test_failed_junit_artifact_redacts_traceback_and_raises(tmp_path):
    x = xml_fixture(marker="failure", suite_failed=1)
    raw = tmp_path / "raw.xml"
    ET.ElementTree(x).write(raw)
    target = tmp_path / "retained.xml"
    with pytest.raises(ValueError):
        safe_artifact(raw, target, (NODE,), head_sha="b" * 40,
                      postgres_major="16", suite_name="negative",
                      process_returncode=0, timed_out=False)
    assert target.exists()
    assert "REDACT-ME" not in target.read_text()
    assert json.loads(target.with_suffix(".json").read_text())["status"] == "NOT_VERIFIED"


def test_absent_xml_fails_closed_and_writes_missing_evidence(tmp_path):
    target = tmp_path / "retained.xml"
    with pytest.raises(ValueError):
        safe_artifact(tmp_path / "absent.xml", target, (NODE,),
                      head_sha="c" * 40, postgres_major="16", suite_name="missing",
                      process_returncode=0, timed_out=False)
    assert target.exists()
    assert json.loads(target.with_suffix(".json").read_text())["status"] == "NOT_VERIFIED"


@pytest.mark.parametrize("mutate", [
    lambda x: setattr(x, "tag", "not_a_junit_root"),
    lambda x: (x[0].remove(x[0][0]), x.append(ET.Element(
        "testcase", {"classname": CLASS, "name": NAME}))),
    lambda x: (x[0].remove(x[0][0]), ET.SubElement(
        x[0], "system-out").append(ET.Element(
        "testcase", {"classname": CLASS, "name": NAME}))),
    lambda x: x.set("tests", "999"),
    lambda x: x.set("failures", "999"),
    lambda x: ET.SubElement(x, "testsuite"),
])
def test_round16_forged_junit_root_or_detached_case_rejected(mutate):
    xml = xml_fixture()
    mutate(xml)
    with pytest.raises(ValueError):
        check_junit(xml, (NODE,))


@pytest.mark.parametrize("returncode,timed_out", [
    (1, False), (2, False), (3, False), (None, False), (None, True),
])
def test_round16_nonzero_or_timeout_never_creates_pass_receipt(tmp_path, returncode, timed_out):
    raw = tmp_path / "pytest.xml"
    ET.ElementTree(xml_fixture()).write(raw)
    artifact = tmp_path / "safe.xml"
    with pytest.raises(ValueError):
        safe_artifact(
            raw, artifact, (NODE,), head_sha="a" * 40,
            postgres_major="16", suite_name="round16-negative",
            process_returncode=returncode, timed_out=timed_out,
        )
    receipt = validate_receipt(artifact)
    assert receipt["status"] == "NOT_VERIFIED"
    assert receipt["process_returncode"] == returncode
    assert receipt["timed_out"] is timed_out
    assert "password=REDACT-ME" not in artifact.read_text()


def test_round16_prior_pass_erased_before_retry(tmp_path):
    source = tmp_path / "original.xml"
    ET.ElementTree(xml_fixture()).write(source)
    target = tmp_path / "last-attempt.xml"
    safe_artifact(source, target, (NODE,), head_sha="a" * 40,
                  postgres_major="18", suite_name="first",
                  process_returncode=0, timed_out=False)
    reset_artifact(target)
    assert not target.exists()
    assert not target.with_suffix(".json").exists()


def test_round16_partial_second_pg_repetition_never_passes(tmp_path):
    manifest = tmp_path / "two-repetitions.json"
    initialize_repetition_manifest(
        manifest, head_sha="a" * 40, postgres_major="16", expected_repetitions=2)
    assert json.loads(manifest.read_text())["status"] == "NOT_VERIFIED"


def test_round16_actual_pytest_generated_junit_classname_and_name(tmp_path):
    # Creates and runs only two offline assertions in a disposable directory.
    package = tmp_path / "tests" / "suite"
    package.mkdir(parents=True)
    sample = package / "test_junit_real.py"
    sample.write_text(
        "import unittest\n"
        "def test_plain_real(): assert True\n"
        "class RealUnitCase(unittest.TestCase):\n"
        "    def test_unit_real(self): self.assertTrue(True)\n",
        encoding="utf-8")
    xml = tmp_path / "actual-pytest.xml"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-o",
         "junit_family=xunit2", f"--junitxml={xml}",
         "tests/suite/test_junit_real.py"], cwd=tmp_path,
        capture_output=True, text=True, timeout=40,
    )
    assert result.returncode == 0, result.stdout[-1200:]
    assert check_junit(ET.parse(xml).getroot(), (
        "tests/suite/test_junit_real.py::test_plain_real",
        "tests/suite/test_junit_real.py::RealUnitCase::test_unit_real",
    ))


def test_round16_pg18_banner_uses_defined_nodes_and_has_exact_13():
    """Execute final f-string AST offline; does not connect to PostgreSQL."""
    source = Path(__file__).resolve().parents[2] / "scripts" / "ci_pg18_principal_isolation.py"
    module = ast.parse(source.read_text(encoding="utf-8"))
    main = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    banner = main.body[-1]
    assert isinstance(banner, ast.Expr) and isinstance(banner.value, ast.Call)
    fragment = banner.value.args[0]
    compiled = compile(ast.Expression(fragment), str(source), "eval")
    output = eval(compiled, {"version": "180003", "nodes": ["case"] * 13})
    assert "/ 13 mandatory executed" in output


def test_round16_second_repetition_failure_cannot_produce_aggregate_pass(tmp_path):
    nodes = tuple(
        f"tests/platform/test_ci_named_junit_gate_round14.py::test_round16_synthetic_{i}"
        for i in range(5)
    )
    rawroot = ET.Element("testsuites")
    suite = ET.SubElement(rawroot, "testsuite", {
        "tests": "5", "skipped": "0", "failures": "0", "errors": "0",
    })
    for node in nodes:
        classname, name = expected_case_identity(node)
        ET.SubElement(suite, "testcase", {"classname": classname, "name": name})
    raw = tmp_path / "raw.xml"
    ET.ElementTree(rawroot).write(raw)
    first = tmp_path / "repeat1.xml"
    second = tmp_path / "repeat2.xml"
    manifest = tmp_path / "two-repetitions.json"
    initialize_repetition_manifest(
        manifest, head_sha="a" * 40, postgres_major="18", expected_repetitions=2)
    safe_artifact(raw, first, nodes, head_sha="a" * 40,
                  postgres_major="18", suite_name="repeat-one",
                  process_returncode=0, timed_out=False)
    with pytest.raises(ValueError):
        safe_artifact(raw, second, nodes, head_sha="a" * 40,
                      postgres_major="18", suite_name="repeat-two",
                      process_returncode=1, timed_out=False)
    with pytest.raises(ValueError):
        complete_repetition_manifest(
            manifest, (first, second),
            head_sha="a" * 40, postgres_major="18",
        )
    assert json.loads(manifest.read_text())["status"] == "NOT_VERIFIED"


def test_round16_sanitized_xml_status_and_json_detect_tamper(tmp_path):
    raw = tmp_path / "raw.xml"
    ET.ElementTree(xml_fixture()).write(raw)
    artifact = tmp_path / "out.xml"
    safe_artifact(raw, artifact, (NODE,), head_sha="a" * 40,
                  postgres_major="16", suite_name="tamper",
                  process_returncode=0, timed_out=False)
    receipt = json.loads(artifact.with_suffix(".json").read_text())
    receipt["observed"][0][2] = "FAIL"
    artifact.with_suffix(".json").write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        validate_receipt(artifact)
