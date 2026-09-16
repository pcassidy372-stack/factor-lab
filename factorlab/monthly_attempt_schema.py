"""Additive migration 015: monthly attempts; legacy job_log is not rewritten."""
MONTHLY_ATTEMPT_SQL = r'''
CREATE TABLE fl_monthly_attempts (
    attempt_id UUID PRIMARY KEY,
    period_key TEXT NOT NULL CHECK (period_key ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
    source_revision TEXT NOT NULL CHECK (source_revision ~ '^[0-9a-f]{40}$'),
    started_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX fl_monthly_period ON fl_monthly_attempts(period_key);
CREATE TABLE fl_monthly_outcomes (
    attempt_id UUID PRIMARY KEY REFERENCES fl_monthly_attempts(attempt_id),
    status TEXT NOT NULL CHECK (status IN ('published','reused','failed','interrupted')),
    generation_id UUID REFERENCES fl_publication_receipts(generation_id),
    error_code TEXT CHECK (error_code IN ('monthly_failed','interrupted')),
    finished_at TIMESTAMPTZ NOT NULL,
    CHECK ((status IN ('published','reused') AND generation_id IS NOT NULL AND error_code IS NULL)
        OR (status IN ('failed','interrupted') AND generation_id IS NULL AND error_code IS NOT NULL))
);
CREATE FUNCTION flm_attempt_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
BEGIN
    -- Also reject invalid calendar years accepted by the textual check.
    PERFORM (NEW.period_key || '-01')::date;
    NEW.started_at := clock_timestamp();
    RETURN NEW;
END $$;
CREATE TRIGGER flm_attempt_insert BEFORE INSERT ON fl_monthly_attempts
FOR EACH ROW EXECUTE FUNCTION flm_attempt_guard();
CREATE FUNCTION flm_outcome_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
DECLARE a fl_monthly_attempts%ROWTYPE;
BEGIN
    SELECT * INTO STRICT a FROM fl_monthly_attempts WHERE attempt_id=NEW.attempt_id;
    IF NEW.generation_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM fl_published_datasets p WHERE p.generation_id=NEW.generation_id
        AND p.asof >= ((a.period_key || '-01')::date - interval '1 month')::date
        AND p.asof < (a.period_key || '-01')::date
        AND p.spec->>'source_revision'=a.source_revision)
    THEN RAISE EXCEPTION 'monthly_publication_lineage_mismatch' USING ERRCODE='23514'; END IF;
    NEW.finished_at := clock_timestamp();
    RETURN NEW;
END $$;
CREATE TRIGGER flm_outcome_insert BEFORE INSERT ON fl_monthly_outcomes
FOR EACH ROW EXECUTE FUNCTION flm_outcome_guard();
CREATE TRIGGER flm_attempt_immutable BEFORE UPDATE OR DELETE ON fl_monthly_attempts
FOR EACH ROW EXECUTE FUNCTION flp_no_mutation();
CREATE TRIGGER flm_attempt_no_truncate BEFORE TRUNCATE ON fl_monthly_attempts
FOR EACH STATEMENT EXECUTE FUNCTION flp_no_mutation();
CREATE TRIGGER flm_outcome_immutable BEFORE UPDATE OR DELETE ON fl_monthly_outcomes
FOR EACH ROW EXECUTE FUNCTION flp_no_mutation();
CREATE TRIGGER flm_outcome_no_truncate BEFORE TRUNCATE ON fl_monthly_outcomes
FOR EACH STATEMENT EXECUTE FUNCTION flp_no_mutation();
'''
