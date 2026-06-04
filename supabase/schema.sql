-- supabase/schema.sql
-- Supabase SQL Editor 에서 실행. IF NOT EXISTS 로 멱등성 보장.
-- 봇은 SUPABASE_DB_URL(서비스 권한)로 INSERT, 대시보드는 anon 역할로 SELECT만 허용.

-- ── 테이블 생성 ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS trades (
    id               bigint  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    timestamp        double precision NOT NULL,
    timestamp_kst    text    NOT NULL,
    symbol           text    NOT NULL,
    side             text    NOT NULL,
    action           text    NOT NULL,
    trigger          text    NOT NULL,
    strategy_name    text    NOT NULL,
    leverage         integer NOT NULL DEFAULT 1,
    mark_price       double precision NOT NULL,
    filled_price     double precision NOT NULL,
    quantity         double precision NOT NULL,
    notional         double precision NOT NULL,
    fee_usdt         double precision NOT NULL,
    realized_pnl     double precision NOT NULL DEFAULT 0.0,
    margin           double precision NOT NULL,
    is_liquidation   integer NOT NULL DEFAULT 0,
    balance_after    double precision NOT NULL,
    equity_after     double precision NOT NULL
);

CREATE TABLE IF NOT EXISTS equity_curve (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    timestamp      double precision NOT NULL,
    timestamp_kst  text    NOT NULL,
    total_equity   double precision NOT NULL,
    balance_usdt   double precision NOT NULL,
    used_margin    double precision NOT NULL,
    unrealized_pnl double precision NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    timestamp      double precision NOT NULL,
    timestamp_kst  text    NOT NULL,
    symbol         text    NOT NULL,
    strategy_name  text    NOT NULL,
    action         text    NOT NULL,
    price          double precision NOT NULL,
    indicator_json jsonb   NOT NULL
);

CREATE TABLE IF NOT EXISTS account_state (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    timestamp      double precision NOT NULL,
    timestamp_kst  text    NOT NULL,
    balance_usdt   double precision NOT NULL,
    positions_json jsonb   NOT NULL,
    realized_pnl   double precision NOT NULL,
    mode           text    NOT NULL DEFAULT 'paper'
);

-- ── 인덱스 ──────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS trades_timestamp_idx    ON trades       (timestamp);
CREATE INDEX IF NOT EXISTS trades_symbol_idx       ON trades       (symbol);
CREATE INDEX IF NOT EXISTS equity_curve_ts_idx     ON equity_curve (timestamp);
CREATE INDEX IF NOT EXISTS signals_timestamp_idx   ON signals      (timestamp);
CREATE INDEX IF NOT EXISTS signals_symbol_idx      ON signals      (symbol);
CREATE INDEX IF NOT EXISTS account_state_ts_idx    ON account_state (timestamp);

-- ── RLS (Row Level Security) ─────────────────────────────────────
-- 봇은 SUPABASE_DB_URL(서비스 권한)로 INSERT하므로 RLS 우회.
-- 대시보드(anon 역할)는 SELECT만 허용.

ALTER TABLE trades       ENABLE ROW LEVEL SECURITY;
ALTER TABLE equity_curve ENABLE ROW LEVEL SECURITY;
ALTER TABLE signals      ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_state ENABLE ROW LEVEL SECURITY;

-- anon 읽기 전용 정책 (멱등: DO 블록으로 중복 생성 방지)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'trades' AND policyname = 'anon_select_trades'
    ) THEN
        CREATE POLICY anon_select_trades
            ON trades FOR SELECT TO anon USING (true);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'equity_curve' AND policyname = 'anon_select_equity_curve'
    ) THEN
        CREATE POLICY anon_select_equity_curve
            ON equity_curve FOR SELECT TO anon USING (true);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'signals' AND policyname = 'anon_select_signals'
    ) THEN
        CREATE POLICY anon_select_signals
            ON signals FOR SELECT TO anon USING (true);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'account_state' AND policyname = 'anon_select_account_state'
    ) THEN
        CREATE POLICY anon_select_account_state
            ON account_state FOR SELECT TO anon USING (true);
    END IF;
END $$;
