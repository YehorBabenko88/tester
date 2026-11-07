# Lossless A handoff — PR10 correction

Verified consumer pins: shared contract/validator `9766c0093e2608280875ac56db074414a3d62c69`; B baseline `5154efab05d44b96f4aa83819672396d75679f2e` and B run ledger `47fd36f32389174377682a13862f5b36c7a886da`. A changes only its ingestion, fixtures and documentation; it neither changes the shared schema nor B's tables.

## Export format and migration

`Journal.export(path)` now writes **one atomic JSON object**, `format='bybit-ingest-handoff', schema_version='1.0'`. This intentionally replaces unsafe row-only JSONL. File extensions do not change the content type; the CLI default is `handoff.json`. Consumers must read the package and select the appropriate projection. Do not stream its top-level object as if it were an old market row.

The package contains:

| Member | Meaning |
|---|---|
| `runs` | Map keyed by actual `ingestion_run`, with request and lifecycle state. Legacy imports are explicitly labeled, not recreated exchange runs. |
| `tasks` | Append-only task-attempt snapshots, including failed, interrupted, retried and replayed observations. |
| `evidence` | Map keyed by evidence reference. Includes original REST result, actual request parameters/cursor in/out/count/terminal classification; original HTTP body as base64/hash when captured by API; archive download/validation manifests; task observation snapshots. |
| `raw_record_versions` | Every original envelope version, including prior sources/conflicting values. Financial strings and original raw timestamp text are preserved. |
| `records` | Each source version/run association: explicit availability, separate coverage, provenance, ingestion_run, evidence_refs, raw_record, storage_record, contract_kind/contract_record, projection_errors and quarantine flag. |
| `storage_batches` | B-compatible unique publication records, evidence and checkpoint. Known identity conflicts and invalid storage projections are excluded. |
| `conflicts` | Same stable identity with different semantic values. Both source versions survive; no last-writer-wins publication. |
| `artifacts` | Original compressed archive bytes as base64, keyed by SHA-256, after a fresh checksum comparison. Missing/changed files are explicitly UNVERIFIED with a reason and no fabricated bytes. |

`record.evidence_refs` and `record.coverage.evidence_refs` resolve directly against `package.evidence`; every provenance ingestion_run resolves against `package.runs`. Archive source hashes resolve against `package.artifacts`; the earlier storage_uri remains an original locator, not a requirement to access A's filesystem. Task snapshots remain independently available even where legacy producers lacked a record-to-request link. Unknown source/version records remain raw-only or unverified; successful task status and an official-looking URL alone do not establish availability.

SQLite keeps the latest working cache separately from append-only versions/attempts. Restart reuses validated acquisition progress but cannot erase previous attempts. Legacy cached task flags are not reused as new proof. Export snapshots are serialized under the journal's process lock, then fsynced to a temporary file and atomically renamed. Use one process owner and a bounded journal per queue work unit. The current self-contained exporter materializes its snapshot/artifact bytes in memory; it is not a streaming whole-exchange export.

Trade conflicts compare exact economic price/quantity/side with unchanged identity/time/precision, rather than the spelling of decimals or ancillary CSV columns. For example price `1.0`/`1.00` and size `2.00`/`2.0` represent one execution. Formatting uses exact Decimal construction and fixed-point formatting without float conversion or context-sensitive normalization. Original raw envelopes, numeric spellings and source hashes remain unchanged. Equivalent source versions sharing an identity and run are published in separate unique-per-batch layers, preserving both B source observations while B's economic record is idempotent. Actual price/quantity/side/time/precision differences still quarantine the identity. Nonnumeric/invalid values retain raw semantics and explicit projection errors. See `decimal-regression.json` and the pinned fixture command's optional isolated `--postgres-dsn`/`--fixture-output` check.

## Two explicit consumer projections

`src.ingest.handoff.storage_record(raw, metadata)` targets B `normalize()` / `Store.ingest()`. `contract_record(raw, storage, metadata)` targets strict shared `validate_record(kind, record)`. They are deliberately different shapes: the shared schema rejects additional dataset/payload/source envelope keys, while B requires them. `records[*].contract_record` can be passed directly to the shared validator; each `storage_batches[*]` can be passed to B without rewriting foreign code. For B47 use `store.ingest(batch['records'], batch['evidence'], batch['batch_id'], batch['checkpoint'], run_id=batch['ingestion_run'])` and configure/check its storage-root disk gate before acquisition. B's `source.source_kind` is explicitly mapped to `rest`/`archive`; shared provenance retains `bybit_rest`/`bybit_archive`. A legacy bookkeeping run without retained acquisition proof is not forwarded as authentic source provenance.

