"""Negative acceptance for Code Guardian; no external resources required."""
import hashlib
import unittest
from dataclasses import replace

from src.api import guardian_policy
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


    def test_untrusted_metrics_and_unknown_source_fail_closed(self):
        for now, budget, circuit in (
            (True, 0, False),
            ("100", 0, False),
            (100, None, False),
            (100, 0, "false"),
        ):
            with self.subTest(now=now, budget=budget, circuit=circuit):
                decision = CodeGuardian(enabled=True).evaluate(
                    ident(), probes(), now=now, restarts_last_hour=budget,
                    circuit_open=circuit,
                )
                self.assertEqual(decision.action, "ALERT")
        samples = list(probes())
        samples[0] = replace(samples[0], source=None)
        decision = CodeGuardian(enabled=True).evaluate(
            ident(), tuple(samples), now=100, restarts_last_hour=0
        )
        self.assertEqual(decision.reason, "invalid_or_stale_probe")

    def test_freshness_boundaries_and_after_check_source_replay(self):
        samples = list(probes())
        samples[0] = replace(samples[0], timestamp=70)
        accepted = CodeGuardian(enabled=True).evaluate(
            ident(), tuple(samples), now=100, restarts_last_hour=0
        )
        self.assertEqual(accepted.action, "PROPOSE_OWNED_RESTART")
        samples = list(probes(healthy=True))
        samples[2] = replace(samples[2], source="scm")
        rejected = CodeGuardian.verify(
            ident(), tuple(samples), now=100, independent_verifier=True
        )
        self.assertEqual(rejected.reason, "after_check_not_independent")


    def test_launcher_foreign_service_and_version_never_propose(self):
        for identity in (
            replace(ident(), service="postgresql-x64-18"),
            replace(ident(), expected_service="postgresql-x64-18"),
            replace(ident(), head="0" * 40),
            replace(ident(), sid_verified=False),
            replace(ident(), image_verified=False),
        ):
            with self.subTest(identity=identity):
                decision = CodeGuardian(enabled=True).evaluate(
                    identity, probes(), now=100, restarts_last_hour=0
                )
                self.assertEqual((decision.action, decision.reason),
                                 ("ALERT", "ownership_unverified"))

    def test_matching_foreign_service_identity_never_proposes(self):
        """Forging both names is insufficient to restart non-TESTER services."""
        for service in ("postgresql-x64-18", "Spooler", "ssh-agent", "TESTERWorker"):
            with self.subTest(service=service):
                foreign = replace(ident(), service=service, expected_service=service)
                result = CodeGuardian(enabled=True).evaluate(
                    foreign, probes(), now=100, restarts_last_hour=0
                )
                self.assertEqual((result.action, result.reason),
                                 ("ALERT", "ownership_unverified"))

    def test_process_crash_and_repeated_failure_do_not_execute(self):
        """SCM crash is only a proposal; repetition cannot create a restart loop."""
        guardian = CodeGuardian(enabled=True)
        first = guardian.evaluate(ident(), probes(), now=100, restarts_last_hour=0)
        self.assertEqual(first.action, "PROPOSE_OWNED_RESTART")
        self.assertEqual(
            guardian.evaluate(
                ident(), probes(), now=101, restarts_last_hour=0
            ).reason,
            "duplicate_incident",
        )
        self.assertEqual(
            guardian.evaluate(
                ident(), probes(), now=102, restarts_last_hour=2
            ).reason,
            "restart_budget_exhausted",
        )
        self.assertFalse(hasattr(guardian, "restart"))
        self.assertFalse(hasattr(guardian, "execute"))

    def test_start_refusal_never_becomes_verified_recovery(self):
        """A failed start yields an unhealthy after-check and no success claim."""
        guardian = CodeGuardian(enabled=True)
        proposed = guardian.evaluate(
            ident(), probes(), now=100, restarts_last_hour=0
        )
        self.assertEqual(proposed.action, "PROPOSE_OWNED_RESTART")
        refused = CodeGuardian.verify(
            ident(), probes(), now=100, independent_verifier=True
        )
        self.assertEqual((refused.action, refused.reason),
                         ("ALERT", "after_check_failed"))
        self.assertIsNone(refused.event)

    def test_corrupt_journal_is_not_code_guardian_authority(self):
        """Code has no journal-repair API; MASTER must reject corrupt receipts."""
        guardian = CodeGuardian(enabled=True)
        self.assertFalse(hasattr(guardian, "repair_journal"))
        self.assertFalse(hasattr(guardian, "reserve"))
        self.assertFalse(hasattr(guardian, "finish"))
        # Even an apparently healthy after-check is only a pure observation,
        # not a durable execution receipt or an authorized restart.
        checked = CodeGuardian.verify(
            ident(), probes(healthy=True), now=100,
            independent_verifier=True,
        )
        self.assertEqual(checked.action, "VERIFIED_RECOVERY")
        self.assertIsNone(checked.event)

    def test_shared_policy_proposal_only_and_nontrusted_claims(self):
        """Code decision -> MASTER policy is never an execution permission."""
        result = CodeGuardian(enabled=True).evaluate(
            ident(), probes(), now=100, restarts_last_hour=0
        )
        self.assertEqual(result.action, "PROPOSE_OWNED_RESTART")
        self.assertEqual(
            guardian_policy.decide(result.event).action, "PROPOSE_OWNED_RESTART"
        )
        self.assertEqual(
            guardian_policy.decide({**result.event, "ownership_verified": False}).action,
            "ALERT",
        )
        self.assertEqual(
            guardian_policy.decide({**result.event, "restarts_last_hour": 2}).action,
            "ALERT",
        )

    def test_new_install_environment_requires_master_schema_migration(self):
        """Characterize legacy preview pin; do not bypass shared policy."""
        result = CodeGuardian(enabled=True).evaluate(
            ident(), probes(), now=100, restarts_last_hour=0
        )
        self.assertEqual(result.event["environment"], "tester_preview")
        policy = guardian_policy.decide(
            {**result.event, "environment": "tester_lab"}
        )
        self.assertEqual((policy.action, policy.reason),
                         ("ALERT", "environment_out_of_scope"))

    def test_launcher_recovery_requires_independent_healthy_after_check(self):
        self.assertEqual(
            CodeGuardian.verify(
                ident(), probes(healthy=True), now=100, independent_verifier=False
            ).reason,
            "independent_verifier_missing",
        )
        self.assertEqual(
            CodeGuardian.verify(
                replace(ident(), lease_clear=False),
                probes(healthy=True), now=100, independent_verifier=True
            ).reason,
            "ownership_unverified",
        )
        samples = list(probes(healthy=True))
        samples[0] = replace(samples[0], healthy=False)
        self.assertEqual(
            CodeGuardian.verify(
                ident(), tuple(samples), now=100, independent_verifier=True
            ).reason,
            "after_check_failed",
        )


if __name__ == "__main__":
    unittest.main()
