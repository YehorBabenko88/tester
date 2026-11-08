# B provenance projection review

Target: B47fd36f and MASTER provenance_from_storage. Seven synthetic regressions pass. Independent review found explicit source.ingestion_run versus caller run mismatch; regression reproduced RED, projection now rejects it. B write-time task is separate Issue2/comment6708925158.

Real disposable PG18.4, loopback55449, independently executed one roundtrip using B actual Store.ingest/read/provenance and MASTER read_trades: same source in two caller runs remains one trade with two canonical provenance entries; original timestamp string and exact price preserved; UNKNOWN coverage retained. No source completeness or full API wiring established. Probe/log in evidence; fixture database only, stopped PG afterward. First scratch attempt used lowercase generated synthetic symbol, correctly rejected by common contract; corrected fixture uppercase, no relaxation of symbol validation. Initial pg_ctl without explicit own port failed; retried with127.0.0.1:55449 only, no settings/services/firewall changed.

Trusted B resolver required. Generic import/legacy are unsupported, no inferred source metadata, and no promotion to COMPLETE. Source hash/original timestamp absent remains null. Factory adoption and active coverage-evidence resolver are still pending. Root total79 methods,77pass,2optinPGskip.
