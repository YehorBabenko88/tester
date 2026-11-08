# Frontend CI runner compatibility repair

PR #39 evidence head `68e59b5965c1d6bc055bc4700b3a80d8c50c9bc6` failed during `npx playwright install --with-deps chromium`: Playwright 1.56.1 rejected `ubuntu26.04-x64`. The 33 Node tests had passed; the browser tests did not run. The original RED evidence remains valid.

Change only the frontend job runner from `ubuntu-latest` to `ubuntu-24.04`. Keep the lockfile, Playwright 1.56.1, Node 22, dependency installation and both test commands unchanged. No tests are skipped or disabled. This pins the OS release family; GitHub continues updating the hosted image within that release.

Version-specific primary evidence checked on 2026-11-08:

- [Playwright v1.56.1 host detection](https://github.com/microsoft/playwright/blob/v1.56.1/packages/playwright-core/src/server/utils/hostPlatform.ts#L72-L84) explicitly treats Ubuntu 24.04 as supported and Ubuntu versions 26 or later as unsupported.
- [GitHub's Ubuntu 24.04 image](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md) documents the available hosted runner and the November 2026 migration of `ubuntu-latest` to 26.04.

Local validation: assert the workflow differs from its prior version only in the runner label, confirm the installed Playwright CLI is 1.56.1, and run `git diff --check`. This Windows workstation cannot establish Linux browser dependency installation success. Root must publish the composed candidate and obtain an actual successful frontend CI run for that exact source SHA; no green CI claim is made by this repair. Other CI failures and release gates remain independent. No installed application, database, UAC or native runtime changes are included.
