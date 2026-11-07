ALTER TABLE tester_storage.raw_records ADD CONSTRAINT trades_have_no_interval
  CHECK(dataset<>'trades' OR interval='');
