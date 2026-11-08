"""Top-level private SCM PythonClass shim, staged verbatim from the published release."""

import sys

# This executes before importing even the release's src/__init__.py.
sys.dont_write_bytecode = True

from src.api.lab_service import LabService  # noqa: E402,F401
