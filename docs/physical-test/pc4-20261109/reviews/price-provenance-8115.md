# Independent PR68 backend review — bounded source assessment

Reviewed exact `81150ad9731de445e0630a6155322981a4c0a73c`, including initial
`d3a64f9...` and time-validation follow-up `9fce906...`, against physical source
base `6a24e50d51f4d65783c4436b94fe87cc883acbeb`. Source snapshots were extracted
from raw Git archives, not the author's mutable worktree. No source changes,
GitHub mutations, source-network calls, PostgreSQL, installed runtime or native
process actions were performed by this reviewer.

## Result

No remaining material source blocker found within this review. One P2 error path
was reproduced RED on 9fce and confirmed fixed on 8115. This is bounded approval
of the code/protocol boundary, not actual Bybit/PG/portable/browser acceptance.

### Resolved P2: B missing-source row must be typed unavailable

B `read_page` LEFT JOINs raw records through record_sources, source_observations
and ingestion_runs. A raw record lacking record_sources still produces an
observation with `source=None` and `authentic_run=False`; outcome may remain OK
with coverage UNKNOWN. On 9fce, ReferencePriceReader unconditionally called
`o['source'].get(...)`, causing uncaught AttributeError (HTTP500 is inferred from
the unchanged API error handlers) before the intended typed
provenance refusal. No invalid data was served, but the safe unavailable response
was lost. 8115 guards dict shape and sends the same case through the existing
strict provenance gate to ProviderDataUnavailable.

Independent expected-correct probe script:
`work/reference-price-adverse-review.py` (execute with PYTHONPATH set to the exact
snapshot root). It exited1 on 9fce only for this case, and exited0 on 8115.

| Boundary scenario | Exact8115 result |
| --- | --- |
| Valid original linked mark row | DATA, coverage UNKNOWN |
| No observations | ProviderDataUnavailable |
| Valid observation plus unlinked-source observation | ProviderDataUnavailable |
| Actual B missing-source LEFT JOIN shape (`source=None`) | ProviderDataUnavailable |
| Undeclared original run | ProviderDataUnavailable |
| Original/stored run mismatch | ProviderDataUnavailable |
| B resource budget exhaustion | ProviderDataUnavailable |

### Why empty unlinked_sources does not upgrade current B page provenance

B page observations are produced for the entire requested range by LEFT JOIN,
including missing/legacy edges. `authentic_run` requires caller-origin, matched
original declaration, normalized raw-envelope/source hash/identity semantics,
matching evidence scope and original precision, and closed candle timing.
Missing edges become false observations rather than disappearing into a separate
unlinked_sources collection. Budget exhaustion resets rows and returns non-OK.

A retains **all** observations for each selected record; it does not filter out
false ones. Its `bool(observations) and all(authentic_run is True)` therefore
rejects any missing edge. The strict declared-run adapter separately checks
run_id, original source.ingestion_run and source_run_status. Reconstructing the
legacy adapter's unlinked_sources=[] shape is safe under this exact B page API;
it is not a generic license to discard a future separate missing-source state.
No historical visibility or completeness certificate is produced: coverage stays
UNKNOWN and observed rows/envelopes are PARTIAL.

## Other reviewed boundaries

* **Ownership/config:** profile is a two-value static selector. Fresh-root seed
  persists the profile and config hash; settings_for reconstructs the exact
  allowed config and verifies root/source/lab/source-SHA/hash. No post-start
  config mutation, root adoption, private-file exposure or process-guard change.
* **DSN/role:** existing verified PC4 DSN boundary adapter, bootstrap/admin
  separation and limited-runtime checks are unchanged. Profile uses the same
  pre-created storage/lab/catalog/research schemas and existing DML grants.
  Constructors/serve introduce no new DDL or grants.
* **Worker fence:** three LabHandler hooks preserve legacy defaults. Reference
  handler retains the same job commit callback, bound B connection/savepoints,
  original-projection insert, raw content-hash/readback checks and acknowledgment
  in the existing fenced transaction. This is static/pure evidence, not a new
  concurrency/lease/rollback PostgreSQL proof.
* **Data:** B identity already includes dataset, so mark/index do not collide
  with official candles. Exact original REST endpoint/category/symbol/interval,
  original ms timestamp, caller run and exchange-time closure are checked.
  Decimal strings are preserved; no price/volume aggregation. v1.1 volume stays
  absent or null, never synthesized as zero or trade volume.
* **HTTP:** new price-only route mounts only for the exact selected reader
  factory/type. Existing v1.0 candle route/validator is unchanged. HttpTime now
  rejects decimal-shaped timestamps. Query scope/UTC cadence/kind/version and
  HMAC continuation binding remain explicit; selected source cadence only.
* **Compatibility limit:** DownloadRequest's enum recognizes mark/index globally
  as an additive schema extension, but the old handler still refuses those jobs
  and the default factory does not mount the v1.1 read route. Do not describe
  funding/OI/trades, derived analytics or a D price panel as integrated.

## Executed versus not executed

Independently ran `pytest tests/integration/test_reference_prices.py -q` on exact
8115: **15 PASS** (in-memory/in-process/SQLite-journal HTTP replay fixtures).
Seven additional independent B-page boundary scenarios passed as above. The one
Starlette TestClient/httpx deprecation warning is not a runtime failure.

No PostgreSQL, actual Bybit requests, worker subprocess, port binding, installed
source, browser or physical profile activation was exercised here. ROOT must
freeze the new immutable source candidate and separately verify actual source
download → limited-role PG/fenced acknowledgment → v1.1 readback, hashes and
missing-source/error cases. Existing physical6a results do not prove this profile.
