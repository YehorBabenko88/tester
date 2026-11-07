"""Explicit shared queue drain/resume, with no database/schema creation."""

import argparse

from .config import load_settings
from .jobs import JobQueue


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["drain", "resume"])
    args = parser.parse_args()
    settings = load_settings()
    if not settings.dsn:
        raise SystemExit("Application DSN required")
    JobQueue(settings.dsn).set_draining(args.action == "drain")


if __name__ == "__main__":
    main()
