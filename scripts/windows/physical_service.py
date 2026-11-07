"""Fixed-scope entry. MASTER alone invokes commands after reviewing approved manifest."""

import sys
from pathlib import Path


def main():
    release = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(release))
    if sys.argv[1:] == ["--dispatch"]:
        from src.api.physical_host import dispatch

        dispatch()
    else:
        from src.api.physical_scm import main as command

        command()


if __name__ == "__main__":
    main()
