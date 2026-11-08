# ADMINIT continuation and independent agents I-L

Native reader719 upgrade PASS; integration release NO_GO.

All four agents actually launched in separate managed worktrees. Capacity is root plus three active children, so launches were overlapping and staggered. I: Issue31 / DraftPR35 / performance; J: Issue32 / DraftPR37 / evidence and recovery preparation; K: Issue33 / DraftPR38 / control seam; L: Issue34 / DraftPR36 / UI presentation. All PRs target integration/adminit-acceptance. No agent changed installed ADMINIT.

The one approved reader upgrade installed published719b02eea4134730b5d688143022706a9cae8dbe. Own28-table backup/restore PASS; old runtime and metadata retained for rollback. Exact release/native inventory and 1000-row pre/post data+coverage hashes match. App Running under LocalService; ownPG55470 PID5132 and preexistingPG5432 PID11740 stayed unchanged. Current app instance8d7e04a83b07471d8282cdc02af51c46.

Ten-symbol probe used published tool88be6f6d4d18edc5dc06f46376602b1a512facfc for604.86s, with bounded600s admission-cycle deadline. 380/380 jobs completed,38 per symbol,0 new failures,0 pending. All1900 acknowledged row verifications passed (not1900 unique inserts). Historical failed job remained untouched.400 bounded GET observations (up to5 rows each) p50/p95=0.273/0.333s;380 POST observations p50/p95=1.471/2.056s. Max sampled running jobs1, max sampled queue21. This is higher-rate admissions, not certified concurrent processing or saturation.

Owned-cluster WAL increased41,112,735 bytes over593.08 sampled seconds (~69,321B/s). Dedicated database grew16,343,040B; runtime files grew59,560,653B. These are separate logical/WAL/file measures, not physical disk throughput. Full raw report and summary retain counter scope and timing.

Original resource sampling timed out. Published optimized collector9c4f65c2f1a03641019d25102c8a21e485cf08f8 obtained10 usable separate snapshots of11 attempts during a partial test window; one partial-counter query failed. App private working-set sampled maximum233,623,552B; PG14,499,840B. Mean sampled app CPU74.3% of one core, maximum337% (multicore aggregate), PGmean1%. Sampled max process writes app762,011B/s, PG115,134B/s. These are sampled process counters, not full-window peaks or physical disk bandwidth. Nonadmin Windows hides LocalService command lines/images: exact worker count UNKNOWN. The raw load result stays INCOMPLETE; later observations do not convert it into PASS.

After load all13 historical timeframes passed API OHLCV/provenance/closed-row checks. API1000-row latency after upgrade was1.822s. Isolated profiled reader peak72,265,728B versus prior legacy1,272,455,168B; individual observations with profiler overhead, not controlled service performance claims. Forty-three automated headless browser tests at0b5339c include wheel, drag, selection and crosshair checks; they are separate from blocked physical mouse acceptance.

J verified the saved authentic1440-candle grid while preserving original UNKNOWN/empty evidence_refs. Pinned F correctly rejects coverage evidence required. No completeness proof or strategy approval was invented. K/L candidate seams remain unmounted/fixture-only; canonical PostgreSQL durability, real permissions/provider bindings, F projection and actual D rendering remain NOT_INTEGRATED. Their timing errors found by independent review were fixed before acceptance claims.

New external recovery safety fixes preserve attempted/confirmed termination accounting across CloseHandle failure and revalidate owner/source/native/tool files under the held handle. Independent12 mocked helper tests passed. They do not establish physical crash recovery. No worker was killed; canceled TOOL509 UAC was not repeated. Any future action requires its own concrete reviewed tool manifest and explicit single-worker/UAC authorization. Missing durable receipt means NOT_PROVEN and never permits automatic retry.

CI count refresh:171queued,0in_progress; sampled hosted jobs had no assigned runner/steps. Scheduler/account/billing cause is UNPROVEN. No mass reruns/settings changes. Main remains0a6ce227d9463562bb18889dcc0ee747f3be9130. No real trades, PG5432 changes, other repositories or office PCs.

Required remaining gates: trustworthy market completeness/admission; canonical F/G/H production integration and actual simulated fills; physical mouse acceptance; worker crash/restart evidence under new authorization; complete resource/worker observations; actual CI execution. Current evidence supports native/API operation and bounded load observations only, not release GO.

Recovery tool proposal: source4f1476e4a1279f6da7ded75be417c4aa2058259b, script SHA2563f2667d2fa342aac12435e5c91167f2d4f9e238259c12425647db33c52757c11; proposal published at016aecd33f931a3767715cd0e140644b27631c2a, raw manifest SHA256e4305b30f4f252a6ab4b40c9c4119856ea8f3eac572d8a8b6472022186f2cd22. Coordinator verified staged raw bytes; NOT_AUTHORIZED_NOT_EXECUTED.
