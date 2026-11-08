"""Published-origin guard, without backend substitutes or installed access."""

import socket
from types import SimpleNamespace

import pytest

from src.integration.research_runtime import _connection_belongs, require_published_origin


def test_refuses_unpinned_source_folder_and_wrong_commit(tmp_path):
    with pytest.raises(ValueError):
        require_published_origin(tmp_path / "checkout", "a" * 40)
    with pytest.raises(ValueError):
        require_published_origin(tmp_path / ("b" * 40), "a" * 40)
    with pytest.raises(ValueError):
        require_published_origin(tmp_path / ("a" * 40), "main")
    assert require_published_origin(tmp_path / ("a" * 40), "a" * 40) == tmp_path / ("a" * 40)


def test_only_owned_accepted_tcp_four_tuple_can_authorize_token_delivery():
    client = ("127.0.0.1", 52839)
    row = dict(
        type=socket.SOCK_STREAM, status="ESTABLISHED", laddr=("127.0.0.1", 18767), raddr=client
    )
    assert _connection_belongs(SimpleNamespace(**row), client)
    for changes in (
        dict(status="NONE"),
        dict(status="PRIVATE_CAPABILITY"),
        dict(type=None),
        dict(raddr=()),
        dict(raddr=("127.0.0.1", 52840)),
        dict(type=socket.SOCK_DGRAM),
        dict(laddr=("0.0.0.0", 18767)),
    ):
        assert not _connection_belongs(SimpleNamespace(**dict(row, **changes)), client)


class ObserverClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, delay):
        self.sleeps.append(delay)
        self.now += delay


def accepted_row(**change):
    values = dict(
        type=socket.SOCK_STREAM,
        status="ESTABLISHED",
        laddr=("127.0.0.1", 18767),
        raddr=("127.0.0.1", 52839),
    )
    return SimpleNamespace(**dict(values, **change))


def test_exact_accepted_tuple_after_old_100ms_budget_is_admitted(monkeypatch):
    from src.integration import research_runtime as runtime

    clock = ObserverClock()
    monkeypatch.setattr(runtime.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(runtime.time, "sleep", clock.sleep)
    births = []
    process = SimpleNamespace(
        net_connections=lambda **kwargs: [] if clock.now < 0.15 else [accepted_row()]
    )
    result = runtime._wait_for_owned_connection(
        process, ("127.0.0.1", 52839), lambda: births.append(clock.now)
    )
    assert clock.now >= 0.15 and result["polls"] >= 4
    assert all(0 < delay <= 0.05 for delay in clock.sleeps)
    assert len(births) >= 2 * result["polls"]


@pytest.mark.parametrize(
    "changes",
    [
        dict(status="NONE"),
        dict(status="LISTEN"),
        dict(type=socket.SOCK_DGRAM),
        dict(type="SECRET_CAPABILITY"),
        dict(raddr=("127.0.0.1", 52840)),
        dict(laddr=("0.0.0.0", 18767)),
    ],
)
def test_observer_never_relaxes_tuple_state_or_type_and_stops(monkeypatch, changes):
    from src.integration import research_runtime as runtime

    clock = ObserverClock()
    monkeypatch.setattr(runtime.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(runtime.time, "sleep", clock.sleep)
    process = SimpleNamespace(net_connections=lambda **kwargs: [accepted_row(**changes)])
    with pytest.raises(runtime.ApiOwnershipError) as caught:
        runtime._wait_for_owned_connection(process, ("127.0.0.1", 52839), lambda: None)
    assert caught.value.reason_code == "api_connection_deadline"
    assert 2 <= clock.now <= 2.001
    assert "SECRET_CAPABILITY" not in str(caught.value.public())


def test_birth_change_after_matching_snapshot_is_refused(monkeypatch):
    from src.integration import research_runtime as runtime

    calls = []

    def birth():
        calls.append(1)
        if len(calls) == 2:
            raise runtime.ApiOwnershipError("api_process_birth_changed")

    process = SimpleNamespace(net_connections=lambda **kwargs: [accepted_row()])
    with pytest.raises(runtime.ApiOwnershipError, match="api_process_birth_changed"):
        runtime._wait_for_owned_connection(process, ("127.0.0.1", 52839), birth)


def test_birth_checks_use_fresh_pid_and_reject_wrong_pid(monkeypatch):
    import psutil

    from src.integration import research_runtime as runtime

    queried = []

    def fresh(pid):
        queried.append(pid)
        return SimpleNamespace(pid=pid, create_time=lambda: 1 if len(queried) == 1 else 2)

    monkeypatch.setattr(psutil, "Process", fresh)
    runtime._assert_api_birth(42, 1)
    with pytest.raises(runtime.ApiOwnershipError):
        runtime._assert_api_birth(42, 1)
    monkeypatch.setattr(
        psutil, "Process", lambda pid: SimpleNamespace(pid=99, create_time=lambda: 1)
    )
    with pytest.raises(runtime.ApiOwnershipError):
        runtime._assert_api_birth(42, 1)


def test_verify_does_not_construct_http_or_g_client_before_ownership(monkeypatch):
    import http.client

    from src.integration import research_runtime as runtime

    monkeypatch.setattr(
        runtime, "context", lambda *args: (None, None, None, {"capability": "PRIVATE"}, None)
    )

    def refused(*args):
        raise runtime.ApiOwnershipError("api_connection_deadline")

    def forbidden(*args, **kwargs):
        raise AssertionError("HTTP/token boundary reached before OS ownership")

    monkeypatch.setattr(runtime, "assert_api_process", refused)
    monkeypatch.setattr(runtime, "ResearchGatewayClient", forbidden)
    monkeypatch.setattr(http.client, "HTTPConnection", forbidden)
    with pytest.raises(runtime.ApiOwnershipError):
        runtime.verify(None, None, None)


@pytest.mark.parametrize("owned", [True, False])
def test_cli_reveals_only_owned_sanitized_observer_diagnostics(
    monkeypatch, capsys, tmp_path, owned
):
    import json
    import sys

    from src.integration import research_runtime as runtime

    error = (
        runtime.ApiOwnershipError(
            "PRIVATE_CAPABILITY",
            observer=dict(
                polls=10**9,
                rows_seen=-1,
                samples=[
                    dict(
                        status="PRIVATE_CAPABILITY",
                        type_kind="PRIVATE_CAPABILITY",
                        type_code="PRIVATE_CAPABILITY",
                        count=10**9,
                        secret="PRIVATE_CAPABILITY",
                    )
                ]
                * 20,
            ),
        )
        if owned
        else RuntimeError("PRIVATE_CAPABILITY")
    )

    def refused(*args):
        raise error

    monkeypatch.setattr(runtime, "verify", refused)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "research_runtime",
            "verify",
            "--root",
            str(tmp_path),
            "--lab-source-sha",
            "b" * 40,
            "--application-source-sha",
            "a" * 40,
        ],
    )
    with pytest.raises(SystemExit):
        runtime.main()
    report = json.loads(capsys.readouterr().out)
    assert "PRIVATE_CAPABILITY" not in str(report)
    if owned:
        assert report["reason_code"] == "api_observer_data_invalid"
        assert report["observer"]["polls"] == 4096 and len(report["observer"]["samples"]) == 8
    else:
        assert set(report) == {"status", "error_type"}


