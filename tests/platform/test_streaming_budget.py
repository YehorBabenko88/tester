import os

import pytest


@pytest.mark.integration
def test_ten_symbol_streaming_peak_and_queue_bound():
    from scripts.benchmark_queue import benchmark

    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Disposable DB required")
    result = benchmark(dsn)
    assert result["completed_jobs"] == 10
    assert result["peak_queue_depth"] <= 4
    assert 0 < result["peak_rss_bytes"] <= 512 * 1024**2
    assert result["network_bytes"] is None
    assert result["market_network_bytes"] == 0
    assert result["synthetic_fixture"] is True
