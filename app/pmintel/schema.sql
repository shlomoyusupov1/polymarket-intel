CREATE TABLE IF NOT EXISTS markets (
    id                text PRIMARY KEY,
    condition_id      text,
    event_id          text,
    event_slug        text,
    slug              text,
    question          text NOT NULL,
    category          text,
    outcomes          jsonb NOT NULL DEFAULT '[]',
    token_ids         jsonb NOT NULL DEFAULT '[]',
    ref_index         int,
    ref_outcome       text,
    ref_price         double precision,
    best_bid          double precision,
    best_ask          double precision,
    spread            double precision,
    volume            double precision,
    volume_24h        double precision,
    liquidity         double precision,
    start_date        timestamptz,
    end_date          timestamptz,
    closed_time       timestamptz,
    active            boolean,
    closed            boolean,
    neg_risk          boolean,
    resolution_status text,
    resolved_outcome  text,
    resolution_source text,
    description       text,
    url               text,
    first_seen        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS markets_open_idx ON markets (closed, liquidity DESC);
CREATE INDEX IF NOT EXISTS markets_event_idx ON markets (event_id);

-- Live observations from the collector (only what we actually observed).
CREATE TABLE IF NOT EXISTS snapshots (
    ts          timestamptz NOT NULL,
    market_id   text NOT NULL,
    ref_price   double precision,
    best_bid    double precision,
    best_ask    double precision,
    spread      double precision,
    volume      double precision,
    volume_24h  double precision,
    liquidity   double precision,
    PRIMARY KEY (market_id, ts)
);

-- Historical prices from the CLOB prices-history endpoint (source: clob).
CREATE TABLE IF NOT EXISTS price_history (
    token_id   text NOT NULL,
    ts         timestamptz NOT NULL,
    market_id  text NOT NULL,
    price      double precision NOT NULL,
    PRIMARY KEY (token_id, ts)
);
CREATE INDEX IF NOT EXISTS price_history_market_idx ON price_history (market_id, ts);

CREATE TABLE IF NOT EXISTS job_runs (
    id          bigserial PRIMARY KEY,
    job         text NOT NULL,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status      text NOT NULL DEFAULT 'running',
    items       int,
    error       text
);
CREATE INDEX IF NOT EXISTS job_runs_job_idx ON job_runs (job, started_at DESC);

CREATE TABLE IF NOT EXISTS research_reports (
    id         bigserial PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    kind       text NOT NULL,
    params     jsonb,
    result     jsonb
);

-- Columns added after v0.1 (idempotent upgrades of existing deployments)
ALTER TABLE markets ADD COLUMN IF NOT EXISTS outcome_prices     jsonb;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS accepting_orders   boolean;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS enable_order_book  boolean;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS archived           boolean;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS neg_risk_market_id text;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS fees_enabled       boolean;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS one_day_change     double precision;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS one_week_change    double precision;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS volume_1wk         double precision;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS group_item_title   text;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS neg_risk_other     boolean;
CREATE INDEX IF NOT EXISTS markets_vol24_idx ON markets (volume_24h DESC NULLS LAST) WHERE active AND NOT closed;
CREATE INDEX IF NOT EXISTS markets_negrisk_idx ON markets (neg_risk_market_id) WHERE neg_risk_market_id IS NOT NULL;

-- Live order-book observations (reference outcome token). Only what we observed.
CREATE TABLE IF NOT EXISTS orderbooks (
    ts             timestamptz NOT NULL,
    market_id      text NOT NULL,
    token_id       text NOT NULL,
    best_bid       double precision,
    best_ask       double precision,
    spread         double precision,
    bid_depth_5c   double precision,
    ask_depth_5c   double precision,
    buy_vwap_100   double precision,
    buy_vwap_1000  double precision,
    sell_vwap_100  double precision,
    sell_vwap_1000 double precision,
    levels         jsonb,
    valid          boolean,
    n_bids         int,
    n_asks         int,
    fetched_at     timestamptz,
    PRIMARY KEY (market_id, ts)
);

-- TimescaleDB is optional: used when available (docker image), skipped on plain Postgres.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb') THEN
        CREATE EXTENSION IF NOT EXISTS timescaledb;
        PERFORM create_hypertable('snapshots', 'ts', if_not_exists => TRUE, migrate_data => TRUE);
        PERFORM create_hypertable('price_history', 'ts', if_not_exists => TRUE, migrate_data => TRUE);
        PERFORM create_hypertable('orderbooks', 'ts', if_not_exists => TRUE, migrate_data => TRUE);
        BEGIN
            ALTER TABLE snapshots SET (timescaledb.compress, timescaledb.compress_segmentby = 'market_id');
            PERFORM add_compression_policy('snapshots', INTERVAL '7 days', if_not_exists => TRUE);
            ALTER TABLE price_history SET (timescaledb.compress, timescaledb.compress_segmentby = 'token_id');
            PERFORM add_compression_policy('price_history', INTERVAL '30 days', if_not_exists => TRUE);
            ALTER TABLE orderbooks SET (timescaledb.compress, timescaledb.compress_segmentby = 'market_id');
            PERFORM add_compression_policy('orderbooks', INTERVAL '7 days', if_not_exists => TRUE);
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'compression setup skipped: %', SQLERRM;
        END;
    END IF;
END $$;

-- ETF paper study v2 (docs/ETF_PAPER_V2_PROTOCOL.md). First-seen bars are never overwritten,
-- so a later data revision cannot change a sealed decision or its fill.
CREATE TABLE IF NOT EXISTS etf_bars (
    symbol     text NOT NULL,
    day        date NOT NULL,
    o double precision NOT NULL, h double precision NOT NULL,
    l double precision NOT NULL, c double precision NOT NULL, v double precision NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, day)
);
CREATE TABLE IF NOT EXISTS etf_dividends (
    symbol     text NOT NULL,
    ex_date    date NOT NULL,
    amount     double precision NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, ex_date)
);
-- one sealed intent per account per decision bar; never updated
CREATE TABLE IF NOT EXISTS etf_intents (
    account    text NOT NULL,
    based_on   date NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    sha256     text NOT NULL,
    payload    jsonb NOT NULL,
    PRIMARY KEY (account, based_on)
);
