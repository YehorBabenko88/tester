"""Fail-closed pip-audit transport retry; at most two complete audit attempts.

No vulnerability exit or handled/ambiguous error is retried. Only a terminal
requests transport exception in a pip_audit traceback, without a result on
stdout, admits one retry. Caller-provided audit flags are preserved verbatim.
"""

import re
import subprocess
import sys
import time

ATTEMPT_TIMEOUT_SECONDS = 180


def transport_failure(stdout, stderr):
    lines = stderr.rstrip().splitlines()
    return bool(
        not stdout.strip()
        and lines
        and not re.search(r"Found [1-9][0-9]* known vulnerabilit", stderr, re.IGNORECASE)
        and "Traceback (most recent call last):" in stderr
        and re.search(r"""File ["'][^"']*[\\/]pip_audit[\\/]""", stderr)
        and re.fullmatch(
            r"requests\.exceptions\.(?:ConnectionError|Timeout|ConnectTimeout|ReadTimeout):(?: .*)?",
            lines[-1],
        )
    )


def redacted(output):
    if output is None:
        return ""
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")

    def authority(match):
        scheme, value = match.groups()
        return scheme + ("[REDACTED]@" + value.rsplit("@", 1)[1] if "@" in value else value)

    output = re.sub(r"""(https?://)([^\s/?#'"<>]+)""", authority, output, flags=re.IGNORECASE)
    return re.sub(
        r"""([?&](?:token|access_token|api_key|key|password|secret)=)[^&\s'"<>]+""",
        r"\1[REDACTED]",
        output,
        flags=re.IGNORECASE,
    )


def run_audit(args):
    command = [sys.executable, "-m", "pip_audit", *args]
    for attempt in range(2):
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=ATTEMPT_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            print(redacted(exc.stdout), end="", file=sys.stdout, flush=True)
            print(redacted(exc.stderr), end="", file=sys.stderr, flush=True)
            print("pip-audit process deadline exceeded; no retry", file=sys.stderr)
            return 124
        except OSError as exc:
            print("pip-audit launch failed: " + type(exc).__name__, file=sys.stderr)
            return 127
        print(redacted(result.stdout), end="", file=sys.stdout, flush=True)
        print(redacted(result.stderr), end="", file=sys.stderr, flush=True)
        if result.returncode == 0:
            return 0
        if attempt or result.returncode != 1 or not transport_failure(result.stdout, result.stderr):
            return result.returncode
        print(
            "Retrying pip-audit once after a confirmed requests transport failure",
            file=sys.stderr,
            flush=True,
        )
        time.sleep(1)
    raise AssertionError("unreachable audit retry state")


def main():
    return run_audit(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
