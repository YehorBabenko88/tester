-- Preserve genuine anonymous public executions with unknown taker direction.
-- DATA v1 normalizes unknown side as "Unknown"; two historical PostgreSQL CHECKs
-- unintentionally rejected that exact legitimate value. This migration only
-- broadens permitted side ENUM values, never removes price/size/id guards.
--
-- Ownership: RESEARCH Draft PR #71; DATA #72 / MASTER must review before
-- any production or combined candidate migration. Do not apply to PC4.

ALTER TABLE tester_storage.raw_records
  DROP CONSTRAINT raw_records_check2;

ALTER TABLE tester_storage.raw_records
  ADD CONSTRAINT raw_records_check2 CHECK (
    dataset <> 'trades'
    OR (
      trade_id IS NOT NULL
      AND length(trade_id) > 0
      AND id_origin IS NOT NULL
      AND price IS NOT NULL
      AND size IS NOT NULL
      AND price > 0
      AND size > 0
      AND payload->>'side' IN ('Buy', 'Sell', 'Unknown')
    )
  );

ALTER TABLE tester_storage.raw_records
  DROP CONSTRAINT trade_side_required;

ALTER TABLE tester_storage.raw_records
  ADD CONSTRAINT trade_side_required CHECK (
    dataset <> 'trades'
    OR (
      payload->>'side' IS NOT NULL
      AND payload->>'side' IN ('Buy', 'Sell', 'Unknown')
    )
  );
