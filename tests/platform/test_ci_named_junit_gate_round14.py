"""Negative mutation tests for strict JUnit identity and artifact sanitization."""
import json
from xml.etree import ElementTree as ET

import pytest

from scripts.ci_named_junit_gate import (
    check_junit, expected_case_identity, safe_artifact,
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
                      postgres_major="16", suite_name="negative")
    assert target.exists()
    assert "REDACT-ME" not in target.read_text()
    assert json.loads(target.with_suffix(".json").read_text())["status"] == "NOT_VERIFIED"


def test_absent_xml_fails_closed_and_writes_missing_evidence(tmp_path):
    target = tmp_path / "retained.xml"
    with pytest.raises(ValueError):
        safe_artifact(tmp_path / "absent.xml", target, (NODE,),
                      head_sha="c" * 40, postgres_major="16", suite_name="missing")
    assert target.exists()
    assert json.loads(target.with_suffix(".json").read_text())["observed"] == []
