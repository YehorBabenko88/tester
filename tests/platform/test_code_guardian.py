"""Negative acceptance for Code Guardian; no external resources required."""
import hashlib
import unittest
from dataclasses import replace

from src.api.guardians.code_guardian import (
    CodeGuardian, Identity, Probe, request_code_review,
)

HEAD = "a" * 40


def ident():
    return Identity("TesterWorker", "TesterWorker", HEAD, HEAD, True, True, True, True)


def probes(healthy=False, now=100):
    return tuple(
        Probe(s, "sample" + str(i), now - i, healthy,
              "sha256:" + hashlib.sha256(s.encode()).hexdigest())
        for i, s in enumerate(("scm", "http_health", "worker_heartbeat"))
    )


class TestCodeGuardian(unittest.TestCase):
    def test_disabled(self):
        self.assertEqual(CodeGuardian().evaluate(ident(), probes(), now=100, restarts_last_hour=0).reason, "disabled")

    def test_quorum_proposes_not_executes_and_deduplicates(self):
        g = CodeGuardian(enabled=True)
        result = g.evaluate(ident(), probes(), now=100, restarts_last_hour=0)
        self.assertEqual(result.action, "PROPOSE_OWNED_RESTART")
        self.assertEqual(result.event["consecutive_failed_probes"], 3)
        self.assertEqual(g.evaluate(ident(), probes(), now=100, restarts_last_hour=0).reason, "duplicate_incident")

    def test_ownership_fail_closed(self):
        for field in ("sid_verified", "image_verified", "config_verified", "lease_clear"):
            self.assertEqual(CodeGuardian(enabled=True).evaluate(
                replace(ident(), **{field: False}), probes(), now=100, restarts_last_hour=0).action, "ALERT")

    def test_wrong_process_and_head(self):
        for value in (replace(ident(), service="UnrelatedService"),
                      replace(ident(), head="b" * 40)):
            self.assertEqual(CodeGuardian(enabled=True).evaluate(value, probes(), now=100, restarts_last_hour=0).action, "ALERT")

    def test_two_failures_or_one_healthy(self):
        g = CodeGuardian(enabled=True)
        self.assertEqual(g.evaluate(ident(), probes()[:2], now=100, restarts_last_hour=0).action, "ALERT")
        samples = list(probes()); samples[2] = replace(samples[2], healthy=True)
        self.assertEqual(g.evaluate(ident(), tuple(samples), now=100, restarts_last_hour=0).action, "ALERT")

    def test_source_independence(self):
        samples = list(probes()); samples[2] = replace(samples[2], source="scm")
        self.assertEqual(CodeGuardian(enabled=True).evaluate(ident(), tuple(samples), now=100, restarts_last_hour=0).reason, "independence_unverified")

    def test_stale_probe_and_bad_evidence(self):
        samples = list(probes()); samples[0] = replace(samples[0], timestamp=10)
        self.assertEqual(CodeGuardian(enabled=True).evaluate(ident(), tuple(samples), now=100, restarts_last_hour=0).action, "ALERT")
        samples[0] = replace(samples[0], timestamp=100, evidence="garbage")
        self.assertEqual(CodeGuardian(enabled=True).evaluate(ident(), tuple(samples), now=100, restarts_last_hour=0).action, "ALERT")

    def test_restart_budget_and_circuit(self):
        for budget in (2, 3, -1, True, "0"):
            self.assertEqual(CodeGuardian(enabled=True).evaluate(ident(), probes(), now=100, restarts_last_hour=budget).action, "ALERT")
        self.assertEqual(CodeGuardian(enabled=True).evaluate(ident(), probes(), now=100, restarts_last_hour=0, circuit_open=True).action, "ALERT")

    def test_independent_after_check(self):
        self.assertEqual(CodeGuardian.verify(ident(), probes(True), now=100, independent_verifier=True).action, "VERIFIED_RECOVERY")
        self.assertEqual(CodeGuardian.verify(ident(), probes(), now=100, independent_verifier=True).action, "ALERT")
        self.assertEqual(CodeGuardian.verify(ident(), probes(True), now=100).action, "ALERT")

    def test_review_is_hash_only_and_draft(self):
        result = request_code_review("TimeoutError", "sha256:" + "c" * 64, HEAD)
        self.assertEqual(result.action, "REQUEST_CODE_REVIEW")
        self.assertNotIn("message", result.event)
        self.assertEqual(request_code_review("cmd /c foo", "sha256:" + "c" * 64, HEAD).action, "ALERT")


if __name__ == "__main__":
    unittest.main()
