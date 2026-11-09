# Independent mark/index profile review

Final exact reviewed PR68 head:9fce906230d073819e327e510f2791ef7a53d02a (parent d3a64f9d8126f5bf551d8326050833c20159449d). Separate detached checkout work/pc4-agents/review-prices-9fce. Source/in-process review only; no A edits, real PostgreSQL/Bybit/browser/process/service/native actions.

## Bounded result

No remaining blocking defect found in the reviewed source scope. One public-parser P2 was reproduced on d3a and fixed by owner at9fce: decimal-shaped time strings0.0/60000.0 returned200 on price1.1 while the original candle route returned422. Final route now reuses existing HttpTime; regression assertions pass.

The actual reference-profile /api/sources response was separately tested in-process with real ReferencePriceReader(None), create_app and TestClient:200 schema1.0 with additive kinds/path/version/not_integrated. The source dataset intentionally treats each source row as an open object while validating its canonical envelope/list, so these additions do not trigger ResponseValidationError. This probe requires no database/catalog/source network.

## Source guarantees inspected

- Price-only records adopt existing contracts/v1_1 validator and schema. No volume or turnover is invented; the projection copies only OHLC and source/time metadata. Shared contracts/v1 and contracts/v1_1 are byte-unchanged versus6a. Existing /api/candles still uses its v1.0 model/route and volume requirement; new /api/price-candles mounts only for the explicit reference factory and typed reader.
- Static profile is restricted to candles or reference-prices/1.1. Fresh seed records its profile and config SHA; settings_for reconstructs that exact recognized configuration. Defaults remain the candle factory. prepare-price-sources is a separate operator action and existing fresh-root/refuse-adoption checks stay in place.
- Handler admission still validates cached exact category/symbol/contract/status, source, original cadence and bounded acquisition window. Funding/OI/trades are not enabled by this profile. Existing provider loader ownership checks remain unchanged.
- The existing worker job fence/commit_batch path is reused: B ingest, instrument metadata, original projection insertion, reread verification and measured acknowledgment execute in its fenced transaction; conflicts roll back and require a fresh valid fence to mark damage. The default LabHandler method extraction retains old candle behavior.
- Mark/index use existing tester_storage tables and tester_lab.projections. Existing limited-role schema DML grants already cover them; no new runtime schema creation or role grant is introduced. Fresh prepare/role/serve lifecycle and DSN ownership controls are retained.
- Source projection requires unquarantined observed acquisition, original ms timestamp/endpoint/kind/category/symbol/interval and exchange-time closure. Rereads require authentic declared source-run linkage, matching stored OHLC and original projection, correct scope and non-corrupt history. Availability is observed PARTIAL (empty UNAVAILABLE); coverage remains UNKNOWN, not inferred historical COMPLETE.
- New read path is bounded to1000 records and archive reads0, uses scoped HMAC continuations and explicitly separates version/kind from the trade-candle endpoint. D reference-price panels and derived analytics remain NOT_INTEGRATED.

## Executed checks

Final9fce command:

```
work/venv/Scripts/python.exe -m pytest tests/integration/test_reference_prices.py tests/integration/test_catalog_market.py tests/integration/test_lab_market.py tests/platform/test_api.py tests/platform/test_canonical.py tests/platform/test_request_scope.py -q --timeout=8 --timeout-method=thread --basetemp=../pytest-review-prices-9fce
```

**95 passed**, one existing Starlette/httpx deprecation warning. Includes15 new reference-profile tests. Checkout clean.

Additional in-process HTTP probes on the otherwise-identical d3a route: actual source response200; negative/reversed/out-of-max time and limit0/1001 inputs422; same-scope continuation accepted, changed kind/interval/symbol/range/version rejected422. Controlled reader responses in those probes are fixtures, not real HTTP source/PG acceptance. Source diff confirms shared contracts, API scope/jobs/owned loader and storage API were not changed.

## Remaining acceptance boundary

This review does not certify actual mark/index endpoint responses, complete pagination, persisted provenance, limited-role fenced publication, HTTP body hashes, readback or browser panels on a new runtime. Root must publish a new immutable composed candidate, select the explicit fresh profile, perform one bounded real sample under its limited role, then compare original source receipts, raw price-only rows, v1.1 projection and acknowledgments. Existing installed6a data/behavior is not upgraded by this source review. No duplicate implementation or physical action was performed.
