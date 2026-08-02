-- =============================================================================
-- ClearedCircle — TimescaleDB Schema (LOCAL DEV ONLY)
-- Production uses plain partitioned Postgres on Supabase free tier.
-- Columns are identical — same application code works against both.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Same table definition as init_postgres.sql but WITHOUT partitioning
-- TimescaleDB handles chunking automatically via create_hypertable
CREATE TABLE IF NOT EXISTS equity_prices (
    isin          VARCHAR(12)   NOT NULL,
    trade_date    DATE          NOT NULL,
    source        VARCHAR(5)    NOT NULL,
    open          DECIMAL(12,2) NOT NULL,
    high          DECIMAL(12,2) NOT NULL,
    low           DECIMAL(12,2) NOT NULL,
    close         DECIMAL(12,2) NOT NULL,
    vwap          DECIMAL(12,2),
    volume        BIGINT        NOT NULL,
    turnover_cr   DECIMAL(16,4),
    delivery_pct  FLOAT,
    adj_close     DECIMAL(12,2),
    adj_factor    FLOAT,
    circuit_upper DECIMAL(12,2),
    circuit_lower DECIMAL(12,2),
    quality_score SMALLINT      NOT NULL DEFAULT 100,
    ingested_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    PRIMARY KEY (isin, trade_date, source)
);

SELECT create_hypertable(
    'equity_prices', 'trade_date',
    chunk_time_interval => INTERVAL '1 month',
    if_not_exists => TRUE
);

ALTER TABLE equity_prices SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'isin, source',
    timescaledb.compress_orderby   = 'trade_date DESC'
);
SELECT add_compression_policy('equity_prices', INTERVAL '12 months');

CREATE INDEX IF NOT EXISTS idx_ep_isin_date ON equity_prices (isin, trade_date DESC);

-- Monthly rollup continuous aggregate (local dev convenience)
CREATE MATERIALIZED VIEW IF NOT EXISTS equity_prices_monthly
WITH (timescaledb.continuous) AS
SELECT
    isin, source,
    time_bucket('1 month', trade_date) AS month,
    FIRST(open, trade_date)            AS open,
    MAX(high)                          AS high,
    MIN(low)                           AS low,
    LAST(close, trade_date)            AS close,
    LAST(adj_close, trade_date)        AS adj_close,
    SUM(volume)                        AS volume,
    COUNT(*)                           AS trading_days
FROM equity_prices
WHERE quality_score >= 70
GROUP BY isin, source, time_bucket('1 month', trade_date)
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'equity_prices_monthly',
    start_offset      => INTERVAL '3 months',
    end_offset        => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day'
);

-- Company financials (same as Postgres version)
CREATE TABLE IF NOT EXISTS company_financials (
    id              UUID          PRIMARY KEY DEFAULT uuid_generate_v4(),
    isin            VARCHAR(12)   NOT NULL,
    period_end      DATE          NOT NULL,
    period_type     VARCHAR(10)   NOT NULL,
    filing_date     DATE          NOT NULL,
    revenue_cr      DECIMAL(18,4),
    ebitda_cr       DECIMAL(18,4),
    ebit_cr         DECIMAL(18,4),
    pat_cr          DECIMAL(18,4),
    eps             DECIMAL(12,4),
    total_assets_cr DECIMAL(18,4),
    total_debt_cr   DECIMAL(18,4),
    cash_cr         DECIMAL(18,4),
    equity_cr       DECIMAL(18,4),
    cfo_cr          DECIMAL(18,4),
    capex_cr        DECIMAL(18,4),
    fcf_cr          DECIMAL(18,4),
    is_consolidated BOOLEAN       NOT NULL DEFAULT TRUE,
    taxonomy        VARCHAR(20),
    quality_score   SMALLINT      NOT NULL DEFAULT 100,
    source_id       VARCHAR(60),
    source_doc_url  TEXT,
    ingested_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_fin_isin_period
    ON company_financials (isin, period_end, period_type, source_id);