def owned_fixture(tmp_path, monkeypatch, *, late=False, birth_after_close=False):
    import json

    import psutil

    from src.integration import research_runtime as runtime

    root = tmp_path / "lab"
    root.mkdir()
    source = tmp_path / "source"
    image = tmp_path / "python.exe"
    (root / ".research-api.json").write_text(
        json.dumps(
            dict(
                schema="tester-research-api/1",
                root=str(root),
                application_source_sha="a" * 40,
                lab_source_sha="b" * 40,
                pid=42,
                created=1,
                image=str(image),
            )
        )
    )
    state = dict(active=False, births=[], queries=0)
    clock = ObserverClock()

    class Probe:
        def __enter__(self):
            state["active"] = True
            return self

        def __exit__(self, *args):
            state["active"] = False

        def getsockname(self):
            return ("127.0.0.1", 52839)

    class Process:
        pid = 42

        def create_time(self):
            state["births"].append(state["active"])
            return (
                2 if birth_after_close and not state["active"] and len(state["births"]) > 1 else 1
            )

        def exe(self):
            return str(image)

        def cwd(self):
            return str(source)

        def cmdline(self):
            return [
                "python",
                "-m",
                "src.integration.research_runtime",
                "serve",
                "--root",
                str(root),
                "--lab-source-sha",
                "b" * 40,
                "--application-source-sha",
                "a" * 40,
            ]

        def net_connections(self, **kwargs):
            assert state["active"], "same probe must remain open throughout polling"
            state["queries"] += 1
            return [] if late and clock.now < 0.15 else [accepted_row()]

    monkeypatch.setattr(psutil, "Process", lambda pid: Process())
    monkeypatch.setattr(runtime.socket, "create_connection", lambda *args, **kwargs: Probe())
    monkeypatch.setattr(runtime.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(runtime.time, "sleep", clock.sleep)
    return runtime, root, source, state


def test_probe_stays_open_and_birth_is_checked_after_close(tmp_path, monkeypatch):
    runtime, root, source, state = owned_fixture(tmp_path, monkeypatch, late=True)
    result = runtime.assert_api_process(root, "a" * 40, source)
    assert result["observer"]["polls"] >= 4
    assert not state["active"] and state["births"][-1] is False
    assert all(state["births"][index] for index in range(1, len(state["births"]) - 1))


def test_birth_change_after_probe_close_refuses(tmp_path, monkeypatch):
    runtime, root, source, state = owned_fixture(tmp_path, monkeypatch, birth_after_close=True)
    with pytest.raises(runtime.ApiOwnershipError) as caught:
        runtime.assert_api_process(root, "a" * 40, source)
    assert caught.value.reason_code == "api_process_birth_changed"
    assert caught.value.observer["polls"] == 1


def test_research_guard_refuses_python_c_before_apparent_module(tmp_path, monkeypatch):
    import psutil

    runtime, root, source, state = owned_fixture(tmp_path, monkeypatch)
    process = psutil.Process(42)
    original = process.cmdline()
    monkeypatch.setattr(
        type(process), "cmdline", lambda self: [original[0], "-c", "pass", *original[1:]]
    )
    with pytest.raises(runtime.ApiOwnershipError) as caught:
        runtime.assert_api_process(root, "a" * 40, source)
    assert caught.value.reason_code == "api_process_arguments_mismatch"
    assert state["queries"] == 0
