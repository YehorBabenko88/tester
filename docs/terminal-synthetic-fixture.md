# Explicit terminal LINEAR synthetic fixture (Issue59)

This is a NEW authored fixture, not a relabeling of the pinned F998 spot data or
downloaded Bybit data. Original `fixtures/f998` files and their pins are unchanged.
The old `certified_fixture_snapshot()` and `.publish_fixture()` default stay spot.

New package directory `fixtures/pc4-terminal-linear-v1` contains standalone
snapshot.json/strategy.json and manifest.json. Code pins the exact manifest SHA256,
which in turn pins both JSON byte hashes. Namespace `synthetic:pc4-terminal-linear/v1`
is distinct from old F source evidence. `certified_terminal_fixture_snapshot()` and
`certified_terminal_fixture_strategy()` return only those checked files. Registry
`.publish_terminal_fixture()` is an explicit operator choice; read verification
accepts only the exact old and exact new certified wrappers, never arbitrary caller
JSON, a caller rehash, changed source labels or a third variant. Distribution
package-data includes new JSON. No real historical admission path was broadened.

The F-required venue/category identity is bybit/linear/BTCUSDT/1, but every price,
volume, fee and funding assumption is explicitly hypothetical. Four fictional
one-minute candles cover [1700000040000,1700000280000); no public Bybit history is
asserted. F's `official_trade` candle kind selects its closed trade-price OHLC
execution model, not authentic market source: all provenance is synthetic_fixture,
all coverage refs use synthetic namespace, wrapper certification is SYNTHETIC_ONLY,
real_market_coverage stays NOT_PROVEN, and genuine engine warnings retain
SYNTHETIC_DATA/NON_CERTIFIED/OHLC_EXECUTION_MODEL.

Matching strategy uses funding_policy=required (spot not_applicable is invalid for
linear). Its funding evidence deliberately declares one HYPOTHETICAL fixed event
at the third candle start, rate0.0001 and hypothetical mark100. This is not Bybit's
funding schedule, rate, mark, settlement or timing evidence. The explicit source
ref says hypothetical-funding-schedule, manifest description and wrapper funding
field repeat this limitation. Fees are a hypothetical rate0.001. Existing F alone
calculates fills, fees, funding, PnL and levels; no financial calculation was added.

Executed local genuine-F pure replay produces3 fills,1 closed take_profit trade
and funding_costs0.0100, retaining non-certified synthetic warnings. These are
deterministic fictional fixture results, not investment or real-market results.
One open position remains at the end, honestly retained by F; this fixture does
not claim a tested stop-loss exit. Matching strategy cannot be silently changed by
the factory. Operator registration may still use reviewed typed strategy parameters
under its separate exact input-digest grant; it cannot alter snapshot certification.

TDD: missing new factory imports gave RED. New package-data regression then gave
1failed/3passed before package inclusion fix. Final focused tests cover both fixed
variants, caller-rehash rejection, original spot compatibility, immutable operator
publication/read boundary, genuine F deterministic bundle verification, actual
hypothetical funding debit and package distribution declaration. Hosted PG test is
parameterized over spot+terminal publication/restart/readback into F; it was not
run locally by the contributing agent. Root owns actual PG/browser/runtime gates.
