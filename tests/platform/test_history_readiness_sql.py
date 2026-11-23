"""History readiness probes are read-only, with identifier-safe composition."""

from unittest.mock import MagicMock, patch

from psycopg import sql

from src.api.history_readiness import check_history_schema


def test_history_schema_uses_quoted_identifiers_without_sql_concatenation():
    connection = MagicMock()
    connection.__enter__.return_value = connection
    with (
        patch(
            "src.api.history_readiness.validate_history_dsn", return_value="isolated"
        ) as validate,
        patch("src.api.history_readiness.connect", return_value=connection) as connect,
    ):
        result = check_history_schema("app", "catalog")
    validate.assert_called_once_with("app", "catalog")
    connect.assert_called_once_with("isolated")
    expected = (
        'SELECT 1 FROM "tester_platform"."jobs" LIMIT 0',
        'SELECT 1 FROM "tester_platform"."workers" LIMIT 0',
        'SELECT 1 FROM "tester_storage"."schema_migrations" LIMIT 0',
        'SELECT 1 FROM "tester_lab"."projections" LIMIT 0',
    )
    queries = [call.args[0] for call in connection.execute.call_args_list]
    assert len(queries) == 4
    assert all(isinstance(query, sql.Composed) for query in queries)
    assert tuple(query.as_string() for query in queries) == expected
    assert result == {
        "app_database": "SCHEMA_READABLE",
        "worker": "NOT_VERIFIED",
        "historical_coverage": "UNKNOWN",
    }
