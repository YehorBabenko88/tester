import unittest
from tests.storage.visibility_protocol_cases import BASE, CASES, reject_reason
from src.storage.knowledge_time import project_knowledge


class VisibilityProposalTests(unittest.TestCase):
    def test_each_unsafe_trace_is_rejected_for_its_specific_reason(self):
        for case in CASES:
            with self.subTest(case=case.name):
                self.assertEqual(reject_reason(case.events),case.reason)

    def test_plausible_trace_is_not_an_implemented_certificate(self):
        self.assertEqual(reject_reason(BASE),'UNIMPLEMENTED')
        self.assertEqual(project_knowledge([])['status'],'UNAVAILABLE')
        self.assertIsNone(project_knowledge([])['quality_available_at_ms'])

if __name__=='__main__': unittest.main()
