# Recovery harness redirector correction

Installed application remains immutable7b59b415760de83061cd8b91dc75f2f0e5c3878d (deployb4438d10495d65e684f9e40a7a29d68e114c772efd4df3edf969d1cb65d4823a; native0f441d54ba7604a668cd9ce0b65883521f9ba8ee2512cde22379637ef46e569a). Its newUI/API/counters and13public timeframes passed bounded physical checks. 30-minute low-rate soak is in progress.

First worker harness terminated0processes and failed worker selection. A normal-user development venv sleeper reproduced Windows redirector behavior: actualimage is basePython, while argv0 stays the venv executable and may have trailing whitespace. The correction independently bounds both image and argv0 to the two exact tester-owned private interpreters and retains exact module/ancestry/creation checks; foreign paths and extraargs still refused.

A tool-only external manifest binds the newly published recovery script to the unchanged installed application/native/deployment hashes. It does not hotfix application files. A future elevated execution still requires a NEW explicit single-UAC approval because previous approvals were already used. It will run only after the nonadmin soak finishes.

Four guard tests pass on Python3.12/3.14. Independent static review found no remaining P1/P2. Actual crash/recovery/replay still NOT_PROVEN at this commit; no real trading or PG5432 modifications.
