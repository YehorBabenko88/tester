"""Bounded diagnosis of loopback/socketpair failures before ASGI tests run."""

import subprocess
import sys


def main():
    probe = (
        "import socket,asyncio; a,b=socket.socketpair();a.close();b.close();"
        "loop=asyncio.new_event_loop();loop.close()"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", probe], timeout=5, capture_output=True, check=False
        )
    except subprocess.TimeoutExpired:
        raise SystemExit(
            "Runtime preflight FAILED: stdlib socketpair/eventloop timed out before ASGI. "
            "Use an environment permitting loopback; HTTP tests are NOT verified."
        ) from None
    if result.returncode:
        raise SystemExit("Runtime preflight FAILED before ASGI; check Python loopback capability")
    print("PASS: bounded stdlib socketpair/eventloop preflight")


if __name__ == "__main__":
    main()
