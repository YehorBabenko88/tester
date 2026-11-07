"""Exercise the screener calculation without starting Streamlit or PostgreSQL."""
import ast
import math
from pathlib import Path
import unittest


class ScreenerTests(unittest.TestCase):
    end = 12000000

    def rows(self):
        return [[str(t), '100', '101', '99', '100', '10']
                for t in range(self.end - 60 * 60000, self.end, 60000)]

    def calculate(self, rows):
        function = next(n for n in ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8')).body
                        if isinstance(n, ast.FunctionDef) and n.name == 'one_hour_range')
        function.decorator_list = []
        class API:
            def api(inner, path, params):
                self.params = params
                return {'list': rows}
        scope = {'math': math, 'utc_ms': lambda: self.end + 12345, 'bybit': API}
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'app.py', 'exec'), scope)
        return scope['one_hour_range']('linear', 'TESTUSDT')

    def test_exact_hour_and_hand_derived_range(self):
        self.assertEqual(self.calculate(self.rows()[::-1]), 2.0)
        self.assertEqual(self.params['start'], self.end - 60 * 60000)
        self.assertEqual(self.params['end'], self.end - 1)

    def test_older_extreme_and_current_candle_are_ignored(self):
        extra = [[str(t), '100', '1000', '1', '100', '10']
                 for t in range(self.end - 65 * 60000, self.end - 60 * 60000, 60000)]
        extra.append([str(self.end), '100', '1000', '1', '100', '10'])
        self.assertEqual(self.calculate(extra + self.rows()), 2.0)

    def test_incomplete_or_duplicate_schedule_is_unavailable(self):
        rows = self.rows()
        for candidate in ([], rows[:-1], rows + [rows[0]], rows[:-1] + [rows[0]],
                          [[str(int(r[0]) + 1), *r[1:]] for r in rows]):
            with self.subTest(candidate=candidate):
                self.assertTrue(math.isnan(self.calculate(candidate)))

    def test_invalid_ohlc_is_unavailable(self):
        for index in range(1, 5):
            for bad in ('NaN', 'Infinity', '-Infinity', '0', '-1', 'bad'):
                rows = self.rows()
                rows[10][index] = bad
                with self.subTest(index=index, bad=bad):
                    self.assertTrue(math.isnan(self.calculate(rows)))
        self.assertTrue(math.isnan(self.calculate([['bad']] + self.rows()[1:])))


if __name__ == '__main__':
    unittest.main()

