# Agent L: read-only overlay presentation proposal v1

Issue #34. Baseline `0b5339c357769134f32f2c2081ee9868aeee3bad`.

Status: **NOT_INTEGRATED**. `frontend/overlays.js` is a pure fixture-only presentation proposal, not an F/G adapter, API validator, authorization decision or chart marker renderer. The application does not import it or request a trader endpoint. The new Trader disclosures and hypotheses workspace reports DATA_UNAVAILABLE with source permission UNKNOWN.

`overlay-presentation-fixture/1` accepts SYNTHETIC_FIXTURE only. Its required scope binds run, snapshot, revision, market, interval, requested half-open UTC range and knowledge cutoff. Source category, availability, permission test value, evidence reference and revision remain separate from coverage. Unknown, denied or revoked permissions and anonymous market sources suppress the entire page. A fixture's GRANTED value is never independent evidence of actual consent or rights.

Events retain exact decimal strings, event IDs, nullable trade/fill/order relationships, evidence, classification, knowledge timestamp, time precision and uncertainty window. DISCLOSED source events and INFERRED hypotheses use different text; hypotheses require a hypothesis ID. Missing fees remain UNKNOWN. The presenter does not invent trade IDs or infer identity from anonymous prints. It never computes entries, SL/TP, profitability or execution facts.

The pure helper admits at most 1000 events and rejects duplicate IDs, out-of-range events, future-known information, scope mismatches and malformed timing/decimals. This proposal has no pagination support and does not establish full artifact correspondence or certify source evidence. A future production adapter must validate authoritative persisted IDs, hashes, permissions/revocation and source scope, freeze the approved producer contract, and account for all accepted events across scoped pages. F/G timing and trade-ID incompatibilities remain unresolved; F/G/H remain NOT_INTEGRATED. No simulation output was fabricated or real snapshot accepted.

Existing chart geometry and automated headless native-gesture fixtures cover 13 display intervals, wheel zoom, drag, keyboard shift/month selection and DPR2 exact-decimal crosshair tables. Those are synthetic automated browser evidence, not physical mouse/keyboard evidence or installed ADMINIT acceptance. No live API, installed service, PostgreSQL or trading action was used.

Verification on this branch: `npm test` (26 unit tests) and `npm run test:ui` (44 automated headless browser fixtures). The new browser test mounts only the workspace module and checks the gate language and absence of API requests. Overlay chart geometry remains a later, contract-gated deliverable; this change does not claim it is implemented.
