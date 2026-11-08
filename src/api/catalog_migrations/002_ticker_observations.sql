CREATE TABLE tester_catalog.ticker_observations (
    category text NOT NULL CHECK (category IN ('linear','inverse')),
    symbol text NOT NULL CHECK (length(symbol) BETWEEN 1 AND 100),
    source_time_ms bigint NOT NULL CHECK (source_time_ms > 0),
    metrics jsonb NOT NULL CHECK (jsonb_typeof(metrics)='object' AND octet_length(metrics::text) <= 16384),
    PRIMARY KEY (category,symbol,source_time_ms)
);
CREATE INDEX ticker_observations_retention
    ON tester_catalog.ticker_observations(category,source_time_ms DESC,symbol);
