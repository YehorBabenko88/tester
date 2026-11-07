# Standalone architecture audit — Issue #25

Normative user boundary read from current GitHub Issue25: tester has no runtime dependency/integration with Unified, Grid, Strattester, Math, MetaAnalyse, Terminan or their Gateway. Only tester's own data/analytics/backtest/terminal/control-plane components. Audited ONLY YehorBabenko88/tester sources and controlled local tests. No other repository, installed external service or prohibited endpoint was inspected or contacted.

## Exact published snapshots

| Owner | Branch HEAD inspected |
|---|---|
| A | addbdda232bc1a604a7a637bdad479752c4cccfe |
| B | 47fd36f32389174377682a13862f5b36c7a886da |
| C | 152823e1528f0953f8175717212d795d7fe1ac0c |
| D | 1fdf8183284a1203be14a6896cec175f6b2aea63 |
| E | 6d8b1b97433fd850a8900ad8a3750b682319dc3a |
| F | 998c5bc89d1b35c9ad3047b1e50dcb1295120e31 |
| G | 5edb722fc936a56e81a174cfe654839de9a19a9d |

Read complete GitHub recursive trees for A-G and fetched57 runtime/dependency files across A/B/C/D/F for prohibited-name/gateway/external-module-registry scanning. Inspected E's current immutable runtime/scripts from prior exact6d8 checkout, and all G control runtime modules plus tests/API/architecture/integration docs. Baseline duplicated files were included where relevant; tests/fixtures excluded from A-F runtime-name scan. H3837f1a2b95343f6ad6d24a88ba89a7a438e78f3 was visible in branch inventory but its runtime was not part of this assigned A-G audit. No cross-repository compatibility inference is made.

## Main finding: no demonstrated prohibited runtime dependency

**There is no concrete external Gateway/offending registry import in the inspected published sources.** Do not report the internal class name GatewayClient as proof of a Unified/Grid integration.

- G src/control/bridge.py:1 explicitly describes an optional MCP stdio client of **E's authenticated loopback API**. GatewayClient at :20 is defined locally, not imported from another product. Constructor :22-27 allows only explicit http127.0.0.1/::1 origins with ports and rejects userinfo/paths/query/fragment. ProxyHandler({}) and NoRedirect prevent proxy/redirect escape. _call :31 constructs only `/api/control/` requests; public methods :50-65 use a fixed tester route set. No Unified/Grid/other-product endpoint, installation path, Python package or registry is referenced.
- G src/control/api.py defines tester's transport-independent `/api/control` dispatcher; it does not bind another application or import a shared Gateway. `LAB_CONTROL_URL`/`LAB_CONTROL_TOKEN` in bridge.py:115 are host-supplied tester connection settings, not automatic discovery of another repo. No gateway_client.py or external-module registry file exists in the complete G tree.
- G dependencies are Python stdlib/local src.control modules; optional requirements-mcp.txt pins ordinary mcp SDK and pydantic-settings. Those are libraries, not prohibited app integrations.
- G docs/control/ARCHITECTURE.md:4,15,21 uses “gateway” for tester's own E-owned local HTTP/auth/service boundary. policy.py:1 “Trusted gateway identities” is naming only. “External credential store” (policy.py:26/API.md:6) describes host-provisioned secrets; TokenAuth itself is local in-memory registration and hashing, with no external secret-service client. These terms should be clarified to prevent architecture confusion, but they are not evidence of forbidden runtime linkage.
- A/B/C/D/F runtime/dependency scan found no prohibited product names, gateway references or external-module registry imports. E has local src.api imports and generic trusted operator factory loading via importlib (providers.py:3,26); no configured prohibited package, path or endpoint appears in this snapshot. Generic factory extension is not proof of current dependency, but deployment configuration must remain tester-local.

`src/integration/storage_api` is explicitly permitted internal B→E integration under the user's boundary. Its path/name must not be removed merely because it contains “integration”; this audit did not reinterpret tester-internal component wiring as an external gateway.

## Deny-contract / execution boundary

Executed G actual commands/contracts/plane suites from isolated work/g-standalone-audit using root dependency runtime, workspace TEMP/TMP:

```
../venv/Scripts/python.exe -m pytest tests/control/test_commands.py tests/control/test_contracts.py tests/control/test_plane.py -q --timeout=10 --timeout-method=thread --basetemp=../pytest-g-standalone-fixed
```

**34 tests passed,16 subtests passed**. Initial attempt using sandbox default temp failed temp-directory permissions; rerun with workspace TEMP/TMP passed. No product failures inferred from that environmental attempt.

Verified coverage includes shell.exec denial, unknown top-level fields, SQL/instructions in action params, actor/scope spoofing, no self-approval, no arbitrary development prompt/main-branch change, stale approval/config revision races, simulation gate absence/partial coverage rejection, pinned simulation evidence mismatch, and cancelled/expired lease fencing.

G contracts.py:17-35 explicitly allowlists typed actions/params. Market plans pin snapshot/category/symbol/UTC range; simulation requires trusted strategy preview and complete evidence, then human approval. dev.change.request accepts only base_revision, codex/* branch and tester issue reference; validate_result:137 onward requires isolated-worktree-draft-pr mode, matching base/branch, head revision, PR number and tests artifact reference. Tests reject live-patch output. No agent-supplied shell/SQL/code execution endpoint is implemented. G queues manifests/provider requests; it does not itself execute code or create a PR.

Bounded desired resource config preview/apply and history downloads are also supported, with authenticated human approval for config apply; these are tester-local typed control operations, not arbitrary service/shell access. MCP exposes eight typed tools and no approval/provisioning method by source inspection. Optional MCP SDK is absent in root venv, so stdio/catalog/HTTP integration tests were not rerun; no live service was probed.

## Remaining architecture/integration decisions for MASTER

1. Rename/document GatewayClient as TesterControlClient and replace ambiguous “gateway/external store” wording with tester-local control API/OS-protected tester credentials. This is clarity work, not removal of an actual prohibited dependency.
2. E must mount G only inside tester's loopback service and register a closed tester-owned provider table. G docs/API.md explicitly assigns fixed provider dispatch, domain-object ACLs, revocation recheck and cancellation to E; G alone does not prove that wiring. Avoid routing to any previously existing cross-product Gateway. Operator factory paths should be checked against the standalone deployment manifest, not imported from arbitrary sibling workspaces.
3. G plane.py:9,30-69 implements authoritative SQLite plans/jobs/approvals/config/audit, not only a staging cache. Documentation calls this control metadata while PG holds market data. This is **not an external repo dependency**, but MASTER should explicitly resolve whether new own-PostgreSQL/SQLite-only-staging policy also covers durable control authority. If yes, assign migration to tester's own PostgreSQL rather than introducing another product's registry/store. Current tests certify SQLite semantics only.
4. Fixed request/result schemas do not attest actual dev-provider branch isolation, real PR state, artifact bytes or source provenance. E's bounded tester provider must enforce them; do not claim real code execution/SCM integration acceptance from manifest-validation tests.
5. Source/activity ingestion under Issue25 must use tester's own A/B/C/F/H typed sources and provenance, with anonymous prints never transformed into named traders. This audit did not test H's newly assigned domain logic and does not certify that future feature.

No current runtime code was identified for deletion as a prohibited cross-repo bridge. Stronger standalone deny-import/path/endpoint tests can encode the normative deployment boundary without treating ordinary Python math modules or tester's own HTTP adapter as forbidden products.
