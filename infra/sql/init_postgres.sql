-- =============================================================================
-- investMITRA — Supabase Schema (mitra-factory project)
-- ₹0: runs on Supabase free tier (500MB, Postgres 15)
--
-- Paste this entire file into: Supabase Dashboard → SQL Editor → Run
-- All tables live in the investmitra schema.
-- Local Docker uses public schema (search_path default) — both work fine.
-- =============================================================================

-- Schema
CREATE SCHEMA IF NOT EXISTS investmitra;
SET search_path TO investmitra;

-- Extensions available on Supabase free tier
CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA extensions;
CREATE EXTENSION IF NOT EXISTS "pg_trgm"   WITH SCHEMA extensions;

-- =============================================================================
-- COMPANY MASTER
-- =============================================================================
CREATE TABLE IF NOT EXISTS company_master (
    isin                VARCHAR(12)  PRIMARY KEY,
    nse_symbol          VARCHAR(20),
    bse_code            VARCHAR(10),
    company_name        VARCHAR(255) NOT NULL,
    aliases             TEXT[],
    sector              VARCHAR(100),
    industry            VARCHAR(100),
    market_cap_category VARCHAR(10),   -- LARGE | MID | SMALL | MICRO
    listing_date        DATE,
    is_active           BOOLEAN      NOT NULL DEFAULT TRUE,
    delisted_date       DATE,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cm_nse_symbol  ON company_master (nse_symbol);
CREATE INDEX IF NOT EXISTS idx_cm_bse_code    ON company_master (bse_code);
CREATE INDEX IF NOT EXISTS idx_cm_name_trgm   ON company_master USING gin (company_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_cm_active      ON company_master (is_active) WHERE is_active = TRUE;

COMMENT ON COLUMN company_master.is_active IS
    'Never delete delisted stocks — survivorship bias prevention.';
COMMENT ON COLUMN company_master.aliases IS
    'All known name variants. Used by NER entity linking pipeline.';

-- =============================================================================
-- SOURCE REGISTRY
-- =============================================================================
CREATE TABLE IF NOT EXISTS source_registry (
    source_id               VARCHAR(60)  PRIMARY KEY,
    domain                  VARCHAR(40)  NOT NULL,
    description             TEXT,
    refresh_frequency       VARCHAR(20)  NOT NULL,  -- realtime|daily|weekly|quarterly|event
    is_active               BOOLEAN      NOT NULL DEFAULT TRUE,
    last_successful_run     TIMESTAMPTZ,
    last_quality_score      SMALLINT,
    avg_quality_score_30d   FLOAT,
    consecutive_failures    SMALLINT     NOT NULL DEFAULT 0,
    auto_disabled_at        TIMESTAMPTZ,
    created_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

INSERT INTO source_registry (source_id, domain, description, refresh_frequency) VALUES
    ('nse_bhavcopy',          'market_data',        'NSE EOD equity prices (Bhavcopy)',       'daily'),
    ('nse_delivery',          'market_data',        'NSE delivery % data',                    'daily'),
    ('nse_fo_bhavcopy',       'market_data',        'NSE F&O OHLCV + open interest',          'daily'),
    ('bse_eod',               'market_data',        'BSE EOD equity prices',                  'daily'),
    ('nse_circuit_limits',    'market_data',        'NSE upper/lower circuit % per stock',    'daily'),
    ('bse_xbrl',              'company_financials', 'BSE XBRL quarterly filings',             'quarterly'),
    ('nse_financials_api',    'company_financials', 'NSE JSON financial results API',         'quarterly'),
    ('bse_shareholding',      'ownership',          'BSE shareholding pattern',               'quarterly'),
    ('sebi_insider',          'ownership',          'SEBI insider trading disclosures',       'event'),
    ('nse_corporate_actions', 'corporate_actions',  'NSE corporate actions',                  'event'),
    ('bse_corporate_actions', 'corporate_actions',  'BSE corporate actions',                  'event'),
    ('sebi_block_deals',      'ownership',          'SEBI block/bulk deals',                  'daily'),
    ('rbi_dbie',              'macroeconomic',      'RBI DBIE — repo rate, CPI, FX, M3',     'weekly'),
    ('mospi',                 'macroeconomic',      'MOSPI — GDP, IIP, CPI, WPI',            'monthly'),
    ('fred_api',              'macroeconomic',      'FRED — USD/INR, WTI, gold, US yields',  'daily'),
    ('et_rss',                'news_events',        'Economic Times RSS',                     'realtime'),
    ('mint_rss',              'news_events',        'Mint RSS',                               'realtime'),
    ('bs_rss',                'news_events',        'Business Standard RSS',                  'realtime'),
    ('reddit_india_invest',   'news_events',        'r/IndiaInvestments via praw',            'realtime'),
    ('google_trends',         'news_events',        'Google Trends per ticker',               'daily'),
    ('sebi_circulars',        'regulatory',         'SEBI circulars and orders (PDF)',        'event'),
    ('rbi_policy',            'regulatory',         'RBI MPC policy announcements',           'event'),
    ('pib_press',             'regulatory',         'PIB press releases RSS',                 'event')
ON CONFLICT (source_id) DO NOTHING;

-- =============================================================================
-- CORPORATE ACTIONS
-- =============================================================================
CREATE TABLE IF NOT EXISTS corporate_actions (
    id                     UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    isin                   VARCHAR(12) NOT NULL REFERENCES company_master(isin),
    action_type            VARCHAR(30) NOT NULL,  -- SPLIT|BONUS|DIVIDEND|RIGHTS|MERGER|DEMERGER
    ex_date                DATE        NOT NULL,
    record_date            DATE,
    adj_factor             FLOAT       NOT NULL,
    notes                  TEXT,
    nse_confirmed          BOOLEAN     NOT NULL DEFAULT FALSE,
    bse_confirmed          BOOLEAN     NOT NULL DEFAULT FALSE,
    requires_manual_review BOOLEAN     NOT NULL DEFAULT FALSE,
    quality_score          SMALLINT,
    ingested_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_id              VARCHAR(60) REFERENCES source_registry(source_id)
);

CREATE INDEX IF NOT EXISTS idx_ca_isin    ON corporate_actions (isin, ex_date DESC);
CREATE INDEX IF NOT EXISTS idx_ca_manual  ON corporate_actions (requires_manual_review)
    WHERE requires_manual_review = TRUE;

-- =============================================================================
-- OWNERSHIP DATA
-- =============================================================================
CREATE TABLE IF NOT EXISTS ownership_data (
    id                   UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    isin                 VARCHAR(12) NOT NULL REFERENCES company_master(isin),
    period_end           DATE        NOT NULL,
    filing_date          DATE        NOT NULL,   -- ⚠️ PIT key — always use this, not period_end
    promoter_pct         FLOAT,
    promoter_pledged_pct FLOAT,
    fii_pct              FLOAT,
    dii_pct              FLOAT,
    mf_pct               FLOAT,
    public_pct           FLOAT,
    total_shareholders   INTEGER,
    quality_score        SMALLINT    NOT NULL DEFAULT 100,
    source_id            VARCHAR(60) REFERENCES source_registry(source_id),
    source_doc_url       TEXT,
    ingested_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_own_isin_period
    ON ownership_data (isin, period_end, source_id);
CREATE INDEX IF NOT EXISTS idx_own_filing ON ownership_data (filing_date);

-- =============================================================================
-- NEWS EVENTS
-- =============================================================================
CREATE TABLE IF NOT EXISTS news_events (
    event_id         UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    published_at     TIMESTAMPTZ NOT NULL,
    source_id        VARCHAR(60) REFERENCES source_registry(source_id),
    headline         TEXT        NOT NULL,
    body_snippet     TEXT,
    url              TEXT,
    entities_isin    VARCHAR(12)[],
    entity_confidence FLOAT[],
    sentiment_score  FLOAT,       -- FinBERT: -1.0 (bearish) to +1.0 (bullish)
    sentiment_label  VARCHAR(10), -- positive | negative | neutral
    event_type       VARCHAR(40), -- earnings | insider | policy | macro | general
    quality_score    SMALLINT,
    ingested_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_news_pub      ON news_events (published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_entities ON news_events USING gin (entities_isin);

-- =============================================================================
-- MACROECONOMIC INDICATORS
-- =============================================================================
CREATE TABLE IF NOT EXISTS macroeconomic_indicators (
    id               UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    indicator_id     VARCHAR(60) NOT NULL,  -- IN_REPO_RATE | IN_CPI_YOY | USD_INR ...
    source_id        VARCHAR(60) REFERENCES source_registry(source_id),
    observation_date DATE        NOT NULL,
    value            FLOAT       NOT NULL,
    unit             VARCHAR(30),
    data_vintage     TIMESTAMPTZ NOT NULL DEFAULT NOW(),  -- revision tracking
    is_revised       BOOLEAN     NOT NULL DEFAULT FALSE,
    ingested_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_macro_vintage
    ON macroeconomic_indicators (indicator_id, observation_date, data_vintage);
CREATE INDEX IF NOT EXISTS idx_macro_obs
    ON macroeconomic_indicators (indicator_id, observation_date DESC);

-- =============================================================================
-- PIPELINE RUN LOG — audit trail for every ingestion run
-- =============================================================================
CREATE TABLE IF NOT EXISTS pipeline_run_log (
    run_id           UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_id        VARCHAR(60) NOT NULL REFERENCES source_registry(source_id),
    run_date         DATE        NOT NULL,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at     TIMESTAMPTZ,
    status           VARCHAR(20) NOT NULL,  -- running|success|failed|quarantined
    rows_ingested    INTEGER     DEFAULT 0,
    rows_quarantined INTEGER     DEFAULT 0,
    quality_score    SMALLINT,
    error_message    TEXT,
    prefect_run_id   VARCHAR(255)
);

CREATE INDEX IF NOT EXISTS idx_prl_source ON pipeline_run_log (source_id, run_date DESC);
CREATE INDEX IF NOT EXISTS idx_prl_failed ON pipeline_run_log (status)
    WHERE status IN ('failed', 'quarantined');

-- =============================================================================
-- EQUITY PRICES (plain Postgres version for Supabase free tier)
-- Declarative range partitioning by year — same query performance as
-- TimescaleDB for our workload (daily batch, not streaming inserts).
-- Local dev uses TimescaleDB instead (see init_timescale.sql).
-- =============================================================================
CREATE TABLE IF NOT EXISTS equity_prices (
    isin          VARCHAR(12)   NOT NULL,
    trade_date    DATE          NOT NULL,
    source        VARCHAR(5)    NOT NULL,   -- NSE | BSE
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
) PARTITION BY RANGE (trade_date);

-- Create yearly partitions (2014–2025)
DO $$
DECLARE yr INT;
BEGIN
    FOR yr IN 2014..2025 LOOP
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS equity_prices_%s
             PARTITION OF equity_prices
             FOR VALUES FROM (%L) TO (%L)',
            yr,
            yr || '-01-01',
            (yr + 1) || '-01-01'
        );
    END LOOP;
END $$;

CREATE INDEX IF NOT EXISTS idx_ep_isin_date ON equity_prices (isin, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_ep_quality   ON equity_prices (quality_score)
    WHERE quality_score < 70;

COMMENT ON TABLE equity_prices IS
    'Partitioned by year. Add new partition each January via: '
    'CREATE TABLE equity_prices_2026 PARTITION OF equity_prices '
    'FOR VALUES FROM (''2026-01-01'') TO (''2027-01-01'');';

-- =============================================================================
-- COMPANY FINANCIALS
-- =============================================================================
CREATE TABLE IF NOT EXISTS company_financials (
    id              UUID          PRIMARY KEY DEFAULT uuid_generate_v4(),
    isin            VARCHAR(12)   NOT NULL REFERENCES company_master(isin),
    period_end      DATE          NOT NULL,
    period_type     VARCHAR(10)   NOT NULL,  -- Q1|Q2|Q3|Q4|ANNUAL
    filing_date     DATE          NOT NULL,  -- ⚠️ PIT key
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
    taxonomy        VARCHAR(20),   -- IFRS | IND_GAAP | PDF_EXTRACTED
    quality_score   SMALLINT      NOT NULL DEFAULT 100,
    source_id       VARCHAR(60)   REFERENCES source_registry(source_id),
    source_doc_url  TEXT,
    ingested_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_fin_isin_period
    ON company_financials (isin, period_end, period_type, source_id);
CREATE INDEX IF NOT EXISTS idx_fin_filing ON company_financials (filing_date DESC);

COMMENT ON COLUMN company_financials.filing_date IS
    '⚠️ Always use filing_date in PIT feature joins. Never period_end — that creates look-ahead bias.';

-- =============================================================================
-- AUTO-UPDATE updated_at
-- =============================================================================
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END; $$;

DROP TRIGGER IF EXISTS trg_cm_updated_at   ON company_master;
DROP TRIGGER IF EXISTS trg_src_updated_at  ON source_registry;

CREATE TRIGGER trg_cm_updated_at
    BEFORE UPDATE ON company_master
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_src_updated_at
    BEFORE UPDATE ON source_registry
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
