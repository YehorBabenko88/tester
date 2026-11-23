"""Private SCM bootstrap for canonical releases."""

import sys

sys.dont_write_bytecode = True
from src.api.release_service import ReleaseService  # noqa: E402,F401
