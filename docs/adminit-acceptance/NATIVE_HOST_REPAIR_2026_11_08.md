# Native host repair awaiting separate UAC approval

Application remains immutable source a3138eb9813832e9a839a99e54547001343ab3fd, deployment manifest 21b2854d02ac1d27563cd263e89504a13d4a6eb1724d3681588e63803eea506c.

Second approved UAC executed exactly once. Both owned LocalService services registered. Own PostgreSQL runs on55470; app stopped at FIRST_START1053. PostgreSQL5432 remains running, PID11740. No automatic elevated retry.

Published repair source aded56998dae16cf13e9c2707fc52012f12a1bfb adds missing locked servicemanager.pyd plus five plain private import paths; selects vendor-supported absolute PythonClass for only the owned app. Existing native/source bytes remain unchanged. Concrete manifest SHA256:39db695756ecc07b632981e70c01c3fa54b82427c7026b456950854e0a29859d.

Approved repair would verify all identities/hashes, backup and restore own database, stop only the two owned services, add two native dependency files, retain old manifest backup, deregister/reinstall those same services retaining all data, set owned PythonClass, and test start-stop-start with matching API service instance/source. It never elevates or retries itself.

Development gate:378 passed,84 skipped,57 subtests. Real cloned native loader/import and console1063 pass; this is not SCM acceptance. Independent read-only repair review has no remaining production P1/P2. Exact-source hosted CI is not yet successful; no release GO. F/G/H remain NOT_INTEGRATED.
