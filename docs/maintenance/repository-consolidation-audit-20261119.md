# TESTER repository consolidation audit — 2026-11-19

Status: audit / NO-GO for destructive cleanup. Target: one protected `main` as the sole stable release source. This document does not declare existing draft PRs release-ready.

## Verified GitHub PR snapshot

| PR | Role | Head | Draft | Latest observed blockers |
|---|---|---|---|---|
| #83 | Legacy Preview | 21dafb499674d659f7ac2d0a4c2dcab05c3c503f | yes | Python, Platform, Frontend failure |
| #89 | Guardian MASTER | fafd1e95d34099b38607201e396bde55696e82c8 | yes | Platform failure; other checks queued |
| #90 | Code | ba1fbe6557e1d34185c27bdf7300c7e12682b0fa | yes | Python failure; not mergeable |
| #91 | Data | 6ea56275c7cdf3317101cba1453346693d89136c | yes | checks incomplete; not mergeable |
| #92 | Storage | a7243a153362d5339a9a54af734e35461949a3db | yes | Python and Platform failures; not mergeable |
| #93 | Exchange | bdc2f9942bdc477cb397f80b59041462a9021dfe | yes | Python and Platform failures; not mergeable |

This is a point-in-time observation; re-check exact HEAD and all required CI before making decisions. A green individual job is not an overall release gate.

## Required process

1. Freeze and document all branch heads, open PRs, protected tags, deployment pins, and installer references. Inventory scripts/windows/ and scripts/windows_preview/ generations, docs/evidence and CI fixture references. Never classify a file obsolete merely by its name or age.
2. Select canonical `C:\\TesterLab` Windows installer owned by MASTER. Distinguish it from Preview v3.5 (LocalAppData/ProgramData, ports 18769/55473). Verify two desktop shortcuts, full TESTER-only uninstall without backup, dedicated PostgreSQL isolation from system 5432, API/worker/terminal, and GUARDIANS_ENABLED=false.
3. Repair each Guardian PR against a pinned foundation; resolve merge conflicts and require exact-head CI including Windows, Python/Ruff, PostgreSQL isolation, negative deletion tests, and fault injection. Independent physical ADMINIT acceptance required.
4. Merge small, independently verified changes in a controlled dependency order; do not merge failed or incomplete PRs merely to reduce branch count. Record merge SHA and release provenance.
5. After a release candidate passes CI and physical acceptance, tag it, update README with one supported installer and migration/uninstall instructions, mark historical installers as deprecated, and remove legacy files only in a separate reviewable PR after dependency search.
6. Delete remote merged branches only after explicit proof they are fully merged into main, not used by open PRs, deployments, release tags, CI workflows, or external consumers. Preserve unmerged work via a reviewed archive/tag before deletion. Never force-push or rewrite main history.
7. Protect main: required checks, review, no direct pushes/force pushes; keep short-lived feature branches during active work. 'One main' means one canonical release branch, not zero development branches.

## Immediate NO-GO

Do not delete branches/files, merge PR #83 or #89–#93, or deploy Guardian-enabled build while CI is failing/unfinished. Do not touch ADMINIT system PostgreSQL 5432. Maintain the current pinned local checkout 0fec16eb19a2d42104af950b66d75805650fff65 until an approved release SHA is selected.

## Owners

MASTER #88 owns installer integration and dependency inventory; Guardian #84–#87 own their respective module tests. Repository consolidation should be a separate change set to avoid conflicts with active Guardian PRs.
