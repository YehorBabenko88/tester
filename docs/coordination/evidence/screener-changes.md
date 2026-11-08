# Screener fix evidence

Only product changes: work/repo/app.py and new work/repo/test_screener.py. No commits, pushes, remote writes, or chart modifications.

Wrote tests first, then ran `../venv/Scripts/python.exe -m unittest test_screener -v` from work/repo. RED: 4 test methods, 28 subtest failures and 3 errors; old/current outlier fixture returned 999% rather than 2%, query requested 65 minutes, incomplete/invalid input was accepted or raised.

Changed one_hour_range to query precisely the 60 closed minute slots before the current minute, exclude any returned out-of-window records, reject missing/duplicate/misaligned timestamps and nonfinite/nonpositive or incoherent OHLC, and return unavailable NaN rather than a misleading ranking. API transport errors retain existing handling. No extra module/import dependencies.

GREEN: same exact command ran 4 tests successfully (0.110s). Fixture with open=100, high=101, low=99 gives (101-99)/100*100=2%; the five preceding extreme minutes and current candle are ignored. All four OHLC fields are checked against NaN/infinities/zero/negative/malformed values. Full suite intentionally deferred pending confirmation concurrent engine edits have ended. No live DB/browser/exchange check performed.

# Independent contract review (no contract edits)

Executed `../venv/Scripts/python.exe -m unittest test_contracts -v`: 4 methods pass. Additional direct Draft202012Validator probes:

- **P2: financial/timestamp regexes accept trailing newlines.** contracts/v1/market.schema.json:85,89,93 use `$`; Python regex treats the position before a terminal newline as an end anchor. A valid synthetic trade mutated to price='1\n' or ts_ns='1700000000123456789\n' still passes the root schema. This contradicts exact decimal and 19-digit timestamp representation, especially across consumers with stricter parsing. Add negative fixtures and reject line endings using a portable absolute-end constraint, e.g. `(?![\\s\\S])` after the numeric expression.
- **P2: impossible execution values pass.** trade price/quantity at :89/:93 both accept '0'; zero execution price and zero size pass together. Reject zero for execution fields (retain zero volume where appropriate); add semantic tests. Instrument tick_size/qty_step similarly permit zero (:37/:41).
- **Review limitation: response data is unconstrained (:490).** A success response containing arbitrary {'price': NaN} is valid because only null-vs-success shape is constrained. If this is deliberately a generic envelope, explicitly validate endpoint data separately and add tests proving that composition; otherwise reference the intended payload union/schema.
- **Semantic checks absent:** progress completed_units=20,total_units=10,status='completed' passes (:403 onward). Candle end-before-start/OHLC ordering and coverage interval relationships likewise have no cross-field validation. These need runtime validators if intended as acceptance guarantees; standard JSON Schema cannot generally compare instance fields. Current test_contracts.py tests syntactic examples only and has no candle/provenance or cross-field negative tests.

