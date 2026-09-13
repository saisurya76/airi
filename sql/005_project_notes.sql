-- Phase 3: an append-only comment/notes history per project.
--
-- Deliberately no `updated_at` or edit endpoint — a note is a timestamped
-- entry in a history, not a document you revise in place (matching the
-- original spec: "save comments and also history of comments with date
-- in a readonly list box"). The only mutation is delete, gated by the
-- same app-key confirmation as every other destructive action in this
-- feature (see api.py:_require_app_key_confirmed).

CREATE TABLE IF NOT EXISTS project_notes (
    id         BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    body       TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS project_notes_lookup_idx
    ON project_notes (project_id, created_at DESC);

-- Next up (Phase 4): 006 — nothing new expected at the schema level for
-- the comparison tab (it reads from tables that already exist); PDF
-- signing uses the session's own email, so no new column is needed there
-- either — see docs/WORKSPACES.md.
