"""Frozen, additive Factor Lab migration 014. Not applied on import.

No legacy table replacement, grants, backfill, or environment access. Functions
bind the installation search_path, and use invoker (not elevated) privileges.
"""
PUBLICATION_SQL = r"""
CREATE TABLE fl_publication_requests (
    request_id TEXT PRIMARY KEY CHECK (request_id ~ '^[0-9a-f]{64}$'),
    asof DATE NOT NULL,
    spec JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (request_id, asof)
);
CREATE TABLE fl_publication_attempts (
    attempt_id UUID PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES fl_publication_requests(request_id),
    started_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX fl_publication_attempt_request ON fl_publication_attempts(request_id);
CREATE TABLE fl_dataset_generations (
    generation_id UUID PRIMARY KEY REFERENCES fl_publication_attempts(attempt_id),
    request_id TEXT NOT NULL UNIQUE,
    asof DATE NOT NULL,
    sealed_at TIMESTAMPTZ NOT NULL,
    seal_xid BIGINT NOT NULL,
    FOREIGN KEY (request_id, asof) REFERENCES fl_publication_requests(request_id, asof),
    UNIQUE (generation_id, asof)
);
CREATE TABLE fl_publication_outcomes (
    attempt_id UUID PRIMARY KEY REFERENCES fl_publication_attempts(attempt_id),
    status TEXT NOT NULL CHECK (status IN ('published','reused','failed','interrupted')),
    generation_id UUID REFERENCES fl_dataset_generations(generation_id),
    error_code TEXT CHECK (error_code IN ('build_failed','interrupted')),
    finished_at TIMESTAMPTZ NOT NULL,
    CHECK ((status IN ('published','reused') AND generation_id IS NOT NULL AND error_code IS NULL)
        OR (status IN ('failed','interrupted') AND generation_id IS NULL AND error_code IS NOT NULL))
);
CREATE TABLE fl_universe_generations (
    generation_id UUID NOT NULL,
    asof DATE NOT NULL,
    security_id INT NOT NULL REFERENCES securities(security_id),
    mktcap NUMERIC, adv_63d NUMERIC, price NUMERIC,
    in_universe BOOLEAN NOT NULL,
    size_bucket TEXT,
    sector TEXT NOT NULL CHECK (length(sector) BETWEEN 1 AND 120),
    PRIMARY KEY (generation_id, security_id),
    FOREIGN KEY (generation_id, asof) REFERENCES fl_dataset_generations(generation_id, asof),
    CHECK (mktcap IS NULL OR mktcap NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)),
    CHECK (adv_63d IS NULL OR adv_63d NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)),
    CHECK (price IS NULL OR price NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)),
    CHECK ((in_universe AND mktcap IS NOT NULL AND mktcap > 0
                       AND adv_63d IS NOT NULL AND adv_63d > 0
                       AND price IS NOT NULL AND price > 0
                       AND size_bucket IS NOT NULL AND size_bucket IN ('small','mid','large'))
        OR (NOT in_universe AND size_bucket IS NULL))
);
CREATE TABLE fl_factor_generations (
    generation_id UUID NOT NULL,
    asof DATE NOT NULL,
    security_id INT NOT NULL,
    factor_id TEXT NOT NULL,
    raw NUMERIC, rank_norm NUMERIC, z_sector NUMERIC, z_sector_size NUMERIC,
    missing_reason TEXT,
    PRIMARY KEY (generation_id, security_id, factor_id),
    FOREIGN KEY (generation_id, security_id) REFERENCES fl_universe_generations(generation_id, security_id),
    FOREIGN KEY (generation_id, asof) REFERENCES fl_dataset_generations(generation_id, asof),
    CHECK (raw IS NULL OR raw NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)),
    CHECK (rank_norm IS NULL OR rank_norm NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)),
    CHECK (z_sector IS NULL OR z_sector NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)),
    CHECK (z_sector_size IS NULL OR z_sector_size NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)),
    CHECK ((z_sector_size IS NULL AND missing_reason IS NOT NULL AND missing_reason IN
            ('not_applicable','insufficient_history','source_value_missing','insufficient_peer_group'))
        OR (z_sector_size IS NOT NULL AND raw IS NOT NULL AND rank_norm IS NOT NULL
            AND z_sector IS NOT NULL AND missing_reason IS NULL)),
    CHECK (raw IS NOT NULL OR (rank_norm IS NULL AND z_sector IS NULL AND z_sector_size IS NULL))
);
CREATE TABLE fl_publication_receipts (
    generation_id UUID PRIMARY KEY REFERENCES fl_dataset_generations(generation_id),
    available_at TIMESTAMPTZ NOT NULL,
    universe_sha256 TEXT NOT NULL CHECK (universe_sha256 ~ '^[0-9a-f]{64}$'),
    factors_sha256 TEXT NOT NULL CHECK (factors_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE FUNCTION flp_request_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
DECLARE required_keys TEXT[] := ARRAY['dataset','asof','source_revision','inputs_observed_at',
    'input_fingerprints','factor_contract','expected_universe','expected_investable'];
input_keys TEXT[] := ARRAY['universe','fundamentals','returns','surprises',
    'benchmarks','classifications','factor_registry'];
core JSONB := '{"gp_a":{"version":1,"formula_hash":"924506ea504b239e"},
    "mom_12_1":{"version":1,"formula_hash":"d7a21fca19ecd4da"},
    "net_issuance":{"version":1,"formula_hash":"260d5cec83fe0d55"},
    "sue":{"version":1,"formula_hash":"a94ea425e90066d0"},
    "vol_12m":{"version":1,"formula_hash":"91b06eaf0881f996"}}';
BEGIN
    IF jsonb_typeof(NEW.spec) IS DISTINCT FROM 'object'
       OR NOT (NEW.spec ?& required_keys) OR (NEW.spec - required_keys) <> '{}'::jsonb
       OR NEW.spec->>'dataset' IS DISTINCT FROM 'monthly_factor_panel_v1'
       OR (NEW.spec->>'source_revision' ~ '^[0-9a-f]{40}$') IS NOT TRUE
       OR NEW.spec->'factor_contract' IS DISTINCT FROM core
       OR jsonb_typeof(NEW.spec->'input_fingerprints') IS DISTINCT FROM 'object'
       OR NOT ((NEW.spec->'input_fingerprints') ?& input_keys)
       OR ((NEW.spec->'input_fingerprints') - input_keys) <> '{}'::jsonb
       OR (NEW.spec->>'expected_universe' ~ '^[1-9][0-9]*$') IS NOT TRUE
       OR (NEW.spec->>'expected_investable' ~ '^[1-9][0-9]*$') IS NOT TRUE
       OR (NEW.spec->>'expected_investable')::integer > (NEW.spec->>'expected_universe')::integer
       OR (NEW.spec->>'expected_universe')::integer > 100000
       OR (NEW.spec->>'asof' ~ '^\d{4}-\d{2}-\d{2}$') IS NOT TRUE
       OR (NEW.spec->>'inputs_observed_at' ~ '(Z|\+00:00)$') IS NOT TRUE
    THEN RAISE EXCEPTION 'invalid_publication_spec' USING ERRCODE='23514'; END IF;
    IF EXISTS (SELECT 1 FROM jsonb_each(NEW.spec->'input_fingerprints') p
               WHERE jsonb_typeof(p.value) <> 'string' OR NOT (p.value#>>'{}' ~ '^[0-9a-f]{64}$'))
    THEN RAISE EXCEPTION 'invalid_input_fingerprint' USING ERRCODE='23514'; END IF;
    NEW.asof := (NEW.spec->>'asof')::date;
    NEW.created_at := clock_timestamp();
    IF (NEW.spec->>'inputs_observed_at')::timestamptz > NEW.created_at
       OR NEW.asof > ((NEW.spec->>'inputs_observed_at')::timestamptz AT TIME ZONE 'UTC')::date
    THEN RAISE EXCEPTION 'unobserved_or_future_inputs' USING ERRCODE='23514'; END IF;
    NEW.request_id := encode(sha256(convert_to(NEW.spec::text,'UTF8')),'hex');
    RETURN NEW;
END $$;
CREATE TRIGGER flp_request_insert BEFORE INSERT ON fl_publication_requests
FOR EACH ROW EXECUTE FUNCTION flp_request_guard();

CREATE FUNCTION flp_attempt_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
BEGIN
    NEW.started_at := clock_timestamp();
    RETURN NEW;
END $$;
CREATE TRIGGER flp_attempt_insert BEFORE INSERT ON fl_publication_attempts
FOR EACH ROW EXECUTE FUNCTION flp_attempt_guard();

CREATE FUNCTION flp_generation_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
BEGIN
    SELECT a.request_id, r.asof INTO NEW.request_id, NEW.asof
    FROM fl_publication_attempts a JOIN fl_publication_requests r USING(request_id)
    WHERE a.attempt_id=NEW.generation_id;
    IF NEW.request_id IS NULL OR EXISTS(SELECT 1 FROM fl_publication_outcomes
                                       WHERE attempt_id=NEW.generation_id)
    THEN RAISE EXCEPTION 'attempt_not_open' USING ERRCODE='23514'; END IF;
    NEW.sealed_at := clock_timestamp();
    NEW.seal_xid := txid_current();
    RETURN NEW;
END $$;
CREATE TRIGGER flp_generation_insert BEFORE INSERT ON fl_dataset_generations
FOR EACH ROW EXECUTE FUNCTION flp_generation_guard();

CREATE FUNCTION flp_child_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
DECLARE xid BIGINT;
BEGIN
    SELECT seal_xid INTO xid FROM fl_dataset_generations WHERE generation_id=NEW.generation_id;
    IF xid IS NULL OR xid <> txid_current() OR EXISTS (SELECT 1 FROM fl_publication_outcomes
            WHERE attempt_id=NEW.generation_id) THEN
        RAISE EXCEPTION 'sealed_generation_rejects_late_children' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER flp_universe_insert BEFORE INSERT ON fl_universe_generations
FOR EACH ROW EXECUTE FUNCTION flp_child_guard();
CREATE TRIGGER flp_factor_insert BEFORE INSERT ON fl_factor_generations
FOR EACH ROW EXECUTE FUNCTION flp_child_guard();

CREATE FUNCTION flp_outcome_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
DECLARE requested TEXT; actual TEXT;
BEGIN
    SELECT request_id INTO requested FROM fl_publication_attempts WHERE attempt_id=NEW.attempt_id;
    IF NEW.status IN ('published','reused') THEN
        SELECT request_id INTO actual FROM fl_dataset_generations WHERE generation_id=NEW.generation_id;
        IF actual IS DISTINCT FROM requested OR actual IS NULL
           OR (NEW.status='published' AND NEW.generation_id<>NEW.attempt_id)
           OR (NEW.status='reused' AND NEW.generation_id=NEW.attempt_id)
        THEN RAISE EXCEPTION 'outcome_generation_mismatch' USING ERRCODE='23514'; END IF;
    ELSIF EXISTS(SELECT 1 FROM fl_dataset_generations WHERE generation_id=NEW.attempt_id) THEN
        RAISE EXCEPTION 'committed_generation_cannot_be_failed' USING ERRCODE='23514';
    END IF;
    NEW.finished_at := clock_timestamp();
    RETURN NEW;
END $$;
CREATE TRIGGER flp_outcome_insert BEFORE INSERT ON fl_publication_outcomes
FOR EACH ROW EXECUTE FUNCTION flp_outcome_guard();

CREATE FUNCTION flp_validate_generation() RETURNS trigger LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
DECLARE s JSONB; n BIGINT; m BIGINT; nf BIGINT; fid TEXT; good BIGINT; present BIGINT;
BEGIN
    SELECT spec INTO s FROM fl_publication_requests WHERE request_id=NEW.request_id;
    SELECT count(*), count(*) FILTER(WHERE in_universe) INTO n,m
    FROM fl_universe_generations WHERE generation_id=NEW.generation_id;
    SELECT count(*) INTO nf FROM fl_factor_generations WHERE generation_id=NEW.generation_id;
    IF n<>(s->>'expected_universe')::integer OR m<>(s->>'expected_investable')::integer
       OR nf<>m*5 OR NOT EXISTS(SELECT 1 FROM fl_publication_outcomes
             WHERE attempt_id=NEW.generation_id AND status='published' AND generation_id=NEW.generation_id)
    THEN RAISE EXCEPTION 'incomplete_generation' USING ERRCODE='23514'; END IF;
    IF EXISTS(SELECT 1 FROM fl_factor_generations f JOIN fl_universe_generations u
              USING(generation_id,security_id) WHERE f.generation_id=NEW.generation_id
              AND (NOT u.in_universe OR NOT ((s->'factor_contract') ? f.factor_id)))
    THEN RAISE EXCEPTION 'factor_scope_mismatch' USING ERRCODE='23514'; END IF;
    FOR fid IN SELECT jsonb_object_keys(s->'factor_contract') LOOP
        SELECT count(*), count(z_sector_size) INTO present,good FROM fl_factor_generations
        WHERE generation_id=NEW.generation_id AND factor_id=fid;
        IF present<>m OR good=0 THEN
            RAISE EXCEPTION 'missing_or_all_null_factor_panel' USING ERRCODE='23514';
        END IF;
    END LOOP;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER flp_complete_generation AFTER INSERT ON fl_dataset_generations
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION flp_validate_generation();

CREATE FUNCTION flp_receipt_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
DECLARE xid BIGINT;
BEGIN
    -- A different transaction can only see this immutable generation after its
    -- complete write (including deferred validation) committed. A timestamp from
    -- the sealing transaction is NOT accepted as an availability certificate.
    SELECT seal_xid INTO xid FROM fl_dataset_generations WHERE generation_id=NEW.generation_id;
    IF xid IS NULL OR xid=txid_current() THEN
        RAISE EXCEPTION 'receipt_requires_committed_generation' USING ERRCODE='23514';
    END IF;
    NEW.available_at := clock_timestamp();
    SELECT encode(sha256(convert_to(string_agg(
        encode(sha256(convert_to((to_jsonb(u)-'generation_id')::text,'UTF8')),'hex'),
        '' ORDER BY security_id),'UTF8')),'hex') INTO NEW.universe_sha256
    FROM fl_universe_generations u WHERE generation_id=NEW.generation_id;
    SELECT encode(sha256(convert_to(string_agg(
        encode(sha256(convert_to((to_jsonb(f)-'generation_id')::text,'UTF8')),'hex'),
        '' ORDER BY security_id, factor_id),'UTF8')),'hex') INTO NEW.factors_sha256
    FROM fl_factor_generations f WHERE generation_id=NEW.generation_id;
    RETURN NEW;
END $$;
CREATE TRIGGER flp_receipt_insert BEFORE INSERT ON fl_publication_receipts
FOR EACH ROW EXECUTE FUNCTION flp_receipt_guard();

CREATE FUNCTION flp_no_mutation() RETURNS trigger LANGUAGE plpgsql
SET search_path FROM CURRENT AS $$
BEGIN RAISE EXCEPTION 'publication_history_is_immutable' USING ERRCODE='23514'; END $$;
DO $$ DECLARE t TEXT; BEGIN
    FOREACH t IN ARRAY ARRAY['fl_publication_requests','fl_publication_attempts',
        'fl_publication_outcomes','fl_dataset_generations','fl_universe_generations',
        'fl_factor_generations','fl_publication_receipts'] LOOP
        EXECUTE format('CREATE TRIGGER flp_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON %I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION flp_no_mutation()', t);
    END LOOP;
END $$;

CREATE VIEW fl_published_datasets AS
SELECT g.generation_id, g.request_id, g.asof, r.spec, p.available_at,
       p.universe_sha256, p.factors_sha256
FROM fl_dataset_generations g JOIN fl_publication_receipts p USING(generation_id)
JOIN fl_publication_requests r USING(request_id);
"""