| A source | B storage projection | Shared projection |
|---|---|---|
| `kline`, array OHLCV | dataset=`candles`, named open/high/low/close/volume/turnover | `candle`, kind=`official_trade`, actual exchange-time `is_closed` |
| mark/index/premium arrays | same named OHLC; missing source volume stays missing | Explicit projection error if required volume/positive OHLC cannot be supplied truthfully |
| `long_short_ratio` | dataset=`ratio`, value=`buyRatio` | observation kind=`ratio`, unit=fraction; original sellRatio remains in raw_record |
| open_interest | value=`openInterest` | observation kind=`oi`, unit=USD for inverse/base_coin for linear |
| funding | value=`fundingRate` | observation kind=`funding`, interval=`event`; no invented funding cadence |
| trades | original exchange ID in `payload.trade_id`; named price/size/side | strict trade_id, price/quantity/taker_side; unknown-side B incompatibility is explicit |
| source precision `s`/`ms` | timestamp_ns=null | timestamp_ns=null |
| source precision `us` | exact integer timestamp_us, timestamp_ns=null | decimal-string timestamp_ns=timestamp_us*1000, timestamp_precision stays `us`; trailing000 is a unit conversion, not a finer measurement |
| source precision `ns` | exact integer timestamp_ns | exact decimal-string timestamp_ns |

Source bytes/hash, raw timestamp text, source_kind and ingestion_run remain alongside the projections. A synthetic fallback requires explicit opt-in; shared id_origin=synthetic and an identity_method, while B uses its documented source.record_key/composite identity rule. Neither can establish COMPLETE.

## Counts and pagination evidence

Each actual REST response is persisted **before** row filtering/deduplication. `evidence[ref].pagination.response_count` is the original array length. `cursor_in` is the actual transmitted token; `cursor_out` is the actual returned field (including an empty string); `cursor_out_field_present` distinguishes an absent field. Raw response and HTTP bytes are authoritative. `terminal_condition` describes the producer's observed endpoint pagination outcome; it is not proof of historical coverage. Failed requests retain count=null outside the successful pagination schema instead of pretending a zero-row response occurred.

B's `validate_evidence()` requires one publication count equal to the unique rows and a linked page chain. It cannot consume arbitrary overlapping retry/bisection/time-window requests as one exchange cursor chain. The handoff therefore provides an explicitly labeled **local_publication_batch** with `count_semantics='unique_published_rows'`, status=UNVERIFIED and no available_range attestation. Its null local cursors refer only to the single local batch; they are never claimed as Bybit cursors. Actual outgoing/returned Bybit tokens and terminal facts are unchanged in the referenced acquisition evidence. `source_response_count` separately sums original REST response lengths (null for archive-only batches); `source_archive_row_counts` preserves validated CSV row counts per hash. All original retry/duplicate/empty responses remain in the evidence manifest.

Each B evidence object also embeds `acquisition_manifest` with the transitive source request/task/run proofs and compressed artifact bytes, so B's persisted evidence retains resolvable references without relying on A's database. Empty/failed windows not represented by published rows receive a zero-record observation batch, keeping the actual acquisition run in B's ledger. An empty publication does not hide a nonempty invalid source response: its actual response_count remains separate and its status is UNVERIFIED. Only all-empty successful source attempts produce an EMPTY observation batch.

No EMPTY, EXHAUSTED, closed-candle flag, local publication page or successful export promotes coverage to COMPLETE. Availability is AVAILABLE/PARTIAL/UNAVAILABLE/UNVERIFIED and is separate from coverage PARTIAL/UNKNOWN and B's independent `verified` result. Conflicting/invalid rows are not quietly published.

## Reproduction

```powershell
python -m unittest discover -v
git fetch origin coordination/master workstream/b-integrity
python -m tests.ingest.verify_handoff --references work/handoff-reference
python -m tests.ingest.verify_handoff --references work/handoff-reference-latest --storage-sha 47fd36f32389174377682a13862f5b36c7a886da
python -m tests.ingest.live_handoff --output-dir work/live-handoff --references work/handoff-reference
```

The pinned fixture command extracts foreign reference files into ignored scratch directories using `git show`; no foreign branch/file is modified. It validates strict shared trade/candle/observation/pagination structures and semantics, then executes B's actual normalize/validate_evidence functions. The live command verifies original HTTP/archive hashes and run/ref resolution. An optional `--postgres-dsn` is for an explicitly isolated scratch DB with the selected B migrations applied; it runs B Store.ingest, replay and Store.read, requires count stability and `verified=false`. Select B47 with the same `--storage-sha` option; its actual run authenticity and persisted transitive evidence references are also checked through Store.read/provenance/coverage_evidence. The real prelaunch empty-response window stays unverified while its empty run ledger is preserved. The checked PostgreSQL18.4 report is in `handoff-live-evidence.json`; the scratch server was stopped after the test.

The shared schema's current signed-price/volume limitation is documented on Issue #7. This correction proves the supported A→B paths, not an all-source/full-system approval, global source completeness, historical orderbook/liquidation availability or a physical deployment. Draft PR10 remains unmerged.
