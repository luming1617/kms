"""权威层 DDL（A.1–A.5）与运营表；全部幂等。"""

from __future__ import annotations

import asyncpg

ICS_SCOPE_TOKEN_ARR_OK = """
CREATE OR REPLACE FUNCTION ics_scope_token_arr_ok(arr text[])
RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
SELECT COALESCE(
    bool_and(t IS NOT NULL AND btrim(t) <> '' AND t !~ '\\s' AND length(t) <= 128), true
)
FROM unnest(arr) AS t;
$$;
"""

FN_KV_CONTENT_OK = """
CREATE OR REPLACE FUNCTION fn_kv_content_ok(content jsonb)
RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
SELECT jsonb_typeof(content) = 'object'
   AND btrim(COALESCE(content->>'title', '')) <> ''
   AND jsonb_typeof(content->'sections') = 'array'
   AND jsonb_array_length(content->'sections') >= 1;
$$;
"""

FN_KV_ANSWER_POLICY_OK = """
CREATE OR REPLACE FUNCTION fn_kv_answer_policy_ok(answer_policy jsonb)
RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
SELECT jsonb_typeof(answer_policy) = 'object';
$$;
"""

FN_KV_QUALITY_OK = """
CREATE OR REPLACE FUNCTION fn_kv_quality_ok(quality jsonb)
RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
SELECT jsonb_typeof(quality) = 'object';
$$;
"""

FN_KV_CHAIN_OK = """
CREATE OR REPLACE FUNCTION fn_kv_chain_ok(review_chain jsonb)
RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
SELECT jsonb_typeof(review_chain) = 'array'
   AND NOT EXISTS (
       SELECT 1 FROM jsonb_array_elements(review_chain) AS item
       WHERE jsonb_typeof(item) <> 'object'
          OR btrim(COALESCE(item->>'role', '')) = ''
          OR COALESCE(item->>'status', '') NOT IN ('pending', 'approved', 'rejected')
   );
$$;
"""

FN_KV_CHAIN_ALL_APPROVED = """
CREATE OR REPLACE FUNCTION fn_kv_chain_all_approved(review_chain jsonb)
RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
SELECT jsonb_typeof(review_chain) = 'array'
   AND jsonb_array_length(review_chain) > 0
   AND NOT EXISTS (
       SELECT 1 FROM jsonb_array_elements(review_chain) AS item
       WHERE COALESCE(item->>'status', '') <> 'approved'
   );
$$;
"""

FN_KV_CHAIN_HAS = """
CREATE OR REPLACE FUNCTION fn_kv_chain_has(review_chain jsonb, role_name text)
RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
SELECT EXISTS (
    SELECT 1 FROM jsonb_array_elements(review_chain) AS item
    WHERE item->>'role' = role_name
);
$$;
"""

KNOWLEDGE_DDL = """
CREATE TABLE IF NOT EXISTS knowledge (
    kid text NOT NULL PRIMARY KEY,
    biz_key text NOT NULL,
    title text NOT NULL,
    schema_version text NOT NULL DEFAULT 'v1',
    current_version int NOT NULL DEFAULT 1,
    publish_state text NOT NULL DEFAULT 'DRAFT',
    language text NOT NULL,
    product text[] NOT NULL,
    year text[] NOT NULL,
    security_level text[] NOT NULL,
    asset text[] NOT NULL DEFAULT '{}',
    region text[] NOT NULL DEFAULT '{}',
    verification_max text NOT NULL DEFAULT 'V1',
    action_risk_level text NOT NULL DEFAULT 'R1',
    valid_from timestamptz NOT NULL,
    valid_to timestamptz NULL,
    family text NULL,
    locale text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_k_publish CHECK (
        publish_state IN ('DRAFT','IN_REVIEW','PUBLISHED','DEPRECATED','ROLLBACK_PENDING')
    ),
    CONSTRAINT ck_k_vmax CHECK (verification_max IN ('V0','V1','V2','V3')),
    CONSTRAINT ck_k_risk CHECK (action_risk_level IN ('R0','R1','R2','R3','R4')),
    CONSTRAINT ck_k_win CHECK (valid_to IS NULL OR valid_to > valid_from),
    CONSTRAINT ck_k_lang CHECK (language = ANY (ARRAY['zh-CN','en-US','ja-JP']::text[])),
    CONSTRAINT ck_k_auth CHECK (
        cardinality(product) >= 1 AND cardinality(year) >= 1 AND cardinality(security_level) >= 1
    ),
    CONSTRAINT ck_k_scope CHECK (
        ics_scope_token_arr_ok(product) AND ics_scope_token_arr_ok(year)
        AND ics_scope_token_arr_ok(security_level) AND ics_scope_token_arr_ok(asset)
        AND ics_scope_token_arr_ok(region)
    ),
    CONSTRAINT ck_k_biz CHECK (btrim(biz_key) <> '' AND biz_key !~ '[\\r\\n]'),
    CONSTRAINT uq_k_biz UNIQUE (biz_key)
);

CREATE INDEX IF NOT EXISTS idx_k_pub ON knowledge (valid_from) WHERE publish_state = 'PUBLISHED';
CREATE INDEX IF NOT EXISTS idx_k_gin
    ON knowledge USING gin (product, year, security_level, asset, region);
"""

