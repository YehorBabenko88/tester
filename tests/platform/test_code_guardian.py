"""Negative acceptance for Code Guardian; no external resources required."""
import hashlib
import unittest
from dataclasses import replace

from src.api.guardians.code_guardian import (
    CodeGuardian,
    Identity,
    Probe,
    request_code_review,
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
        samples = list(probes())
        samples[2] = replace(samples[2], healthy=True)
        self.assertEqual(g.evaluate(ident(), tuple(samples), now=100, restarts_last_hour=0).action, "ALERT")

    def test_source_independence(self):
        samples = list(probes())
        samples[2] = replace(samples[2], source="scm")
        self.assertEqual(CodeGuardian(enabled=True).evaluate(ident(), tuple(samples), now=100, restarts_last_hour=0).reason, "independence_unverified")

    def test_stale_probe_and_bad_evidence(self):
        samples = list(probes())
        samples[0] = replace(samples[0], timestamp=10)
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


class TestCodeGuardianFaultInjection(unittest.TestCase):
    """Adversarial evidence tests: no Windows services, network or database."""

    def test_forged_process_ownership_attestation(self):
        for change in (
            {"service": "OtherWorker", "expected_service": "TesterWorker"},
            {"sid_verified": 1},
            {"image_verified": "true"},
            {"config_verified": None},
            {"lease_clear": 1},
            {"expected_service": "OtherWorker"},
        ):
            with self.subTest(change=change):
                result = CodeGuardian(enabled=True).evaluate(
                    replace(ident(), **change), probes(), now=100, restarts_last_hour=0
                )
                self.assertEqual((result.action, result.reason), ("ALERT", "ownership_unverified"))

    def test_stale_or_invalid_version_sha(self):
        for change in (
            {"head": "b" * 40},
            {"expected_head": "b" * 40},
            {"head": "A" * 40},
            {"head": "not-a-commit"},
        ):
            with self.subTest(change=change):
                result = CodeGuardian(enabled=True).evaluate(
                    replace(ident(), **change), probes(), now=100, restarts_last_hour=0
                )
                self.assertEqual(result.reason, "ownership_unverified")

    def test_contradictory_probe_blocks_restart(self):
        samples = list(probes())
        samples[1] = replace(samples[1], healthy=True)
        result = CodeGuardian(enabled=True).evaluate(
            ident(), tuple(samples), now=100, restarts_last_hour=0
        )
        self.assertEqual((result.action, result.reason), ("ALERT", "failure_unconfirmed"))

    def test_stale_future_and_malformed_probes(self):
        for change in (
            {"timestamp": 69},
            {"timestamp": 101},
            {"timestamp": True},
            {"healthy": 0},
            {"evidence": "sha256:invalid"},
            {"sample_id": "../other"},
        ):
            with self.subTest(change=change):
                samples = list(probes())
                samples[0] = replace(samples[0], **change)
                result = CodeGuardian(enabled=True).evaluate(
                    ident(), tuple(samples), now=100, restarts_last_hour=0
                )
                self.assertEqual(result.reason, "invalid_or_stale_probe")

    def test_duplicate_incident_with_rotated_sample_ids(self):
        guardian = CodeGuardian(enabled=True)
        first = guardian.evaluate(ident(), probes(), now=100, restarts_last_hour=0)
        rotated = tuple(
            replace(sample, sample_id="rotated" + str(i))
            for i, sample in enumerate(probes())
        )
        second = guardian.evaluate(ident(), rotated, now=100, restarts_last_hour=0)
        self.assertEqual(first.action, "PROPOSE_OWNED_RESTART")
        self.assertEqual((second.action, second.reason), ("ALERT", "duplicate_incident"))
        self.assertEqual(first.fingerprint, second.fingerprint)

    def test_failed_after_check_never_verifies_recovery(self):
        samples = list(probes(healthy=True))
        samples[2] = replace(samples[2], healthy=False)
        failed = CodeGuardian.verify(
            ident(), tuple(samples), now=100, independent_verifier=True
        )
        self.assertEqual((failed.action, failed.reason), ("ALERT", "after_check_failed"))

    def test_after_check_rejects_wrong_identity_and_stale_evidence(self):
        samples = list(probes(healthy=True))
        samples[0] = replace(samples[0], timestamp=0)
        stale = CodeGuardian.verify(
            ident(), tuple(samples), now=100, independent_verifier=True
        )
        wrong_head = CodeGuardian.verify(
            replace(ident(), head="b" * 40),
            probes(healthy=True),
            now=100,
            independent_verifier=True,
        )
        self.assertEqual(stale.reason, "after_check_failed")
        self.assertEqual(wrong_head.reason, "ownership_unverified")

    def test_distinct_sources_must_not_reuse_evidence_digest(self):
        samples = list(probes())
        samples[1] = replace(samples[1], evidence=samples[0].evidence)
        decision = CodeGuardian(enabled=True).evaluate(
            ident(), tuple(samples), now=100, restarts_last_hour=0
        )
        self.assertEqual((decision.action, decision.reason), ("ALERT", "evidence_not_independent"))

    def test_after_check_rejects_reused_evidence_digest(self):
        samples = list(probes(healthy=True))
        samples[2] = replace(samples[2], evidence=samples[0].evidence)
        decision = CodeGuardian.verify(
            ident(), tuple(samples), now=100, independent_verifier=True
        )
        self.assertEqual(
            (decision.action, decision.reason),
            ("ALERT", "after_check_evidence_not_independent"),
        )


if __name__ == "__main__":
    unittest.main()
