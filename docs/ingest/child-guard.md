# Child isolation correction after A467 review

Both child entry modes call the same read-only gate before Store construction, reads, ingestion or DDL. The normal unittest class also calls it, so direct class execution cannot skip the gate. Parent-passed SHA-256 values bind pins.json and restart-state.json; fixed producer/storage/contract SHAs, production file hashes and exact imported module paths are checked. The database must report the fresh run pgdata path. Restart metadata must target a generated pair database and the exact replay fixture/handoff paths.

These checks prevent accidental/misdirected fixture execution. They are not a security boundary against a process that can replace its environment and filesystem together. Negative tests use mocks only; no user database is contacted. They assert no Store construction, no suite execution and no SQL except SHOW data_directory. A valid context control prevents blanket rejection from passing these cases.

Validation: 44 unit methods (42 existing + 2 guard methods), six real PostgreSQL 18.4 cases, actual server restart/new-process replay and verified shutdown. Production pins remain A3098d55 / B e236395 / shared9766c00; no production source changed. This is synthetic parser/API fixture integration, not live acquisition or full pipeline acceptance.

Run from repository root:

```powershell
python -m unittest discover -v
python -m tests.ingest.pinned_roundtrip --work-root work/pinned-pair --pg-bin "C:/Program Files/PostgreSQL/18/bin"
```

Evidence: child-guard-report.json, child-guard-unit-tests.txt, child-guard-postgresql-tests.log. The earlier pinned-pair evidence is retained as historical A467 evidence. Root replay remains separate from author evidence.
