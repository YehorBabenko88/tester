# TESTER unified integration manifest — candidate snapshot 2026-11-20

**RELEASE NO_GO.** This manifest pins independent Draft PR heads; it does
not mean those commits have been merged, tested together or approved for
deployment.

| Owner | PR | Exact SHA | Required gate |
| --- | --- | --- | --- |
| MASTER foundation | #89 | `f235e51fba22172f232afdfb80e3deb78407df5e` | 8/8 exact-head workflow PASS, plus physical ADMINIT |
| Code | #90 | `c7392bb1913ac7c570bb1a285658a0b55f5b968a` | own exact-head tests, Windows recovery |
| Data | #91 | `26f3640cdc2d574b494c3f64c763802956b78960` | own exact-head tests, dedicated DB/data integrity |
| Storage | #92 | `5ff192fa54f005c18eb7d36160823c6c0e0d22af` | own exact-head tests, reparse and deletion scope |
| Exchange | #93 | `4df3835db4383ffe64caf2776022c862971745f5` | own exact-head tests, 403/TLS/rate limits |
| Consolidation audit | #94 | `16d794df1305c825b01852aff636d028a44c375e` | reference and consumer audit; no deletion |

## Foundation CI snapshot

At the time this document was prepared, MASTER isolated fault workflow
`44784085030` was **PASS**. Python `44784085324`, Platform
`44784085241`, PG `44784085337`, PC4 `44784085131`, research
`44784085110`, synthetic browser `44784085104`, contract diagnosis
`44784085166` were **QUEUED**. Do not transfer these statuses to a
newer HEAD or call QUEUED a PASS.

## Ownership and integration order

1. Freeze and independently verify a green exact-head MASTER foundation.
2. Resolve the versioned signed `tester_lab` runtime identity contract
   without weakening the historical `tester_preview` policy.
3. Refresh each Guardian branch non-force from the approved foundation
   while preserving its four owned files. Review the resulting three-way
   diff; stop on conflicts. Rerun all required exact-head CI.
4. Integrate only verified Code, Data, Storage and Exchange changes into
   a separate reviewed release-candidate branch. Pin its new 40-char HEAD
   and dependency digests, then rerun all combined Windows/Linux checks.
5. Independently validate the single `C:\TesterLab` installer, owned
   API/worker/dedicated PostgreSQL service identities, localhost terminal,
   boot/recovery, exact two Cyrillic desktop shortcuts, explicit
   `DELETE TESTER`, no retained backups, and full owned-only removal in
   a disposable Windows system. Confirm system PostgreSQL 5432 unchanged.
6. Obtain human acceptance before any merge to `main`, physical ADMINIT
   deployment or legacy file/branch deletion.

## Release blockers

- No complete 8/8 foundation CI PASS yet.
- Code's historical I001 and child-branch foundation/workflow skew need
  approved refresh; current branch-level mergeability is insufficient.
- The actual installer, service lifecycle and destructive uninstaller
  are not implemented/physically accepted; existing shortcut script is
  disposable CI fixture only.
- `src/ingest/transport.py` HTTP 403 retry and broad URL allowlist
  conflict with Exchange's halt/public-path policy.
- No independently authenticated signed runtime identity and post-action
  verifier. `GUARDIANS_ENABLED=false` remains mandatory.
- PR #94 is an audit, not authorization to remove legacy installers or
  branches.
