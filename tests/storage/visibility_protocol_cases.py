"""Executable negative traces for proposal v0.1; no production admission path.

python -m tests.storage.visibility_protocol_cases --demonstrate-red
must fail: a sampled-time-only candidate is unsafe on every supplied trace.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Case:
    name: str
    events: tuple[str, ...]
    reason: str


BASE=('gate_acquired','snapshot','inventory_closed','capture_complete',
      'capture_commit','confirm_committed','clock_sample','receipt_commit','return')
CASES=(
    Case('delayed_commit',('writer_start','snapshot','clock_sample','writer_commit','return'),'uncommitted_capture'),
    Case('snapshot_before_gate',('snapshot','writer_commit','gate_acquired')+BASE[2:],'stale_snapshot'),
    Case('late_source_run_link',BASE[:3]+('outside_writer_commit',)+BASE[3:],'unfenced_mutation'),
    Case('late_corruption_resolution',BASE[:5]+('outside_writer_commit',)+BASE[5:],'unfenced_mutation'),
    Case('missing_quarantine_inventory',tuple(e for e in BASE if e!='inventory_closed'),'negative_universe_unknown'),
    Case('crash_after_data_commit',BASE[:7]+('crash','return'),'receipt_not_durable'),
    Case('partial_manifest',tuple(e for e in BASE if e!='capture_complete'),'partial_manifest'),
    Case('stale_snapshot_reuse',BASE+('writer_commit','reuse_as_live'),'stale_live_binding'),
    Case('bounded_failure',BASE[:3]+('resource_failure',)+BASE[3:],'resource_failure'),
    Case('external_archive_changes',BASE+('file_mutation','claim_file_health'),'unfenced_archive'),
)


def reject_reason(events):
    """Negative-only trace oracle. No successful receipt is produced or accepted."""
    def before(a,b):
        return a in events and b in events and events.index(a)<events.index(b)
    if 'resource_failure' in events: return 'resource_failure'
    if 'outside_writer_commit' in events: return 'unfenced_mutation'
    if 'claim_file_health' in events: return 'unfenced_archive'
    if 'reuse_as_live' in events and before('receipt_commit','writer_commit'): return 'stale_live_binding'
    if not before('gate_acquired','snapshot'): 
        if 'gate_acquired' in events: return 'stale_snapshot'
        return 'uncommitted_capture'
    if 'inventory_closed' not in events: return 'negative_universe_unknown'
    if 'capture_complete' not in events: return 'partial_manifest'
    if not before('capture_commit','confirm_committed') or not before('confirm_committed','clock_sample'): return 'receipt_not_durable'
    if not before('clock_sample','receipt_commit') or not before('receipt_commit','return'): return 'receipt_not_durable'
    return 'UNIMPLEMENTED'  # A plausible trace is still not a production certificate.


def demonstrate_red():
    import unittest
    class UnsafeSampledTimeCandidate(unittest.TestCase):
        pass
    for case in CASES:
        def test(self, case=case):
            candidate='CERTIFIED' if 'clock_sample' in case.events else 'UNAVAILABLE'
            self.assertEqual(candidate,'UNAVAILABLE',case.name+': '+case.reason)
        setattr(UnsafeSampledTimeCandidate,'test_'+case.name,test)
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(UnsafeSampledTimeCandidate))
    return 0 if result.wasSuccessful() else 1


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--demonstrate-red',action='store_true',required=True)
    parser.parse_args()
    raise SystemExit(demonstrate_red())
