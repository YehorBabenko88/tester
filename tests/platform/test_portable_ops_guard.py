import unittest
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.portable_ops import Ownership, write_checkpoint
from scripts.portable_ops_guard import (ProcessWitness, LifecycleRefusal,
                                       guarded_resume, ResourcePolicy, sustained_metrics)

class GuardTests(unittest.TestCase):
    def setUp(self):
        self.owner = Ownership("test-1", 123, "birth", "/tmp/isolated")
        self.witness = ProcessWitness(self.owner, "python", ("-m", "worker"), True, 0, True)

    def test_durable_recovery_and_single_winner(self):
        with TemporaryDirectory() as td:
            cp = Path(td) / "cp.json"
            write_checkpoint(cp, self.owner, [{"id":"a","state":"running"},
                        {"id":"b","state":"queued"}, {"id":"c","state":"completed"}], 0)
            leases = set()
            def claim(job):
                if job in leases: return None
                leases.add(job)
                return SimpleNamespace(id=job, lease_token="token-"+job)
            args = (cp, self.owner, self.witness, "python", ("-m", "worker"),
                    {"a":"running","b":"queued","c":"completed"}, claim, frozenset({"a"}))
            self.assertEqual([lease.id for lease in guarded_resume(*args)], ["a","b"])
            self.assertEqual(guarded_resume(*args), [])

    def test_missing_durable_state_refuses(self):
        with TemporaryDirectory() as td:
            cp = Path(td) / "cp.json"
            write_checkpoint(cp, self.owner, [{"id":"a","state":"running"}], 0)
            with self.assertRaises(LifecycleRefusal):
                guarded_resume(cp,self.owner,self.witness,"python",("-m","worker"),{},lambda _: True)

    def test_live_or_nonzero_exit_refuses_before_claim(self):
        with TemporaryDirectory() as td:
            cp = Path(td) / "cp.json"
            write_checkpoint(cp,self.owner,[{"id":"a","state":"queued"}],0)
            calls = []
            for exited, code, drained in [(False,None,False),(True,1,True),(True,0,False)]:
                witness = ProcessWitness(self.owner,"python",("-m","worker"),exited,code,drained)
                with self.assertRaises(LifecycleRefusal):
                    guarded_resume(cp,self.owner,witness,"python",("-m","worker"),
                                   {"a":"queued"},lambda x:calls.append(x))
            self.assertEqual(calls,[])

    def test_unexpired_running_lease_refuses_before_claim(self):
        with TemporaryDirectory() as td:
            cp = Path(td) / "cp.json"
            write_checkpoint(cp, self.owner, [{"id":"a","state":"running"}], 0)
            calls = []
            with self.assertRaises(LifecycleRefusal):
                guarded_resume(cp, self.owner, self.witness, "python", ("-m","worker"),
                               {"a":"running"}, lambda x:calls.append(x))
            self.assertEqual(calls, [])

    def test_paused_job_not_automatically_resumed(self):
        with TemporaryDirectory() as td:
            cp = Path(td) / "cp.json"
            write_checkpoint(cp, self.owner, [{"id":"a","state":"queued"}], 0)
            self.assertEqual(guarded_resume(cp, self.owner, self.witness, "python",
                             ("-m","worker"), {"a":"paused"}, lambda _:SimpleNamespace(lease_token="token")), [])

    def test_claim_without_fencing_token_refused(self):
        with TemporaryDirectory() as td:
            cp = Path(td) / "cp.json"
            write_checkpoint(cp, self.owner, [{"id":"a","state":"queued"}], 0)
            with self.assertRaises(LifecycleRefusal):
                guarded_resume(cp, self.owner, self.witness, "python", ("-m","worker"),
                               {"a":"queued"}, lambda _: True)

    def test_claim_wrong_job_identity_refused(self):
        with TemporaryDirectory() as td:
            cp = Path(td) / "cp.json"
            write_checkpoint(cp, self.owner, [{"id":"a","state":"queued"}], 0)
            with self.assertRaises(LifecycleRefusal):
                guarded_resume(cp, self.owner, self.witness, "python", ("-m","worker"),
                               {"a":"queued"}, lambda _: SimpleNamespace(id="foreign", lease_token="token"))

    def test_wrong_image_refuses(self):
        with TemporaryDirectory() as td:
            cp = Path(td) / "cp.json"
            write_checkpoint(cp,self.owner,[],0)
            with self.assertRaises(LifecycleRefusal):
                guarded_resume(cp,self.owner,self.witness,"foreign",("-m","worker"),{},lambda _:True)

    def test_policy_flags_without_killing(self):
        self.assertEqual(ResourcePolicy(100,80,4).evaluate(
            {"rss_bytes":101,"cpu_percent":90,"threads":5}),("RSS","CPU","THREADS"))

    def test_ten_symbol_metrics_fixture(self):
        symbols = {f"SYM{i}" for i in range(10)}
        samples = [{"symbol":f"SYM{i}", "duration_ms":i+1,
                    "rss_bytes":100+i,"cpu_percent":i} for i in range(10)]
        result = sustained_metrics(samples,symbols,10)
        self.assertEqual(result["symbols"],10)
        self.assertEqual(result["samples"],10)
        with self.assertRaises(LifecycleRefusal):
            sustained_metrics(samples[:-1],symbols,10)

if __name__ == "__main__":
    unittest.main()
