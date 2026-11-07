# Physical SCM code-only plan

Scope: Issue27, MASTER sole physical operator. Preserve d28f492 and old service.
1. Test manifest scope guards and exact-name ownership; observe RED, implement physical_scope.
2. Test mocked SCM install/update/start/stop: no foreign mutation; implement physical_scm.
3. Test lifecycle/failure-budget/readiness with fake clock/process; implement service runtime and native dispatcher.
4. Verify Python lint/unit/full isolated acceptance; independent static review; publish separate Draft.

Fixed root C:/ProgramData/TesterPhysicalTest20261107; service TesterPhysicalTest20261107;
optional PG dependency TesterPhysicalPg20261107, API18765/PG55469. No physical mutations,
ACL/firewall/PG/global Python changes. No data/version deletion. MASTER supplies protected
immutable checksummed release, private venv, config/runtime, approved manifest hash.
