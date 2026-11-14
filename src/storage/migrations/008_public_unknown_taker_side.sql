-- DATA public execution taker side v1: preserve source uncertainty.
-- PostgreSQL 001 initial raw_records_check2 and 002 trade_side_required
-- both allowed Buy/Sell only, while DATA normalization admits Unknown.
-- These are trade-side domain checks ONLY; price/size/identity constraints stay intact.
ALTER TABLE tester_storage.raw_records
  DROP CONSTRAINT raw_records_check2;
ALTER TABLE tester_storage.raw_records
  ADD CONSTRAINT raw_records_check2 CHECK (
    dataset <> 'trades' OR (
      trade_id IS NOT NULL AND length(trade_id) > 0
      AND id_origin IS NOT NULL
      AND price IS NOT NULL AND size IS NOT NULL
      AND price > 0 AND size > 0
      AND payload->>'side' IN ('Buy','Sell','Unknown')
    )
  );
ALTER TABLE tester_storage.raw_records
  DROP CONSTRAINT trade_side_required;
ALTER TABLE tester_storage.raw_records
  ADD CONSTRAINT trade_side_required CHECK (
    dataset <> 'trades' OR (
      payload->>'side' IS NOT NULL
      AND payload->>'side' IN ('Buy','Sell','Unknown')
    )
  );
