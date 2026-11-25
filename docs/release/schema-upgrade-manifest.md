# Schema upgrade manifest — for owner approval

**Status: DRAFT FOR OWNER APPROVAL. Not approved. Do not run `-SchemaUpgrade` with it until the owner records approval in issue #88.**

This manifest covers upgrading an existing lab installation (`C:\TesterLab`) whose source is the accepted baseline `d6059af84474329ec8cf02d686416ef8a54c208a` to the integration candidate. The exact target source SHA is filled in only when the final candidate SHA is accepted. The SHA-256 values below are the file bytes on branch `integration/audit-fixes-20261125` at `8f6a85e264173e0527042af4e74f12dcfefd3c8c`. If round-3 work adds or changes a migration, this manifest is regenerated and must be re-approved.

Published migrations are untouched. Platform `001`–`007` and storage `001`–`007` are byte-identical to `d6059af`. The updater refuses any edited or removed migration (`Assert-UpgradeMigrationScope`).

## Value for the upgrade plan

`Update-TesterLabRelease.ps1 -SchemaUpgrade` compares this list with the files that the published payload adds. The comparison is exact and sorted.

```json
{
  "schema_upgrade": {
    "new_migrations": [
      "src/api/migrations/008_restart_window.sql",
      "src/api/migrations/009_runtime_least_privilege.sql",
      "src/storage/migrations/008_public_unknown_taker_side.sql"
    ]
  }
}
```

## Files, hashes and order

`scripts/lab_schema_upgrade.py lab-upgrade` applies the migrations. It connects as owner `tester_lab_admin` on 127.0.0.1:55470 and never uses port 5432. Everything runs in **one transaction**, in this order:

| Step | File | SHA-256 | Introduced by |
|---|---|---|---|
| 1 | `src/storage/migrations/008_public_unknown_taker_side.sql` | `80b8a69e615689be09389644fb2d153e37e2d6de4910c768d0e78b85264a4746` | `3c71b57` (PR #102) |
| 2 | `src/api/migrations/008_restart_window.sql` | `0d5cd411ec013f43a73d047fbdcb6c6696c1d62c2a3d739a540d3664e4e4c586` | PR #116, PR #122 |
| 3 | `src/api/migrations/009_runtime_least_privilege.sql` | `e4aef72101547c7f79c3b6b93ef9dc2479738f902de4bd9b99ffc41b252883d3` | PR #118 |
| 4 | `restrict_lab_app` (code, not a file): revoke DELETE, TRUNCATE, REFERENCES and TRIGGER from `tester_lab_app`; storage UPDATE only on `download_jobs` and `ingestion_checkpoints` | — | PR #122 |

Each migration's checksum is recorded in `tester_storage.schema_migrations` or `tester_platform.migrations`.

**A database that already applied an intermediate version of platform 008** (any branch head before `7139d94`) fails with "checksum mismatch". This is fail-closed. Such a database must be rebuilt; it is never patched.

## What each migration does, and whether the old app still works

| Migration | Effect | Old app (`d6059af`) on the new schema | Lock / duration |
|---|---|---|---|
| storage 008 | Widens two CHECK constraints so trades may have taker side `Unknown`. No data is rewritten. | **Compatible.** The old code never writes `Unknown`. It does not run storage migrations at start: only `seed` (fresh DB only) and the installers do. | ACCESS EXCLUSIVE on `tester_storage.raw_records`. It validates every row. **The upgrade must be offline.** |
| platform 008 | Sliding-window crash-restart limits (host 3/h, api 6/h, worker 6/h), clean-stop tokens and owner-only `restart_window_status`/`reset_restart_window`. Replaces `reserve_restart`/`restart_counts` with the same signatures. | **Compatible.** The legacy roles `host`, `api` and `worker` are accepted and counted in the window. Lab installations have no `restart_budgets` row, so there is no effect on labs. | Short. Function and column DDL only. |
| platform 009 | Removes DELETE/TRUNCATE (and UPDATE where not needed) from enrolled **release** runtime roles. | **Compatible.** At `d6059af` the only runtime DELETEs are on `tester_catalog`, whose grants are unchanged. Labs are covered by step 4 instead. | Short. |
| step 4 (`restrict_lab_app`) | Lab App role loses DELETE/TRUNCATE. | **Compatible**, for the same reason as 009. Verified after commit. | Short. |

## Safety checks built into the run

- Pre-flight refuses:
  - a non-loopback host, port 5432, or a non-owner role;
  - an app state that differs from the approved `expected_app_state`; the updater itself stops the app before the schema stage;
  - an edited or removed migration;
  - a mismatch between this list and the payload.
- A `pg_dump -Fc` backup is created and its SHA-256 recorded. It is restored into an **owner-only** scratch database (CONNECT/TEMP revoked from PUBLIC). The live state inside the migration transaction must equal that restored snapshot, otherwise the run refuses. The scratch database is then dropped and its digest recorded.
- Row counts and the SHA-256 of every pre-existing table and column are compared before commit, and again after commit.
- Any failure rolls the whole transaction back. The schema stays at the `d6059af` state and the old app is restarted.

## Rollback procedure

Migrations are forward-only. They are never reverted in place.

1. **Failure before commit:** nothing to do. The transaction was rolled back and the outcome reports `FAILED_TRANSACTION_ROLLED_BACK`.
2. **Failure after commit** (a later updater stage fails):
   - The old app is restarted on the upgraded schema (`OLD_APP_RESTORED_ON_UPGRADED_SCHEMA`). This is supported because every step above is old-app compatible.
   - To return to the pre-upgrade data:
     1. Run `python -I -B scripts/lab_schema_upgrade.py restore` with the recorded backup SHA-256. It restores into a **new**, owner-only database and never overwrites one.
     2. Review that database.
     3. Grant CONNECT to the App role explicitly.
     4. Repoint the installation through the owner procedure.
3. Keep the backup archive (`schema-backup` in the update bundle) until the upgraded installation has been accepted.

## Evidence and limits

- CI evidence:
  - `lab-schema-upgrade.yml` runs on PostgreSQL 14 and 18 with Python 3.12 and 3.14.
  - It starts from the exact `d6059af` migration set, with every dataset kind seeded.
  - It injects faults at every stage and covers:
    - data tampering;
    - writes between backup and migration;
    - the non-owner refusal;
    - lab privilege restriction;
    - the owner-only scratch database.
- **UNVERIFIED:** nothing has run against a real `C:\TesterLab`. The PowerShell orchestration is covered only by parsing, static tests and `-FunctionsOnly` unit tests.

## Owner approval record

To approve, post in issue #88:

- the exact target source SHA;
- the three file names with their SHA-256 values as listed above;
- the approved initial app state (`expected_app_state`: `Running` or `Stopped`, as observed on the host);
- the PostgreSQL system identifier of the lab cluster.

Agents do not approve this manifest.
