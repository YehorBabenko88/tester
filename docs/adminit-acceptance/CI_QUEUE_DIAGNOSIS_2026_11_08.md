# Tester CI queue diagnosis

Observed: 2026-11-08T15:42:37.949528+00:00

Read-only GitHub API inspection; no reruns or settings changes.

## Direct observations

- queued_runs: total_count=134, returned=100.
- in_progress_runs: total_count=0, returned=0.
- PR30 head: `24751fb768e9e940df8be7526eb854199f048064`.
- Main observed: `0a6ce227d9463562bb18889dcc0ee747f3be9130`.
- Token-visible repository permissions: `{"admin": true, "maintain": true, "push": true, "triage": true, "pull": true}`.

- [Storage integrity #42485030595](https://github.com/YehorBabenko88/tester/actions/runs/42485030595): queued, conclusion=None, created=2026-11-08T13:27:14Z.
  - postgres (18, 3.11): status=queued, runner_name='', labels=["ubuntu-latest"], started_at=2026-11-08T13:27:14Z, steps=0.
  - postgres (18, 3.13): status=queued, runner_name='', labels=["ubuntu-latest"], started_at=2026-11-08T13:27:15Z, steps=0.
  - postgres (14, 3.11): status=queued, runner_name='', labels=["ubuntu-latest"], started_at=2026-11-08T13:27:15Z, steps=0.
  - postgres (14, 3.13): status=queued, runner_name='', labels=["ubuntu-latest"], started_at=2026-11-08T13:27:15Z, steps=0.
  - Pending deployments: [].
- [Frontend contract and fixture browser tests #42485030591](https://github.com/YehorBabenko88/tester/actions/runs/42485030591): queued, conclusion=None, created=2026-11-08T13:27:14Z.
  - fixture-browser: status=queued, runner_name='', labels=["ubuntu-latest"], started_at=2026-11-08T13:27:14Z, steps=0.
  - Pending deployments: [].
- [Python static and unit tests #42485030589](https://github.com/YehorBabenko88/tester/actions/runs/42485030589): queued, conclusion=None, created=2026-11-08T13:27:14Z.
  - test (ubuntu-latest): status=queued, runner_name='', labels=["ubuntu-latest"], started_at=2026-11-08T13:27:15Z, steps=0.
  - test (windows-latest): status=queued, runner_name='', labels=["windows-latest"], started_at=2026-11-08T13:27:15Z, steps=0.
  - postgres-acceptance: status=queued, runner_name='', labels=["ubuntu-latest"], started_at=2026-11-08T13:27:15Z, steps=0.
  - Pending deployments: [].
- [Platform acceptance #42485030582](https://github.com/YehorBabenko88/tester/actions/runs/42485030582): queued, conclusion=None, created=2026-11-08T13:27:14Z.
  - linux (3.11): status=queued, runner_name='', labels=["ubuntu-latest"], started_at=2026-11-08T13:27:15Z, steps=0.
  - linux (3.14): status=queued, runner_name='', labels=["ubuntu-latest"], started_at=2026-11-08T13:27:15Z, steps=0.
  - windows: status=queued, runner_name='', labels=["windows-latest"], started_at=2026-11-08T13:27:15Z, steps=0.
  - Pending deployments: [].
- [Python static and unit tests #42485026341](https://github.com/YehorBabenko88/tester/actions/runs/42485026341): queued, conclusion=None, created=2026-11-08T13:27:11Z.
  - postgres-acceptance: status=queued, runner_name='', labels=["ubuntu-latest"], started_at=2026-11-08T13:27:11Z, steps=0.
  - test (windows-latest): status=queued, runner_name='', labels=["windows-latest"], started_at=2026-11-08T13:27:11Z, steps=0.
  - test (ubuntu-latest): status=queued, runner_name='', labels=["ubuntu-latest"], started_at=2026-11-08T13:27:11Z, steps=0.
  - Pending deployments: [].

## Inference and limits

- A queued status with no assigned runner/started steps shows that these jobs have not executed; it does not identify the underlying GitHub scheduler/account cause.
- Repository action enablement, permission settings, requested runner labels and concurrency YAML are inspectable. They do not by themselves prove billing availability, organization limits or hosted-runner capacity.
- Empty pending_deployments means no deployment-environment approval is exposed for that run; it does not rule out all other approval/account gates.
- Billing/spending limits and organization/account concurrency were not inferred or changed; account billing endpoints were not queried.
- No mass reruns, workflow dispatch, runner changes or settings mutations were performed.
