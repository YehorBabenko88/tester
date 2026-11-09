# Independent PC4 published evidence audit

Reviewed Draft64 exact e158ec03d24ef4aeb07b6eb1af66f0d0e55587ed, docs/physical-test/pc4-20261109, from separate detached work/pc4-agents/review-evidence-e158. Artifact/source-only: no app, browser, PostgreSQL, service, native action or network beyond GitHub fetch. This review validates published artifact consistency, not live host state or independent replay of root's physical operations.

## P2: current checkpoint retains resolved work and predates included results

CHECKPOINT.json:90 still calls the original SPOT/linear mismatch a pending integration blocker although the same checkpoint and candidate2 artifacts contain successful LINEAR execution. Lines85/296 still request the already-completed DPR2 harness/run and label D validation ACTIVE. confirmed_at line9 is18:14:36, but included DPR2 reports finish18:15:24 and18:16:05. Update the current checkpoint's as-of time, move resolved items to history, and keep the actual unresolved restart/analytics/native/real-admission gates.

REPORT.md:3 identifies ea4d9b2 as installed source without a baseline qualifier; its Next gates section and final paragraph still say research/HiDPI require future installation/testing despite the later successful6a section and current checkpoint. Label the opening sections explicitly historical baseline, then add a concise current6a/DPR2 status. The actual13/13 baseline applies to ea+418 at DPR1; only strategy1case and timeframe1m1case are DPR2 on6a+5169. No artifact supports13/13 DPR2, and the checkpoint correctly says NOT13DPR2.

## Evidence materialization boundary

candidate-2-manifest.json lists exactly905 files present in Git6a24e50 tree.188 SHA256 entries equal raw Git blob bytes;717 equal LF-to-CRLF Windows materialization; no mismatch remains beyond that conversion. An initial raw-Git-only comparison therefore fails717 entries, but this is explained checkout materialization, not observed code drift. Declare that the manifest/archive hash Windows-materialized source bytes rather than raw Git blobs, including the line-ending policy. The existing unchanged-installed-source claim is a comparison against this deployed materialization, not proof of byte equality with every canonical Git blob.

The private source ZIP and dump files themselves are not published here; their retained presence/archive/dump hashes cannot be independently rechecked from GitHub artifacts alone. No request to publish credentials/private backups is implied.

## Checks passed within artifact scope

- Browser gzip manifest raw/gzip byte counts and SHA256 hashes all match. Decompressed evidence contains13 cases; applicationea4d9b2 and harness418 are distinguished in baseline reports.
- Both candidate2 strategy reports reconstruct to exactly the declared canonical summary digestc8d5f9... from their published producer fields, fixed summary schema/engine and SYNTHETIC_ONLY/NOT_PROVEN certification. Three fills, one closed trade, exact funding0.0100 and original no-IID-confidence text are consistent with the published screenshot and PG receipt.
- DPR2 strategy records2x:3008x836 backing /1504x418 CSS. DPR2 timeframe is only interval1:3012x520 /1506x260 candles and1478x340 /739x170 volume. All10 recorded visible panel snapshots across initial/gesture/restoration data also satisfy2x dimensions. Both reports mark assertions_completed true and zero uncaught page_errors.
- Console errors are not zero: DPR2 timeframe88 and strategy13, including expected unsupported-panel/negative-auth HTTP errors. Claims should say zero uncaught page errors, not zero errors of every kind. REPORT/JSON generally preserve this boundary and do not count404/422/503 as successful analytics.
- Candidate2 source/restored raw/snapshot/artifacts/execution/ACK group row counts/hashes agree. The backup and PG proof are timestamped18:10, BEFORE the second DPR2 browser run18:15. CHECKPOINT.research_runs explicitly acknowledges that ordering. This is a bounded pre-second-run restore result, not a backup of every later job/ACK. The earlier145-row baseline backup is a separate database/source from candidate2's10 raw rows.
- Foreign-Origin403 and anonymous-summary401 receipt is explicitly loopback-only. Wrong-token401/unregistered403 browser negatives do not pretend to be REAL/UNKNOWN proof. Separate real10-row admission receipt records both rejected boundaries and unchanged research table counts. The wrong-category negative remains NOT_RUN in browser evidence.
- No live token/password/DSN field or credential-string pattern was found in the published JSON files, decompressed1.5MB browser JSON, Markdown and logs scanned. The authenticated summary screenshot was visually inspected: capability field empty; displayed input/artifact/run digests are identities, not bearer secrets. This is bounded inspection, not a general secret-scanning guarantee.

## Claim limits retained

EXPERIMENTAL_PORTABLE/NO_GO is appropriate. No evidence here establishes native SCM, hard OS resource caps, crash/reboot, same-root restart, full real historical completeness/admission, missing analytics or trader-source permission. Hosted CI receipts are as-of snapshots; this review did not turn queued CI into PASS or reverify current CI. RUNNING PID strings are root's timestamped observations, not live ownership attestation by this reviewer.

Recommendation: correct stale current-state prose/checkpoint and explicitly document source materialization. No credential leak or new unsupported broad PASS claim was identified in the checked artifacts. Preserve chronological source/candidate separation and backup cutoff when publishing the next checkpoint.
