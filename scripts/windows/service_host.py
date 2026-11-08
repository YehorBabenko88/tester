"""Native SCM dispatcher entrypoint; never invoked by dry-run generation.

Run only from SCM in an approved disposable VM. No install/start/repair CLI exists.
"""

import sys
from pathlib import Path


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import servicemanager

    from src.api.windows_service import TesterService

    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(TesterService)
    servicemanager.StartServiceCtrlDispatcher()


if __name__ == "__main__":
    main()
