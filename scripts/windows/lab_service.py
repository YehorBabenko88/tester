"""Published-release CLI for the fresh lab SCM proposal; never self-elevates."""

import argparse
import json
import sys
from pathlib import Path

# Executed by absolute path from an exact inventoried immutable release.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.api.lab_install import LabViolation, check, remove
from src.api.lab_scm import NativeSCM, apply


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=["install", "start", "stop", "restart", "status", "remove"]
    )
    parser.add_argument("--root", type=Path, default=Path(r"C:\TesterLab"))
    args = parser.parse_args()
    if args.root.absolute() != Path(r"C:\TesterLab"):
        raise LabViolation("Physical SCM proposal is bounded to C:\\TesterLab")
    marker = check(args.root)
    expected = args.root / "releases" / marker["active_sha"] / "scripts/windows/lab_service.py"
    if Path(__file__).resolve() != expected:
        raise LabViolation("Execute only the published inventoried active-release entrypoint")
    scm = NativeSCM(mutating=args.action == "install")
    try:
        result = apply(args.action, args.root, scm)
        if args.action == "remove":
            # apply verified exact native images before deregistration; absent services are accepted here.
            result["retention"] = remove(args.root, scm.inspect, scm.remove)
        print(json.dumps(result, indent=2))
    finally:
        scm.close()


if __name__ == "__main__":
    main()
