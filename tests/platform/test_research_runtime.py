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
        dict(raddr=()),
        dict(raddr=("127.0.0.1", 52840)),
        dict(type=socket.SOCK_DGRAM),
        dict(laddr=("0.0.0.0", 18767)),
    ):
        assert not _connection_belongs(SimpleNamespace(**dict(row, **changes)), client)