KNOWLEDGE_VERSION_DDL = """
CREATE TABLE IF NOT EXISTS knowledge_version (
    kid text NOT NULL,
    version int NOT NULL,
    answer_hash text NOT NULL,
    topic text NOT NULL,
    intent_refs text[] NOT NULL DEFAULT '{}',
    content jsonb NOT NULL,
    answer_policy jsonb NOT NULL,
    quality jsonb NOT NULL,
    review_chain jsonb NOT NULL,
    risk_level text NOT NULL DEFAULT 'low',
    timeline_required boolean NOT NULL DEFAULT false,
    timeline_ref text NULL,
    source_refs text[] NOT NULL,
    owner text NOT NULL,
    change_reason text NOT NULL,
    refs jsonb NOT NULL,
    author_id text NOT NULL,
    reviewer_id text NOT NULL,
    is_current boolean NOT NULL DEFAULT false,
    published_at timestamptz NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_kv PRIMARY KEY (kid, version),
    CONSTRAINT fk_kv_kid FOREIGN KEY (kid) REFERENCES knowledge (kid),
    CONSTRAINT ck_kv_dual CHECK (author_id <> reviewer_id),
    CONSTRAINT ck_kv_refs CHECK (jsonb_array_length(refs) >= 1),
    CONSTRAINT ck_kv_content CHECK (fn_kv_content_ok(content)),
    CONSTRAINT ck_kv_ap CHECK (fn_kv_answer_policy_ok(answer_policy)),
    CONSTRAINT ck_kv_quality CHECK (fn_kv_quality_ok(quality)),
    CONSTRAINT ck_kv_chain CHECK (fn_kv_chain_ok(review_chain)),
    CONSTRAINT ck_kv_chain_pub CHECK (
        published_at IS NULL
        OR (jsonb_array_length(review_chain) >= 2 AND fn_kv_chain_all_approved(review_chain))
    ),
    CONSTRAINT ck_kv_risk CHECK (risk_level IN ('low','medium','high')),
    CONSTRAINT ck_kv_chain_cmp CHECK (
        published_at IS NULL OR risk_level <> 'high' OR fn_kv_chain_has(review_chain, 'compliance')
    ),
    CONSTRAINT ck_kv_src CHECK (
        cardinality(source_refs) >= 1 AND ics_scope_token_arr_ok(source_refs)
    ),
    CONSTRAINT ck_kv_owner CHECK (
        btrim(owner) <> '' AND btrim(change_reason) <> '' AND btrim(topic) <> ''
    ),
    CONSTRAINT ck_kv_timeline CHECK (
        timeline_ref IS NULL OR timeline_ref ~ '^TL:[-A-Za-z0-9_.:@]{1,64}$'
    ),
    CONSTRAINT ck_kv_timeline_req CHECK (timeline_required = false OR timeline_ref IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_kv_current ON knowledge_version (kid) WHERE is_current = true;
CREATE INDEX IF NOT EXISTS idx_kv_content ON knowledge_version USING gin (content jsonb_path_ops);
CREATE INDEX IF NOT EXISTS idx_kv_chain
    ON knowledge_version USING gin (review_chain jsonb_path_ops);
CREATE INDEX IF NOT EXISTS idx_kv_src ON knowledge_version USING gin (source_refs);
"""

SCOPE_SIG_FUNCTIONS = """
CREATE OR REPLACE FUNCTION ics_norm_tokens(arr text[])
RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
SELECT COALESCE(array_to_string(ARRAY(SELECT x FROM unnest(arr) AS x ORDER BY x), '|'), '');
$$;

CREATE OR REPLACE FUNCTION ics_scope_sig(
    product text[], year text[], security_level text[], publish_state text,
    asset text[], region text[]
) RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
SELECT encode(
    sha256(
        convert_to(
            ics_norm_tokens(product) || ';' || ics_norm_tokens(year) || ';'
            || ics_norm_tokens(security_level) || ';' || publish_state || ';'
            || ics_norm_tokens(asset) || ';' || ics_norm_tokens(region),
            'UTF8'
        )
    ),
    'hex'
);
$$;

CREATE OR REPLACE FUNCTION ics_cf_scope_sig() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.scope_sig := ics_scope_sig(
        NEW.product, NEW.year, NEW.security_level, NEW.publish_state, NEW.asset, NEW.region
    );
    RETURN NEW;
END;
$$;
"""

