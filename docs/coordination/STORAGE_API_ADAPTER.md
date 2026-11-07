# B→E lossless trade boundary

src/integration/storage_api.py adapts the current B5154efab Store.read representation to approved v1 Trade/API records. It is coordinator-owned preparation, not proof that E endpoints have been wired.

- project_trade preserves identity and exact decimal strings, maps payload.size/side to quantity/taker_side, converts genuine stored ns to strings and genuine us to trailing000 ns with precision=us. It never reconstructs omitted sub-ms precision from ms.
- An authoritative provenance_resolver(record_key) is required for read_trades. It must return all actual source observations with source_kind/source_ref/hash/ingestion_run/source_timestamp/precision. Current B source-observation/run linkage is a tracked blocker; creating a new read-time UUID would fabricate provenance and is prohibited.
- Store.coverage maps conservatively. A boolean complete without durable evidence_refs remains UNVERIFIED/UNKNOWN. Corruption/unavailability stays UNAVAILABLE with original reason and ERROR; an empty page cannot erase a known integrity failure or establish completeness.
- B keyset position (timestamp_ms,record_key) is encoded with request scope/range. At most1000 public rows; internal overfetch1 detects continuation. Equal timestamps retain all stable IDs. Returned boundaries and strict key ordering are checked; instrument scope cannot change mid-page.
- Shared validate_record/validate_response validate every financial row plus envelope. Internal SQL need not equal API JSON. No aggregate, delta, CVD, VWAP or backtest calculation is implemented here.

Tests use explicit synthetic producer rows and authentic-context fixtures, not fake live API claims. Ten projection/cursor/metadata tests pass. Independent review caught empty-corruption demotion and boundary/order failures; each was reproduced RED and repaired GREEN. The prior real live baseline smoke remains separate from this not-yet-wired v1 API adapter.

