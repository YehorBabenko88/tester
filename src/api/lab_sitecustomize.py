"""Private native service CPython startup hook; copied to runtime Lib/sitecustomize.py.

Python imports this before the release's PythonClass packages. Disabling bytecode
here keeps exact published source inventories unchanged under LocalService.
"""

import sys

sys.dont_write_bytecode = True
