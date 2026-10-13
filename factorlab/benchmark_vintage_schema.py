"""Additive 016. Trusted public owner; install with search_path=public,pg_temp.

Invoker guards and fully qualified relations. A committed header is the seal:
its deferred constraint demands the complete grid; value inserts require the
header's creating transaction. Timestamps are observations, never commit clocks.
"""
BENCHMARK_VINTAGE_SQL = r"""
CREATE TABLE public.benchmark_vintages (
 vintage_id UUID PRIMARY KEY,
 period TEXT NOT NULL CHECK (period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
 document BYTEA NOT NULL CHECK(octet_length(document) BETWEEN 1 AND 262144),
 payload BYTEA NOT NULL CHECK(octet_length(payload) BETWEEN 1 AND 5242880),
 document_sha256 TEXT NOT NULL CHECK(document_sha256=encode(sha256(document),'hex')),
 payload_sha256 TEXT NOT NULL CHECK(payload_sha256=encode(sha256(payload),'hex')),
 grid DATE[] NOT NULL CHECK(array_ndims(grid)=1 AND array_lower(grid,1)=1 AND array_upper(grid,1)=13 AND array_position(grid,NULL) IS NULL),
 levels NUMERIC[] NOT NULL CHECK(array_ndims(levels)=1 AND array_lower(levels,1)=1 AND array_upper(levels,1)=13 AND array_position(levels,NULL) IS NULL),
 source_start TIMESTAMPTZ NOT NULL,
 source_end TIMESTAMPTZ NOT NULL CHECK(source_end>=source_start),
 imported_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 import_xid XID8 NOT NULL DEFAULT pg_current_xact_id(),
 UNIQUE(period,vintage_id)
);
CREATE TABLE public.benchmark_vintage_values (
 vintage_id UUID NOT NULL REFERENCES public.benchmark_vintages,
 asof DATE NOT NULL,
 symbol TEXT NOT NULL CHECK(symbol='SPY'),
 tr NUMERIC NOT NULL CHECK(tr>0 AND tr NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)),
 PRIMARY KEY(vintage_id,asof)
);
CREATE TABLE public.benchmark_vintage_selections (
 event_id UUID PRIMARY KEY,
 period TEXT NOT NULL,
 vintage_id UUID NOT NULL,
 predecessor UUID,
 approval_reference TEXT NOT NULL CHECK(length(btrim(approval_reference)) BETWEEN 1 AND 1000),
 selected_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 selection_xid XID8 NOT NULL DEFAULT pg_current_xact_id(),
 UNIQUE(period,event_id),
 UNIQUE NULLS NOT DISTINCT(period,predecessor),
 FOREIGN KEY(period,vintage_id) REFERENCES public.benchmark_vintages(period,vintage_id),
 FOREIGN KEY(period,predecessor) REFERENCES public.benchmark_vintage_selections(period,event_id),
 CHECK(event_id IS DISTINCT FROM predecessor)
);
CREATE FUNCTION public.bv_immutable() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN RAISE EXCEPTION 'benchmark history immutable' USING ERRCODE='23514'; END $$;
CREATE FUNCTION public.bv_json_unique(j json, depth integer DEFAULT 0) RETURNS boolean LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE r record;
BEGIN
 IF depth>20 THEN RETURN false; END IF;
 IF json_typeof(j)='object' THEN
  IF EXISTS(SELECT 1 FROM json_each(j) GROUP BY key HAVING count(*)>1) THEN RETURN false; END IF;
  FOR r IN SELECT value FROM json_each(j) LOOP
   IF NOT public.bv_json_unique(r.value,depth+1) THEN RETURN false; END IF;
  END LOOP;
 ELSIF json_typeof(j)='array' THEN
  FOR r IN SELECT value FROM json_array_elements(j) LOOP
   IF NOT public.bv_json_unique(r.value,depth+1) THEN RETURN false; END IF;
  END LOOP;
 END IF;
 RETURN true;
END $$;
CREATE FUNCTION public.bv_header() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE i integer; doc jsonb; raw jsonb; required_date date; n numeric;
BEGIN
 NEW.import_xid:=pg_current_xact_id(); NEW.imported_at:=clock_timestamp();
 IF NEW.source_end>NEW.imported_at THEN RAISE EXCEPTION 'future source observation' USING ERRCODE='23514'; END IF;
 IF NOT public.bv_json_unique(convert_from(NEW.document,'UTF8')::json) OR NOT public.bv_json_unique(convert_from(NEW.payload,'UTF8')::json) THEN RAISE EXCEPTION 'duplicate or deep JSON' USING ERRCODE='23514'; END IF;
 doc:=convert_from(NEW.document,'UTF8')::jsonb; raw:=convert_from(NEW.payload,'UTF8')::jsonb;
 IF jsonb_typeof(raw) IS DISTINCT FROM 'array' OR jsonb_array_length(raw)=0
 OR doc->>'period' IS DISTINCT FROM NEW.period OR doc->>'symbol' IS DISTINCT FROM 'SPY'
 OR doc->>'field' IS DISTINCT FROM 'adjClose' OR jsonb_array_length(doc->'rows') IS DISTINCT FROM 13
 OR doc#>>'{provenance,response_sha256}' IS DISTINCT FROM NEW.payload_sha256
 OR doc->>'status' IS DISTINCT FROM 'UNAPPLIED_UNAPPROVED_CANDIDATE'
 OR (doc#>>'{provenance,retrieval_start}')::timestamptz IS DISTINCT FROM NEW.source_start
 OR (doc#>>'{provenance,retrieval_end}')::timestamptz IS DISTINCT FROM NEW.source_end
 OR doc#>>'{provenance,request_endpoint}' IS DISTINCT FROM 'https://financialmodelingprep.com/stable/historical-price-eod/dividend-adjusted'
 OR doc#>>'{provenance,request_scope,symbol}' IS DISTINCT FROM 'SPY'
 OR doc#>>'{provenance,request_scope,from}' IS DISTINCT FROM NEW.grid[1]::text
 OR doc#>>'{provenance,request_scope,to}' IS DISTINCT FROM NEW.grid[13]::text
 OR doc#>>'{provenance,code_commit}' IS DISTINCT FROM '2b1a66b580d2fa5708db1e32594d99e181c2a9ec'
 OR doc#>>'{provenance,code_tree}' IS DISTINCT FROM '81a609372f65e03e647caa9f90ca3a79772a2b7e'
 THEN RAISE EXCEPTION 'invalid benchmark document' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(raw) r WHERE jsonb_typeof(r)<>'object' OR NOT (r ?& ARRAY['date','adjClose']) OR (r ? 'symbol' AND r->>'symbol' IS DISTINCT FROM 'SPY') OR (r->>'date' ~ '^\d{4}-\d{2}-\d{2}$') IS NOT TRUE OR (r->>'adjClose')::numeric<=0 OR (r->>'adjClose')::numeric IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric) OR r->>'adjClose' IS NULL) THEN RAISE EXCEPTION 'invalid raw benchmark row' USING ERRCODE='23514'; END IF;
 FOR i IN 1..13 LOOP
  required_date:=NEW.grid[i]; n:=NEW.levels[i];
  IF date_trunc('month',required_date)::date <> (to_date(NEW.period||'-01','YYYY-MM-DD')-(14-i)*interval '1 month')::date
   OR n<=0 OR n IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)
   OR (doc->'rows'->(i-1)->>'date')::date IS DISTINCT FROM required_date
   OR (doc->'rows'->(i-1)->>'adjClose')::numeric IS DISTINCT FROM n
   OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(raw) r WHERE (r->>'date')::date=required_date AND (r->>'adjClose')::numeric=n)
   OR EXISTS(SELECT 1 FROM jsonb_array_elements(raw) r WHERE (r->>'date')::date=required_date AND (r->>'adjClose')::numeric IS DISTINCT FROM n)
  THEN RAISE EXCEPTION 'invalid benchmark grid' USING ERRCODE='23514'; END IF;
 END LOOP;
 RETURN NEW;
END $$;
CREATE FUNCTION public.bv_value() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE h public.benchmark_vintages; pos integer;
BEGIN
 SELECT * INTO STRICT h FROM public.benchmark_vintages WHERE vintage_id=NEW.vintage_id;
 pos:=array_position(h.grid,NEW.asof);
 IF h.import_xid<>pg_current_xact_id() OR pos IS NULL OR h.levels[pos] IS DISTINCT FROM NEW.tr
 THEN RAISE EXCEPTION 'sealed or mismatched benchmark value' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION public.bv_complete() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF (SELECT count(*) FROM public.benchmark_vintage_values WHERE vintage_id=NEW.vintage_id)<>13
 THEN RAISE EXCEPTION 'incomplete benchmark import' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
CREATE FUNCTION public.bv_select() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 NEW.selection_xid:=pg_current_xact_id(); NEW.selected_at:=clock_timestamp();
 IF NOT EXISTS(SELECT 1 FROM public.benchmark_vintages WHERE vintage_id=NEW.vintage_id AND period=NEW.period AND import_xid<>pg_current_xact_id())
 OR (NEW.predecessor IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.benchmark_vintage_selections WHERE event_id=NEW.predecessor AND period=NEW.period AND selection_xid<>pg_current_xact_id()))
 THEN RAISE EXCEPTION 'committed predecessor/import required' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER bv_header BEFORE INSERT ON public.benchmark_vintages FOR EACH ROW EXECUTE FUNCTION public.bv_header();
CREATE TRIGGER bv_value BEFORE INSERT ON public.benchmark_vintage_values FOR EACH ROW EXECUTE FUNCTION public.bv_value();
CREATE CONSTRAINT TRIGGER bv_complete AFTER INSERT ON public.benchmark_vintages DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.bv_complete();
CREATE TRIGGER bv_select BEFORE INSERT ON public.benchmark_vintage_selections FOR EACH ROW EXECUTE FUNCTION public.bv_select();
CREATE TRIGGER bv_header_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON public.benchmark_vintages FOR EACH STATEMENT EXECUTE FUNCTION public.bv_immutable();
CREATE TRIGGER bv_values_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON public.benchmark_vintage_values FOR EACH STATEMENT EXECUTE FUNCTION public.bv_immutable();
CREATE TRIGGER bv_selections_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON public.benchmark_vintage_selections FOR EACH STATEMENT EXECUTE FUNCTION public.bv_immutable();
"""
