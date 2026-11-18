import pytest

from src.api.history_readiness import validate_history_dsn

CATALOG = (
    "host=127.0.0.1 hostaddr=127.0.0.1 port=55473 "
    "dbname=tester_preview_catalog user=tester_preview_app password=secret"
)
APP = (
    "host=127.0.0.1 hostaddr=127.0.0.1 port=55473 "
    "dbname=tester_preview_history user=tester_preview_history_app password=secret"
)


def test_accepts_isolated_nonprivileged_app_db():
    assert validate_history_dsn(APP, CATALOG) == APP


@pytest.mark.parametrize(
    "replacement",
    [
        "port=5432",
        "port=55432",
        "host=10.0.0.1",
        "hostaddr=10.0.0.1",
        "dbname=tester_preview_catalog",
        "dbname=postgres",
        "user=postgres",
        "user=tester_preview_app",
    ],
)
def test_rejects_shared_or_unsafe_application_database(replacement):
    key = replacement.split("=", 1)[0]
    tokens = [token for token in APP.split() if not token.startswith(key + "=")]
    tokens.append(replacement)
    with pytest.raises(ValueError):
        validate_history_dsn(" ".join(tokens), CATALOG)


def test_rejects_missing_password():
    with pytest.raises(ValueError):
        validate_history_dsn(APP.replace("password=secret", ""), CATALOG)
