# Standalone Trader Intelligence — gated integration plan

Normative requirements: Issue25 and PR11 architecture correction6708565655. Only tester; no cross-repository Gateway or runtime integration. Status: NOT_READY. This plan is implementation authorization sequencing, not evidence that a source is permitted or a feature completed.

## P0 — existing A–G integration first

Freeze producer and consumer SHAs. Integrate tested A export→B ingestion/provenance→C canonical calculations→E request-bound API→D quality-preserving charts. F remains offline and requires explicit validated strategies, point-in-time evidence and honest fill timestamps. G uses tester's typed local control API with owned-provider allowlist, authentication, idempotency, cancellation and approval gates; never an assumed Codex or foreign-project API. Resolve G canonical PostgreSQL ownership instead of duplicating authoritative SQLite job/audit ledgers.

Blocking acceptance: actual lossless source/run/evidence round-trip; duplicate/conflict and exact time/decimal invariants; old/late/unclosed/partial data; same-timestamp cursors; request identity/range/resource binding; UI cannot promote availability/coverage; no-lookahead and honest uncertainty; kill/restart/concurrent import; safe retention/backups/resources/service dry-run; 10 instruments and real scoped source checks; successful CI for exact candidate SHA. No main merge or physical service/deployment.

## S0 — source registry and permission gate (current stage)

Owner A researches; MASTER reviews, B defines provenance links. Registry entries separately describe technical access, identity assurance, publication/retention/filters, completeness evidence and applicable rights. No source enabled by default. Public web visibility/no-auth/API-code license is not data redistribution permission. A trading API permission or follower subscription is not research authorization. User exports require owner consent and verified file scope. Keep unknowns explicit.

Bybit anonymous prints are market context only; public master disclosures are product/visibility-dependent and no permitted documented third-party history feed has been established. Hyperliquid/Polymarket document public account reads with caps/filters; account/wallet identity is pseudonymous, not a verified human. Current terms/version limits are in SOURCE_REGISTRY.json and audit files. No trader-history acquisition is enabled.

Exit gate: at least one concrete intended-use source with actual permitted access and recorded rights/consent, bounded sample with authentic source identifier, request/terminal evidence, original response hash, observed/published time and source revision. A zero-address protocol probe or generic public page does not satisfy this gate. If no such source is available, the product must say DATA_UNAVAILABLE with reason.

## S1 — contracts, authorized import and storage (after P0)

Owners A+B+E; MASTER schema review. Add separate trader_sources/profiles/disclosed_events/positions/observations/revisions and evidence links through B migrations, not a parallel database. Stable key (source_id,source_scoped_trader_id,source_trade_id), Decimal strings, source precision and separate event_time/published_at/observed_at/available_at. Retain original bytes/hash/consent revision and collection filters; record empty/capped/delayed/revoked coverage. Missing fees/SL/TP/leverage/position direction remain null/UNKNOWN. Authorized CSV fixtures first; a public pilot only after S0 gates.

Keep data categories distinct: ANONYMOUS_MARKET, PUBLIC_ACCOUNT_DISCLOSURE, OWNER_AUTHORIZED_EXPORT, RECONSTRUCTION_HYPOTHESIS. No anonymous-print attribution, hidden UID discovery or account-to-human/cross-platform linkage without independently verified authorized evidence. Source account claims are not audited profitability.

## S2 — observed trade/position semantics and behavioral analysis

Owners B+C. Match entry/exit only when explicitly disclosed or deterministically reconcilable from verified events. Partial closes, net/hedge mode, transfers, funding/fees, average entry and liquidations require explicit policies; ambiguity stays UNKNOWN. Cohort metrics separate realized/unrealized, gross/net, missing costs, survivorship, selection and disclosure lag. Market context aligns to information actually available at decision time, not later publication. No fabricated historical L2/L3.

## S3 — reconstruction and simulation

Owner F with C; G plans only. Reconstruction_hypotheses have hypothesis IDs, evidence/counterexamples, confidence and train/test cutoffs; never an observed execution flag. User previews/approves a hypothesis before an ambiguous simulation. Reuse F's deterministic engine, costs/gap/liquidity uncertainty and artifact versioning. No auto-copy or real order execution.

## S4 — local API, charts and Codex analysis

E owns bounded authenticated /api/trader-sources, /api/traders, /api/traders/{id}/events, /api/trader-analysis and /api/trader-overlays. Shared typed schemas, source scope/cursors/range/revision and revocation controls; no direct SQL/shell. D renders disclosed facts and hypotheses with different shapes/labels, source evidence tooltip, disclosure lag and UNKNOWN fields; overlay counts/IDs must match recorded events/artifacts. G uses tester-only typed plans/jobs, user approval where required and digest-backed results, never a foreign Gateway.

## Ownership and review matrix

| Owner | Deliverable | Must not do |
|---|---|---|
| A | Source registry, permitted adapters, lossless evidence and timing | Attribute anonymous prints, scrape protected profiles, bypass access |
| B | Canonical PostgreSQL migrations, scoped IDs, consent/revocation/provenance/revisions | Invent run identity or duplicate authoritative ledgers |
| C | Behavioral metrics, observed position reconciliation, market alignment | Convert unknowns/inferences into facts or use future disclosures |
| D | Trader explorer, observed/hypothesis overlays and reasons | Infer entries/SLTP or compute substitute C/F math |
| E | Owned local API/auth/provider/cursor/schema/service boundary | Query foreign repos/services, lose scope/metadata, trade |
| F | Hypothesis DSL, deterministic offline simulation and uncertainty | Auto-run ambiguous prose or label modeled fills as observed |
| G | tester-only Codex typed read/research orchestration | Foreign module registry/Gateway, prompt SQL/shell/hotpatch |
| MASTER | Versioned contracts, exact pin matrix, independent checks and gate decisions | Claim READY without source/P0/CI evidence |

## Acceptance fixtures for every stage

Anonymous print with attempted named trader; missing cost/SLTP; delayed/revised disclosure; partial close/net/hedge ambiguity; conflict/duplicate and retry; consent import/revoke; unavailable/no public source; pagination cap/filter gaps; future-publication cutoff; ten instruments and bounded memory/disk; exact event→overlay mapping; denied unsafe Codex actions; no foreign runtime origin. Synthetic fixtures and real samples are separate. Each stage uses its own branch/Draft PR; main merge, physical deployment and real trading require separate authorization.