CHUNK_FILTER_DDL = """
CREATE TABLE IF NOT EXISTS chunk_filter (
    chunk_id text NOT NULL,
    kid text NOT NULL,
    kid_version int NOT NULL,
    product text[] NOT NULL,
    year text[] NOT NULL,
    security_level text[] NOT NULL,
    asset text[] NOT NULL DEFAULT '{}',
    region text[] NOT NULL DEFAULT '{}',
    publish_state text NOT NULL,
    valid_from timestamptz NOT NULL,
    valid_to timestamptz NULL,
    scope_sig text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_cf PRIMARY KEY (chunk_id, kid_version),
    CONSTRAINT fk_cf_kv FOREIGN KEY (kid, kid_version) REFERENCES knowledge_version (kid, version),
    CONSTRAINT ck_cf_win CHECK (valid_to IS NULL OR valid_to > valid_from),
    CONSTRAINT ck_cf_auth CHECK (
        cardinality(product) >= 1 AND cardinality(year) >= 1 AND cardinality(security_level) >= 1
    ),
    CONSTRAINT ck_cf_publish CHECK (
        publish_state IN ('DRAFT','IN_REVIEW','PUBLISHED','DEPRECATED','ROLLBACK_PENDING')
    )
);

CREATE INDEX IF NOT EXISTS idx_cf_kid ON chunk_filter (kid, kid_version);
CREATE INDEX IF NOT EXISTS idx_cf_gin
    ON chunk_filter USING gin (product, year, security_level, asset, region);
CREATE INDEX IF NOT EXISTS idx_cf_window ON chunk_filter (valid_from, valid_to);

DROP TRIGGER IF EXISTS trg_cf_scope_sig ON chunk_filter;
CREATE TRIGGER trg_cf_scope_sig
BEFORE INSERT OR UPDATE ON chunk_filter
FOR EACH ROW EXECUTE FUNCTION ics_cf_scope_sig();
"""

WORKSPACE_REGISTRY_DDL = """
CREATE TABLE IF NOT EXISTS workspace_registry (
    lang text NOT NULL PRIMARY KEY,
    enabled boolean NOT NULL DEFAULT true,
    embedding_model text NOT NULL,
    embedding_dim int NOT NULL,
    corpus_generation bigint NOT NULL DEFAULT 0,
    next_boundary timestamptz NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
"""

DIM_DDL = """
CREATE TABLE IF NOT EXISTS dim_scope_value (
    dim text NOT NULL,
    code text NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    PRIMARY KEY (dim, code)
);
CREATE INDEX IF NOT EXISTS idx_dsv_dim ON dim_scope_value (dim, enabled);

CREATE TABLE IF NOT EXISTS dim_registry (
    dim text NOT NULL PRIMARY KEY,
    grouping text NOT NULL,
    CONSTRAINT ck_dr_group CHECK (grouping IN ('authorization','preference'))
);
"""

DIM_SEEDS = """
INSERT INTO dim_scope_value (dim, code) VALUES
('product','SPOT'), ('product','CONTRACT'),
('security_level','TopSecret'), ('security_level','Confidential'),
('security_level','Secret'), ('security_level','Public'),
('asset','BTC'), ('asset','ETH'), ('asset','USDT'),
('region','USA'), ('region','JP'), ('region','KR'),
('region','HK'), ('region','TW'), ('region','UK'),
('year','2024'), ('year','2025'), ('year','2026'),
('languages','zh-CN'), ('languages','en-US'), ('languages','ja-JP')
ON CONFLICT DO NOTHING;

INSERT INTO dim_registry (dim, grouping) VALUES
('product','authorization'), ('year','authorization'), ('security_level','authorization'),
('asset','preference'), ('region','preference')
ON CONFLICT DO NOTHING;
"""

CONFLICT_QUEUE_DDL = """
CREATE TABLE IF NOT EXISTS conflict_review_queue (
    id bigserial PRIMARY KEY,
    kid text NOT NULL,
    language text NOT NULL,
    conflicts text[] NOT NULL,
    status text NOT NULL DEFAULT 'PENDING',
    created_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz NULL,
    resolution text NULL
);
CREATE INDEX IF NOT EXISTS idx_crq_status ON conflict_review_queue (status, created_at);
"""

PUBLISH_FREEZE_DDL = """
CREATE TABLE IF NOT EXISTS workspace_publish_freeze (
    lang text NOT NULL PRIMARY KEY,
    frozen boolean NOT NULL DEFAULT false,
    reason text NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
"""

DDL_STATEMENTS: tuple[str, ...] = (
    ICS_SCOPE_TOKEN_ARR_OK,
    FN_KV_CONTENT_OK,
    FN_KV_ANSWER_POLICY_OK,
    FN_KV_QUALITY_OK,
    FN_KV_CHAIN_OK,
    FN_KV_CHAIN_ALL_APPROVED,
    FN_KV_CHAIN_HAS,
    KNOWLEDGE_DDL,
    KNOWLEDGE_VERSION_DDL,
    SCOPE_SIG_FUNCTIONS,
    CHUNK_FILTER_DDL,
    WORKSPACE_REGISTRY_DDL,
    DIM_DDL,
    DIM_SEEDS,
    CONFLICT_QUEUE_DDL,
    PUBLISH_FREEZE_DDL,
)


async def apply_authority_schema(connection: asyncpg.Connection) -> None:
    for statement in DDL_STATEMENTS:
        await connection.execute(statement)
