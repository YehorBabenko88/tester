"""Private SCM bootstrap for canonical releases."""

import sys

sys.dont_write_bytecode = True
from src.api.release_provision import ReleaseProvisionService  # noqa: E402,F401
from src.api.release_service import ReleasePostgresService, ReleaseService  # noqa: E402,F401
