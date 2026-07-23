"""Schema migrations. 001 = Phase 0 foundation: security master (spec s3),
bitemporal fundamentals with R7/R8 columns, raw prices + corp actions + TR,
delistings, universe snapshots (spec s4, verdicts R7-R12)."""

MIGRATIONS = {}

MIGRATIONS[1] = """
-- ============ identity: the security master (spec s3, R12) ============
CREATE TABLE issuers (
    issuer_id   SERIAL PRIMARY KEY,
    cik         TEXT UNIQUE,
    name        TEXT NOT NULL
);

CREATE TABLE securities (
    security_id SERIAL PRIMARY KEY,
    issuer_id   INT NOT NULL REFERENCES issuers(issuer_id),
    isin        TEXT,
    cusip       TEXT,
    share_class TEXT,
    first_seen  DATE,
    last_seen   DATE,
    status      TEXT NOT NULL DEFAULT 'active'  -- active|delisted|merged|shell|unknown
);
CREATE INDEX securities_isin_idx  ON securities(isin);
CREATE INDEX securities_cusip_idx ON securities(cusip);

CREATE TABLE symbol_map (
    security_id INT  NOT NULL REFERENCES securities(security_id),
    symbol      TEXT NOT NULL,
    exchange    TEXT,
    valid_from  DATE NOT NULL,
    valid_to    DATE,                            -- NULL = currently effective
    source      TEXT NOT NULL,                   -- feed|profile|manual
    PRIMARY KEY (symbol, valid_from)
);
CREATE INDEX symbol_map_sec_idx ON symbol_map(security_id);

-- ============ bitemporal fundamentals (spec s4, R7/R8) ============
CREATE TABLE fundamentals_q (
    security_id       INT  NOT NULL REFERENCES securities(security_id),
    fiscal_period_end DATE NOT NULL,
    period            TEXT NOT NULL,             -- Q1..Q4
    vintage_id        INT  NOT NULL DEFAULT 1,
    accepted_date     TIMESTAMPTZ,               -- public-knowledge time
    filing_date       DATE,
    observed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),  -- system time
    backfill          BOOLEAN NOT NULL DEFAULT false,
    value_pit         BOOLEAN NOT NULL DEFAULT false,      -- R7
    timing_pit        BOOLEAN NOT NULL DEFAULT false,      -- R7
    lag_class         TEXT,                      -- R8: missing|release|filing|delinquent
    accession_no      TEXT,
    source_hash       TEXT NOT NULL,
    mapping_version   TEXT NOT NULL,
    currency          TEXT,
    revenue NUMERIC, gross_profit NUMERIC, ebit NUMERIC, net_income NUMERIC,
    cfo NUMERIC, capex NUMERIC, total_assets NUMERIC, total_debt NUMERIC,
    cash NUMERIC, equity NUMERIC, shares_dil NUMERIC,
    raw JSONB NOT NULL,
    PRIMARY KEY (security_id, fiscal_period_end, vintage_id)
);
CREATE INDEX fundamentals_q_accepted_idx ON fundamentals_q(accepted_date);

-- ============ prices, actions, TR (spec s6, R10) ============
CREATE TABLE prices_raw_d (
    security_id INT NOT NULL REFERENCES securities(security_id),
    d           DATE NOT NULL,
    open NUMERIC, high NUMERIC, low NUMERIC, close NUMERIC,
    volume      NUMERIC,
    PRIMARY KEY (security_id, d)
);

CREATE TABLE corp_actions (
    security_id INT  NOT NULL REFERENCES securities(security_id),
    ex_date     DATE NOT NULL,
    action_type TEXT NOT NULL,                   -- split|div_cash|div_special|spinoff|other
    ratio       NUMERIC,
    amount      NUMERIC,
    meta        JSONB,
    source      TEXT NOT NULL,
    PRIMARY KEY (security_id, ex_date, action_type)
);

CREATE TABLE tr_index_d (
    security_id    INT  NOT NULL REFERENCES securities(security_id),
    d              DATE NOT NULL,
    tr             NUMERIC NOT NULL,
    method_version TEXT NOT NULL,
    PRIMARY KEY (security_id, d)
);

CREATE TABLE mktcap_m (
    asof        DATE NOT NULL,
    security_id INT  NOT NULL REFERENCES securities(security_id),
    mktcap      NUMERIC,
    PRIMARY KEY (asof, security_id)
);

-- ============ delistings (spec s6, R11) ============
CREATE TABLE delistings (
    security_id     INT  PRIMARY KEY REFERENCES securities(security_id),
    delist_date     DATE NOT NULL,
    delist_reason   TEXT,                        -- merger|bankruptcy|reg|unknown
    terminal_return NUMERIC,
    terminal_method TEXT,                        -- rung1-deal|rung2-imputed|rung3-flagged
    source          TEXT NOT NULL
);

-- ============ universe (spec s5/s8) ============
CREATE TABLE universe_snapshots (
    asof        DATE NOT NULL,
    security_id INT  NOT NULL REFERENCES securities(security_id),
    mktcap      NUMERIC,
    adv_63d     NUMERIC,
    price       NUMERIC,
    in_universe BOOLEAN NOT NULL,
    size_bucket TEXT,
    PRIMARY KEY (asof, security_id)
);
"""
