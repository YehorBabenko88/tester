import unittest

from scripts.ops_load_receipt import LoadSample, EvidenceRefusal, evaluate_load

class LoadReceiptTests(unittest.TestCase):
    def setUp(self):
        self.symbols = {f"TEST{i}" for i in range(10)}
        self.samples = [LoadSample(f"TEST{i%10}", i * 4.0, float(i % 17),
                         1024 + i, float(i % 80), True) for i in range(201)]

    def test_sufficient_fixture_is_observed_only(self):
        result = evaluate_load(self.samples, self.symbols)
        self.assertEqual(result["symbol_count"], 10)
        self.assertFalse(result["provenance_verified"])

    def test_short_duration_refused(self):
        with self.assertRaises(EvidenceRefusal):
            evaluate_load(self.samples[:100], self.symbols)

    def test_missing_symbol_refused(self):
        with self.assertRaises(EvidenceRefusal):
            evaluate_load([s for s in self.samples if s.symbol != "TEST9"], self.symbols)

    def test_failure_budget_refused(self):
        rows = list(self.samples)
        rows[0] = LoadSample("TEST0", 0.0, 1.0, 1, 1.0, False)
        rows[1] = LoadSample("TEST1", 4.0, 1.0, 1, 1.0, False)
        rows[2] = LoadSample("TEST2", 8.0, 1.0, 1, 1.0, False)
        with self.assertRaises(EvidenceRefusal):
            evaluate_load(rows, self.symbols)

    def test_nonfinite_refused(self):
        rows = list(self.samples)
        rows[0] = LoadSample("TEST0", float("nan"), 1.0, 1, 1.0, True)
        with self.assertRaises(EvidenceRefusal):
            evaluate_load(rows, self.symbols)

if __name__ == "__main__":
    unittest.main()
