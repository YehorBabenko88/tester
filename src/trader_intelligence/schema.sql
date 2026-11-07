CREATE SCHEMA IF NOT EXISTS tester_trader;
CREATE TABLE IF NOT EXISTS tester_trader.trader_sources (
 tenant text NOT NULL, source text NOT NULL, revoked boolean NOT NULL DEFAULT false,
 PRIMARY KEY(tenant,source));
CREATE TABLE IF NOT EXISTS tester_trader.snapshots (
 tenant text NOT NULL, dataset_revision text NOT NULL, source text NOT NULL,
 source_revision text NOT NULL, payload jsonb NOT NULL,
 PRIMARY KEY(tenant,dataset_revision), UNIQUE(tenant,source,source_revision),
 FOREIGN KEY(tenant,source) REFERENCES tester_trader.trader_sources);
CREATE TABLE IF NOT EXISTS tester_trader.analysis_runs (
 tenant text NOT NULL, request_id text NOT NULL, request_digest text NOT NULL,
 dataset_revision text NOT NULL, payload jsonb NOT NULL,
 PRIMARY KEY(tenant,request_id),
 FOREIGN KEY(tenant,dataset_revision) REFERENCES tester_trader.snapshots);
CREATE TABLE IF NOT EXISTS tester_trader.chart_acks (
 tenant text NOT NULL, request_id text NOT NULL, artifact_digest text NOT NULL,
 renderer_revision text NOT NULL, PRIMARY KEY(tenant,request_id,renderer_revision),
 FOREIGN KEY(tenant,request_id) REFERENCES tester_trader.analysis_runs);
CREATE TABLE IF NOT EXISTS tester_trader.schema_version (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton), checksum text NOT NULL);
