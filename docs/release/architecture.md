# Architecture and repository

`src/api` provides the API, worker/supervisor composition and release lifecycle. `src/api/guardians` contains the Code, Data, Storage and Exchange proposal engines plus the common decision supervisor. Runtime transport remains the canonical ingest public transport rather than a second release-only exchange client. Durable state, leases, fencing and bounded retry decisions remain runtime contracts.

`src/ingest` supplies public historical-data ingestion; `src/storage` supplies storage primitives/migrations; `src/control`, `src/integration`, `src/analytics`, `src/backtest` and `src/strategy` hold integrated platform capabilities. `contracts` contains versioned DTO/schema contracts. `frontend` is the shipped static browser terminal; it has no application bundler or compilation command. Its exact Git blobs are deployed directly. Node/Playwright are development and browser-acceptance tools.

The release layout is `Setup-Tester.ps1`, `payload/`, `runtimes/python/`, `runtimes/postgres/`, `wheelhouse/`, `component-inventory.json`, `sbom.json`, `release-notes.md` and, after owner signing, `release-manifest.json`. All regular payload files except the envelope are covered by its signed SHA-256 map. The unsigned manifest and unsigned status sit outside that directory.

Private PostgreSQL uses an owned cluster and database, separate from the existing system cluster. Transactions, OHLCV/provenance/checksum checks, duplicate handling, interruption/replay and concurrent access require the dedicated PostgreSQL integration suite. A passing HTTP readiness response alone does not establish durable data acceptance.

The API schema and routes are defined by the source-bound FastAPI application and versioned contracts; retrieve `/openapi.json` from the accepted instance. Browser acceptance must cover instrument navigation, candles, history progress, results, worker state and recovery after API interruption. Public Bybit network access is environment-dependent; 403/429 and offline states require bounded retry and truthful errors.

`tests` contains regression and integration evidence. `docs/adminit-acceptance`, `docs/evidence`, `docs/physical-test` and Guardian coordination documents are historical evidence, retained with their original SHAs. New changes do not retroactively certify those old results for a new release SHA.
