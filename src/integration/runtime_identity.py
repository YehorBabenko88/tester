"""Exact Python invocation shape for the explicitly allowlisted owned runtime CLIs."""


def require_runtime_argv(
    args, root, lab_sha, application_sha, *, module="src.integration.research_runtime"
):
    if module not in {
        "src.integration.research_runtime",
        "src.integration.market_runtime",
        "src.integration.pc4_runtime",
    }:
        raise ValueError("Unrecognized owned runtime module")
    expected = [
        "-m",
        module,
        "serve",
        "--root",
        str(root),
        "--lab-source-sha",
        lab_sha,
        "--application-source-sha",
        application_sha,
    ]
    tail = args[1:] if isinstance(args, list) and args else None
    if tail and tail[0] == "-B":
        tail = tail[1:]
    if tail != expected:
        raise ValueError("Owned runtime invocation differs")
