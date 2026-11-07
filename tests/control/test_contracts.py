import copy
import unittest
from concurrent.futures import ThreadPoolExecutor
import threading
from src.control.contracts import validate_overlay
import test_plane as fixtures
from test_plane import NOW, request
from src.control.plane import Conflict, ControlPlane, QuotaExceeded


class ContractTests(unittest.TestCase):
    setUp = fixtures.PlaneTests.setUp
    tearDown = fixtures.PlaneTests.tearDown
    submit = fixtures.PlaneTests.submit
    result = fixtures.PlaneTests.result
    def test_configuration_result_has_provenance_and_audit_hash(self):
        job = self.submit(request("system.config.preview"))
        self.assertEqual(job["result"]["provenance"]["source_ids"], ["control:config:0"])
        self.assertIsNone(job["result"]["provenance"]["code_revision"])
        self.assertIn("result_hash", self.plane.audit(self.agent)[-1]["details"])

    def test_rate_limit_checked_before_expensive_simulation_gate(self):
        called = []
        plane = ControlPlane(self.path, clock=lambda: NOW, plans_per_minute=1,
                             simulation_gate=lambda c: called.append(c))
        plane.plan(self.agent, request())
        with self.assertRaises(QuotaExceeded):
            plane.plan(self.agent, request("backtest.run", params={"strategy_id":"s", "strategy_revision":"a"*40,"validation_id":"v"}))
        self.assertEqual(called, [])
    def test_future_market_range_is_rejected(self):
        with self.assertRaises(ValueError):
            self.plane.plan(self.agent, request(start_ms=(NOW+10)*1000, end_ms=(NOW+20)*1000))

    def test_simulation_plan_retry_does_not_repeat_provider_call(self):
        payload = request("backtest.run", params={"strategy_id":"ma-cross", "strategy_revision":"a"*40,"validation_id":"validation-1"})
        evidence = {**payload["params"], "snapshot_id":"market-1", "coverage":"complete", "preview":{"entry":"ma-cross"}, "source_ids":["archive-1"]}
        plane = ControlPlane(self.path, clock=lambda: NOW, simulation_gate=lambda c: evidence)
        plan = plane.plan(self.agent, payload)
        plane.simulation_gate = None
        self.assertEqual(plane.plan(self.agent, payload)["plan_id"], plan["plan_id"])

    def test_audit_filters_scopes_before_pagination(self):
        from src.control.policy import Identity
        broad = Identity("agent:g", frozenset({"system.health"}), frozenset({"a", "z"}), NOW+600)
        narrow = Identity("agent:g", broad.actions, frozenset({"z"}), NOW+600)
        self.plane.plan(broad, request("system.health", scope="a"))
        self.plane.plan(broad, request("system.health", scope="z"))
        self.assertEqual(self.plane.audit(narrow, limit=1)[0]["scope"], "z")

    def test_development_result_must_be_isolated_draft_pr_with_tests(self):
        payload = request("dev.change.request", params={"base_revision":"a"*40,"branch":"codex/test-change","task_ref":"issue:18"})
        plan = self.plane.plan(self.agent, payload)
        self.plane.approve(self.human, plan["plan_id"])
        job = self.plane.submit(self.agent, plan["plan_id"])
        lease = self.plane.claim("worker-1")
        result = self.result()
        result["data"] = {"mode":"live-patch"}
        with self.assertRaises(ValueError): self.plane.complete(job["job_id"], lease["lease_token"], result)
        result["data"] = {"mode":"isolated-worktree-draft-pr", "branch":"codex/test-change", "base_revision":"a"*40,"head_revision":"b"*40,"pr_number":42,"tests_artifact_id":"tests-1"}
        result["artifacts"] = [{"artifact_id":"tests-1","sha256":"c"*64,"media_type":"text/plain","size_bytes":100}]
        self.plane.complete(job["job_id"], lease["lease_token"], result)
        self.assertEqual(self.plane.get(self.agent, job["job_id"])["state"], "succeeded")
    def test_simulation_requires_trusted_preview_complete_data_and_approval(self):
        payload = request("backtest.run", params={"strategy_id":"ma-cross", "strategy_revision":"a"*40,"validation_id":"validation-1"})
        with self.assertRaises(Conflict): self.plane.plan(self.agent, payload)
        evidence = {**payload["params"], "snapshot_id":"market-1", "coverage":"partial", "preview":{"entry":"ma-cross"}, "source_ids":["archive-1"]}
        plane = ControlPlane(self.path, clock=lambda: NOW, simulation_gate=lambda c: copy.deepcopy(evidence))
        with self.assertRaises(Conflict): plane.plan(self.agent, payload)
        evidence["coverage"] = "complete"
        plan = plane.plan(self.agent, payload)
        with self.assertRaises(PermissionError): plane.submit(self.agent, plan["plan_id"])
        plane.approve(self.human, plan["plan_id"])
        evidence["preview"]["entry"] = "changed"
        with self.assertRaises(Conflict): plane.submit(self.agent, plan["plan_id"])
        evidence["preview"]["entry"] = "ma-cross"
        job = plane.submit(self.agent, plan["plan_id"])
        self.assertEqual(job["state"], "queued")

    def test_expired_approval_cannot_start_mutation(self):
        plan = self.plane.plan(self.agent, request("system.config.apply", params={"expected_revision":0,"changes":{"max_workers":2}}))
        self.plane.approve(self.human, plan["plan_id"])
        future = ControlPlane(self.path, clock=lambda: NOW+301)
        with self.assertRaises(PermissionError): future.submit(self.agent, plan["plan_id"])
        self.assertEqual(future.config()["revision"], 0)

    def test_concurrent_config_apply_has_one_winner_and_rollback_is_versioned(self):
        plans = [self.plane.plan(self.agent, request("system.config.apply", params={"expected_revision":0,"changes":{"max_workers":n}})) for n in (2, 3)]
        for plan in plans: self.plane.approve(self.human, plan["plan_id"])
        barrier = threading.Barrier(2)
        def submit(plan):
            barrier.wait(timeout=10)
            try:
                return self.plane.submit(self.agent, plan["plan_id"])["state"]
            except Conflict: return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(submit, plans)), ["conflict", "succeeded"])
        rollback = self.plane.plan(self.agent, request("system.config.apply", params={"expected_revision":1,"changes":{"max_workers":1}}))
        self.plane.approve(self.human, rollback["plan_id"])
        self.plane.submit(self.agent, rollback["plan_id"])
        self.assertEqual(self.plane.config(), {"revision":2,"max_workers":1,"max_downloads":1,"memory_mb":512})

    def test_distinct_concurrent_agents_share_global_quota(self):
        plane = ControlPlane(self.path, clock=lambda: NOW, max_active=1)
        plans = [(actor, plane.plan(actor, request(actor=actor.actor))) for actor in (self.agent, self.other)]
        barrier = threading.Barrier(2)
        def submit(item):
            actor, plan = item
            barrier.wait(timeout=10)
            try: return plane.submit(actor, plan["plan_id"])["state"]
            except QuotaExceeded: return "quota"
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(submit, plans)), ["queued", "quota"])

    def test_bounded_logs_monotonic_progress_and_lease_fencing(self):
        job = self.submit()
        lease = self.plane.claim("worker-1")
        with self.assertRaises(Conflict): self.plane.progress(job["job_id"], "bad", 1)
        self.plane.progress(job["job_id"], lease["lease_token"], 20)
        with self.assertRaises(Conflict): self.plane.progress(job["job_id"], lease["lease_token"], 19)
        for _ in range(20): self.plane.progress(job["job_id"], lease["lease_token"], 20)
        self.assertEqual(len(self.plane.events(self.agent, job["job_id"])), 3)
        for limit in (True, 0, 501):
            with self.assertRaises(ValueError): self.plane.events(self.agent, job["job_id"], limit=limit)

    def test_rate_limit_and_queued_expiry(self):
        plane = ControlPlane(self.path, clock=lambda: NOW, plans_per_minute=1)
        plan = plane.plan(self.agent, request())
        plane.submit(self.agent, plan["plan_id"])
        with self.assertRaises(QuotaExceeded): plane.plan(self.agent, request())
        future = ControlPlane(self.path, clock=lambda: NOW+601)
        self.assertIsNone(future.claim("worker-1"))

    def overlay(self):
        return {"schema_version":"1.0", "run_id":"run-1", "revision":1, "venue":"bybit",
                "category":"linear", "symbol":"BTCUSDT", "interval":"1", "start_ms":1000,
                "end_ms":2000, "availability":"AVAILABLE", "coverage":"complete",
                "provenance":self.result()["provenance"], "next_cursor":None,
                "markers":[{"timestamp_ms":1000,"kind":"fill","price":"10.25","trade_id":"trade-1","fill_id":"fill-1","reason":"entry-1"}],
                "levels":[{"start_ms":1000,"end_ms":2000,"kind":"sl","price":"9.50","rule_id":"sl-1"}]}

    def test_overlay_fills_signals_decimal_range_and_provenance(self):
        self.assertEqual(validate_overlay(self.overlay()), self.overlay())
        for key, value in (("fill_id",None), ("price",10.25), ("timestamp_ms",2000), ("kind","signal")):
            overlay = self.overlay()
            overlay["markers"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): validate_overlay(overlay)
        overlay = self.overlay()
        overlay["markers"][0].update(kind="signal", trade_id=None, fill_id=None)
        self.assertEqual(validate_overlay(overlay)["markers"][0]["kind"], "signal")
        overlay["provenance"] = {}
        with self.assertRaises(ValueError): validate_overlay(overlay)


if __name__ == "__main__": unittest.main()
