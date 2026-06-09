CREATE TABLE IF NOT EXISTS tenants (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS drawings (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    source_uri TEXT NOT NULL,
    source_format TEXT NOT NULL CHECK (source_format IN ('DWG', 'DXF', 'PDF', 'SVG')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS balloons (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    drawing_id UUID NOT NULL REFERENCES drawings(id),
    label TEXT NOT NULL,
    geometry JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS revisions (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    drawing_id UUID NOT NULL REFERENCES drawings(id),
    revision_number INTEGER NOT NULL,
    change_summary TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, drawing_id, revision_number)
);

CREATE TABLE IF NOT EXISTS translation_jobs (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    source_uri TEXT NOT NULL,
    target_format TEXT NOT NULL,
    status TEXT NOT NULL,
    output_uri TEXT NOT NULL,
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS geometry_extractions (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    drawing_id UUID NOT NULL REFERENCES drawings(id),
    revision_id UUID NULL REFERENCES revisions(id),
    features JSONB NOT NULL,
    extracted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ai_balloon_suggestions (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    drawing_id UUID NOT NULL REFERENCES drawings(id),
    max_suggestions INTEGER NOT NULL,
    suggestions JSONB NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
