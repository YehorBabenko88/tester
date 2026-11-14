"""CI-only disposable API stand-in; no ports, secrets or service installation."""
import json
import os
from pathlib import Path
import sys
import time


def main() -> int:
    stop=Path(os.environ['OPS_STOP_PATH'])
    output=Path(os.environ['OPS_CHECKPOINT_PATH'])
    if stop.parent != output.parent:
        return 91
    until=time.monotonic()+15
    while not stop.exists():
        if time.monotonic()>until:
            return 92
        time.sleep(0.02)
    with output.open('x',encoding='utf-8') as stream:
        json.dump({'generation':int(os.environ['OPS_GENERATION']),
                   'owner_marker':os.environ['OPS_OWNER_MARKER'],
                   'inflight':'api-drained'},stream)
        stream.flush()
        os.fsync(stream.fileno())
    return 0


if __name__=='__main__':
    sys.exit(main())
