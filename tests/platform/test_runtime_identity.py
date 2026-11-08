"""Offline argv spoof regression: Python options cannot masquerade as -m."""

import pytest

from src.integration.runtime_identity import require_runtime_argv


def arguments():
    return [
        "python",
        "-m",
        "src.integration.research_runtime",
        "serve",
        "--root",
        "owned-root",
        "--lab-source-sha",
        "a" * 40,
        "--application-source-sha",
        "b" * 40,
    ]


def test_only_exact_invocation_or_single_leading_no_bytecode_option_is_admitted():
    args = arguments()
    require_runtime_argv(args, "owned-root", "a" * 40, "b" * 40)
    require_runtime_argv([args[0], "-B", *args[1:]], "owned-root", "a" * 40, "b" * 40)


@pytest.mark.parametrize(
    "change",
    [
        lambda a: [a[0], "-c", "pass", *a[1:]],
        lambda a: [a[0], "-I", *a[1:]],
        lambda a: [a[0], "-S", *a[1:]],
        lambda a: [a[0], "-B", "-B", *a[1:]],
        lambda a: [*a, "--root", "owned-root"],
        lambda a: [*a, "--extra"],
        lambda a: [*a[:3], "verify", *a[4:]],
        lambda a: [*a[:3], *a[4:]],
        lambda a: [*a[:4], *a[6:8], *a[4:6], *a[8:]],
        lambda a: [*a[:-1], "c" * 40],
        lambda a: [a[0], "-m"],
        lambda a: [],
    ],
)
def test_interpreter_commands_switches_duplicates_extras_and_wrong_action_refuse(change):
    with pytest.raises(ValueError):
        require_runtime_argv(change(arguments()), "owned-root", "a" * 40, "b" * 40)


def test_arbitrary_expected_module_cannot_relax_guard():
    with pytest.raises(ValueError):
        require_runtime_argv(arguments(), "owned-root", "a" * 40, "b" * 40, module="unrelated")
