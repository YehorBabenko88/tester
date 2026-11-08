# Compact historical read candidate

Observed published7b legacy1000-row read repeated one283805-byte evidence document1000times (~284MB PostgreSQL/client transport) for a1.27MB public response. HTTP timing was4.797s; isolated cProfile process peaked at1272455168bytes working set and1259065344bytes private commit. This is actual diagnostic evidence, not a newcandidate performance claim.

The new opt-in compact provenance projection sends typedSQLNULL for repeated raw_envelope/evidence bodies while retaining source/run/observation/evidence linkage, original declarations, unlinked/legacy refusal, statuses and validator metadata. Only LabReader and its fenced committed-record reread optin. Default Store.read, Store.provenance and full resolver behavior retain full documents. Coverage remains UNKNOWN; no proof promotion. Public canonical row values and provenance shapes unchanged.

TDD21focused integrationtests pass; independent source review has no P1/P2. Fullgate430PASS/85SKIP/57subtests. New source must be published and installed as a new immutable SHA before actualafterbenchmark; existing7b baseline30minute soak and its separately approved recoverytest will finish first. Actualafterperformance and latest-source physical UI remain unverified at this commit.
