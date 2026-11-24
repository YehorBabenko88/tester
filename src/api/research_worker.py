"""One bounded isolated research projection job in a separate worker process."""

import argparse
import os
from uuid import UUID

from .research_control import Identity
from .research_ledger import create_persistent_control


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id", type=UUID)
    parser.add_argument("--actor", required=True)
    args = parser.parse_args()
    dsn = os.environ.get("TEST_RESEARCH_DATABASE_URL")
    if not dsn:
        raise SystemExit("explicit isolated TEST_RESEARCH_DATABASE_URL required")
    control = create_persistent_control(dsn)
    result = control.dispatch(Identity(args.actor), str(args.job_id))
    if result["state"] != "complete":
        raise SystemExit("research job not complete")
    print(result["artifact_digest"])


if __name__ == "__main__":
    main()
