CREATE TABLE tester_storage.instruments (
  venue text NOT NULL, category text NOT NULL, symbol text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(venue,category,symbol)
);
CREATE TABLE tester_storage.instrument_metadata (
  venue text NOT NULL, category text NOT NULL, symbol text NOT NULL,
  revision text NOT NULL, effective_ms bigint NOT NULL CHECK(effective_ms>=0),
  metadata jsonb NOT NULL, provenance jsonb NOT NULL,
  PRIMARY KEY(venue,category,symbol,revision),
  FOREIGN KEY(venue,category,symbol) REFERENCES tester_storage.instruments
);
CREATE TABLE tester_storage.raw_records (
  record_key text PRIMARY KEY CHECK(length(record_key)=64),
  venue text NOT NULL, category text NOT NULL, symbol text NOT NULL,
  dataset text NOT NULL CHECK(dataset IN ('trades','candles','mark','index','premium','open_interest','funding','ratio')),
  interval text NOT NULL, schema_version text NOT NULL CHECK(schema_version='1.0'),
  timestamp_ms bigint NOT NULL CHECK(timestamp_ms>=0),
  timestamp_ns bigint, timestamp_us bigint,
  timestamp_precision text NOT NULL CHECK(timestamp_precision IN ('ms','us','ns')),
  trade_id text, id_origin text CHECK(id_origin IN ('exchange','composite_v1')),
  payload jsonb NOT NULL, content_hash text NOT NULL CHECK(length(content_hash)=64),
  price numeric GENERATED ALWAYS AS ((payload->>'price')::numeric) STORED,
  size numeric GENERATED ALWAYS AS ((payload->>'size')::numeric) STORED,
  open numeric GENERATED ALWAYS AS ((payload->>'open')::numeric) STORED,
  high numeric GENERATED ALWAYS AS ((payload->>'high')::numeric) STORED,
  low numeric GENERATED ALWAYS AS ((payload->>'low')::numeric) STORED,
  close numeric GENERATED ALWAYS AS ((payload->>'close')::numeric) STORED,
  volume numeric GENERATED ALWAYS AS ((payload->>'volume')::numeric) STORED,
  turnover numeric GENERATED ALWAYS AS ((payload->>'turnover')::numeric) STORED,
  value numeric GENERATED ALWAYS AS ((payload->>'value')::numeric) STORED,
  inserted_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY(venue,category,symbol) REFERENCES tester_storage.instruments,
  CHECK((timestamp_precision='ns' AND timestamp_ns IS NOT NULL AND timestamp_ns>=0 AND timestamp_ns/1000000=timestamp_ms)
     OR (timestamp_precision<>'ns' AND timestamp_ns IS NULL)),
  CHECK((timestamp_precision='us' AND timestamp_us IS NOT NULL AND timestamp_us>=0 AND timestamp_us/1000=timestamp_ms)
     OR (timestamp_precision<>'us' AND timestamp_us IS NULL)),
  CHECK(dataset<>'trades' OR (trade_id IS NOT NULL AND length(trade_id)>0 AND id_origin IS NOT NULL
       AND price IS NOT NULL AND size IS NOT NULL AND price>0 AND size>0 AND payload->>'side' IN ('Buy','Sell'))),
  CHECK(dataset NOT IN ('candles','mark','index','premium') OR (interval<>'' AND open IS NOT NULL AND high IS NOT NULL
       AND low IS NOT NULL AND close IS NOT NULL AND high>=greatest(open,close,low) AND low<=least(open,close,high)
       AND (dataset='premium' OR least(open,high,low,close)>=0))),
  CHECK(dataset NOT IN ('open_interest','funding','ratio') OR (value IS NOT NULL AND (dataset='funding' OR value>=0))),
  CHECK(dataset<>'ratio' OR value<=1), CHECK(volume>=0), CHECK(turnover>=0),
  CHECK(COALESCE(price::text,'') NOT IN ('NaN','Infinity','-Infinity')),
  CHECK(COALESCE(size::text,'') NOT IN ('NaN','Infinity','-Infinity')),
  CHECK(COALESCE(open::text,'') NOT IN ('NaN','Infinity','-Infinity')),
  CHECK(COALESCE(high::text,'') NOT IN ('NaN','Infinity','-Infinity')),
  CHECK(COALESCE(low::text,'') NOT IN ('NaN','Infinity','-Infinity')),
  CHECK(COALESCE(close::text,'') NOT IN ('NaN','Infinity','-Infinity')),
  CHECK(COALESCE(volume::text,'') NOT IN ('NaN','Infinity','-Infinity')),
  CHECK(COALESCE(turnover::text,'') NOT IN ('NaN','Infinity','-Infinity')),
  CHECK(COALESCE(value::text,'') NOT IN ('NaN','Infinity','-Infinity'))
);
CREATE UNIQUE INDEX raw_trade_identity ON tester_storage.raw_records(venue,category,symbol,trade_id) WHERE dataset='trades';
CREATE UNIQUE INDEX raw_candle_identity ON tester_storage.raw_records(venue,category,symbol,dataset,interval,timestamp_ms) WHERE dataset<>'trades';
CREATE INDEX raw_time ON tester_storage.raw_records(venue,category,symbol,dataset,interval,timestamp_ms,record_key);
CREATE INDEX raw_time_brin ON tester_storage.raw_records USING brin(timestamp_ms);
CREATE VIEW tester_storage.raw_trades AS SELECT * FROM tester_storage.raw_records WHERE dataset='trades';
CREATE VIEW tester_storage.raw_ohlcv AS SELECT * FROM tester_storage.raw_records WHERE dataset IN ('candles','mark','index','premium');
CREATE VIEW tester_storage.open_interest AS SELECT * FROM tester_storage.raw_records WHERE dataset='open_interest';
CREATE VIEW tester_storage.funding AS SELECT * FROM tester_storage.raw_records WHERE dataset='funding';
CREATE VIEW tester_storage.price_series AS SELECT * FROM tester_storage.raw_records WHERE dataset IN ('mark','index','premium');
CREATE TABLE tester_storage.record_sources (
  source_id text PRIMARY KEY, record_key text NOT NULL REFERENCES tester_storage.raw_records,
  symbol text NOT NULL, source jsonb NOT NULL, raw_envelope jsonb NOT NULL, recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE tester_storage.source_archives (
  archive_id text PRIMARY KEY, scope_key text NOT NULL, symbol text NOT NULL,
  start_ms bigint NOT NULL CHECK(start_ms>=0), end_ms bigint NOT NULL CHECK(end_ms>start_ms),
  url text NOT NULL, sha256 text NOT NULL CHECK(length(sha256)=64), bytes bigint NOT NULL CHECK(bytes>0),
  row_count bigint NOT NULL CHECK(row_count>=0), format text NOT NULL CHECK(format IN ('csv.gz','parquet','jsonl','binary')),
  storage_uri text NOT NULL, manifest jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX archives_range ON tester_storage.source_archives(scope_key,start_ms,end_ms);
CREATE TABLE tester_storage.download_jobs (
  job_key text PRIMARY KEY, scope_key text NOT NULL, symbol text NOT NULL, request jsonb NOT NULL,
  state text NOT NULL CHECK(state IN ('running','committed','retry')),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE tester_storage.ingestion_checkpoints (
  job_key text PRIMARY KEY REFERENCES tester_storage.download_jobs, checkpoint jsonb NOT NULL,
  commit_hash text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE tester_storage.pagination_evidence (
  evidence_id text PRIMARY KEY, job_key text NOT NULL REFERENCES tester_storage.download_jobs,
  scope_key text NOT NULL, symbol text NOT NULL, start_ms bigint NOT NULL CHECK(start_ms>=0),
  end_ms bigint NOT NULL CHECK(end_ms>start_ms),
  status text NOT NULL CHECK(status IN ('EXHAUSTED','EMPTY','PARTIAL','FAILED','UNVERIFIED')),
  record_count bigint NOT NULL CHECK(record_count>=0), evidence jsonb NOT NULL,
  validator_version text NOT NULL, verified boolean NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  CHECK(NOT verified OR (status='EXHAUSTED' AND record_count>0))
);
CREATE TABLE tester_storage.coverage_intervals (
  coverage_id text PRIMARY KEY, scope_key text NOT NULL, symbol text NOT NULL,
  start_ms bigint NOT NULL CHECK(start_ms>=0), end_ms bigint NOT NULL CHECK(end_ms>start_ms),
  state text NOT NULL CHECK(state IN ('available','requested','downloaded','verified','unknown','corrupt','unavailable')),
  evidence_id text REFERENCES tester_storage.pagination_evidence, reason text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(), CHECK(state NOT IN ('available','verified') OR evidence_id IS NOT NULL)
);
CREATE INDEX coverage_scope_range ON tester_storage.coverage_intervals(scope_key,start_ms,end_ms);
CREATE TABLE tester_storage.coverage_resolutions (
  coverage_id text PRIMARY KEY REFERENCES tester_storage.coverage_intervals,
  evidence_id text NOT NULL REFERENCES tester_storage.pagination_evidence,
  resolved_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE tester_storage.quality_findings (
  finding_id text PRIMARY KEY, scope_key text NOT NULL, symbol text NOT NULL,
  start_ms bigint NOT NULL, end_ms bigint NOT NULL CHECK(end_ms>start_ms),
  code text NOT NULL, detail jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE tester_storage.finding_resolutions (
  finding_id text PRIMARY KEY REFERENCES tester_storage.quality_findings,
  evidence_id text NOT NULL REFERENCES tester_storage.pagination_evidence, resolved_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE tester_storage.derived_aggregates (
  aggregate_id text PRIMARY KEY, scope_key text NOT NULL, symbol text NOT NULL,
  start_ms bigint NOT NULL, end_ms bigint NOT NULL CHECK(end_ms>start_ms),
  algorithm_version text NOT NULL, source_fingerprint text NOT NULL, payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE tester_storage.audit_events (
  event_id text PRIMARY KEY, scope_key text NOT NULL, symbol text NOT NULL,
  action text NOT NULL, detail jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE FUNCTION tester_storage.reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'historical storage records are append-only'; END $$;
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['instruments','instrument_metadata','raw_records','record_sources','source_archives',
   'pagination_evidence','coverage_intervals','coverage_resolutions','quality_findings','finding_resolutions',
   'derived_aggregates','audit_events'] LOOP
    EXECUTE format('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON tester_storage.%I FOR EACH ROW EXECUTE FUNCTION tester_storage.reject_mutation()',t);
  END LOOP;
END $$;
