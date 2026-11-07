ALTER TABLE tester_storage.raw_records DROP CONSTRAINT raw_records_timestamp_precision_check;
ALTER TABLE tester_storage.raw_records ADD CONSTRAINT supported_timestamp_precision
  CHECK(timestamp_precision IN ('s','ms','us','ns'));
ALTER TABLE tester_storage.raw_records ADD CONSTRAINT whole_second_precision
  CHECK(timestamp_precision<>'s' OR mod(timestamp_ms,1000)=0);
ALTER TABLE tester_storage.raw_records ADD CONSTRAINT trade_side_required
  CHECK(dataset<>'trades' OR (payload->>'side' IS NOT NULL AND payload->>'side' IN ('Buy','Sell')));
