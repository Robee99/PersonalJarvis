-- 0010_brain_map.sql
-- The project intelligence layer ("brain map"): one derived index over the
-- stores Jarvis already has (agent chat, missions, quests, automations, the
-- Agentic IDE library, mission outputs, wiki project pages and imported
-- conversations, the coding-CLI usage index).
--
-- Derived, never a second copy. A row here POINTS at its source row through
-- (source_kind, source_id[, message_ref]); the source stays the truth. The
-- only text this layer keeps is a bounded, redacted verbatim quote per piece
-- of evidence, so a derived fact can be shown with the words it came from.
--
-- Vocabulary source of truth: jarvis/memory/brain_map/constants.py; the CHECK
-- lists below are kept in step by tests/unit/memory/brain_map/test_parity.py.
--
-- CREATE ... IF NOT EXISTS throughout: BrainMapStore also executes this file
-- idempotently on a standalone open (the CandidateJournal pattern), without
-- bumping user_version.

BEGIN;

-- One row per source record the indexer has seen: a conversation, a mission,
-- a quest, an IDE project, a wiki page... `cursor` is the per-source
-- high-water mark (last event seq, last turn index, ...); `version_hash` is a
-- digest of the native row's state, so an unchanged row is skipped.
CREATE TABLE IF NOT EXISTS brain_map_sources (
    source_kind       TEXT    NOT NULL
        CHECK (source_kind IN ('agent_chat', 'goal', 'mission', 'output', 'quest',
                               'automation', 'ide_project', 'ide_thread',
                               'cli_session', 'wiki_project', 'wiki_import')),
    source_id         TEXT    NOT NULL,
    cursor            TEXT    NOT NULL DEFAULT '',
    version_hash      TEXT    NOT NULL DEFAULT '',
    cwd               TEXT    NOT NULL DEFAULT '',
    last_activity_ms  INTEGER NOT NULL DEFAULT 0,
    indexed_ms        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (source_kind, source_id)
);

-- One normalized text unit (a chat message, an imported turn). No text is
-- stored: content_hash decides whether the unit changed since it was last
-- processed, and (source_kind, source_id, message_ref) finds the text again.
CREATE TABLE IF NOT EXISTS brain_map_segments (
    segment_id         TEXT    PRIMARY KEY,
    source_kind        TEXT    NOT NULL,
    source_id          TEXT    NOT NULL,
    conversation_id    TEXT    NOT NULL DEFAULT '',
    message_ref        TEXT    NOT NULL DEFAULT '',
    message_id         TEXT    NOT NULL DEFAULT '',
    role               TEXT    NOT NULL DEFAULT '',
    ts_ms              INTEGER NOT NULL DEFAULT 0,
    content_hash       TEXT    NOT NULL,
    char_len           INTEGER NOT NULL DEFAULT 0 CHECK (char_len >= 0),
    extractor_version  TEXT    NOT NULL DEFAULT '',
    signal_count       INTEGER NOT NULL DEFAULT 0 CHECK (signal_count >= 0),
    processed_ms       INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_brain_map_segments_source
    ON brain_map_segments (source_kind, source_id);

-- Every node of the map. Native rows (a mission, a quest...) are projected
-- with confidence 1.0 and point back via (source_kind, source_id); extracted
-- rows (a decision said in a chat) carry evidence instead. `status` is always
-- derived by Python rules (status.py), never by a model.
CREATE TABLE IF NOT EXISTS brain_map_entities (
    entity_id         TEXT    PRIMARY KEY,
    kind              TEXT    NOT NULL
        CHECK (kind IN ('PROJECT', 'CONVERSATION', 'THREAD', 'TASK', 'DECISION',
                        'BLOCKER', 'MILESTONE', 'DOCUMENT', 'AGENT', 'OUTPUT')),
    title             TEXT    NOT NULL DEFAULT '',
    status            TEXT    NOT NULL DEFAULT 'blue'
        CHECK (status IN ('red', 'blue', 'yellow', 'green')),
    status_reason     TEXT    NOT NULL DEFAULT '',
    confidence        REAL    NOT NULL DEFAULT 1.0
        CHECK (confidence >= 0.0 AND confidence <= 1.0),
    origin            TEXT    NOT NULL DEFAULT 'native'
        CHECK (origin IN ('native', 'extracted', 'derived')),
    native_status     TEXT    NOT NULL DEFAULT '',
    source_kind       TEXT    NOT NULL DEFAULT '',
    source_id         TEXT    NOT NULL DEFAULT '',
    -- Where the thing lives, for a jump: a project folder, a wiki page path,
    -- an output folder. Empty for things that only live in a conversation.
    locator           TEXT    NOT NULL DEFAULT '',
    -- The rule that found an extracted entity (blocked / waiting / todo ...).
    signal            TEXT    NOT NULL DEFAULT '',
    user_priority     INTEGER NOT NULL DEFAULT 0
        CHECK (user_priority BETWEEN 0 AND 3),
    due_ms            INTEGER,
    first_seen_ms     INTEGER NOT NULL DEFAULT 0,
    last_activity_ms  INTEGER NOT NULL DEFAULT 0,
    last_updated_ms   INTEGER NOT NULL DEFAULT 0,
    -- When a time-based rule (stale, overdue) would change this status next;
    -- the indexer re-derives exactly those rows, nothing else.
    review_at_ms      INTEGER,
    priority_score    REAL    NOT NULL DEFAULT 0.0,
    priority_reason   TEXT    NOT NULL DEFAULT '',
    summary           TEXT    NOT NULL DEFAULT '',
    input_hash        TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_brain_map_entities_kind
    ON brain_map_entities (kind, status);
CREATE INDEX IF NOT EXISTS idx_brain_map_entities_source
    ON brain_map_entities (source_kind, source_id);
CREATE INDEX IF NOT EXISTS idx_brain_map_entities_review
    ON brain_map_entities (review_at_ms);

-- Typed edges. A low-confidence `part_of` is kept as assignment 'suggested'.
CREATE TABLE IF NOT EXISTS brain_map_relations (
    src_id      TEXT NOT NULL,
    kind        TEXT NOT NULL
        CHECK (kind IN ('part_of', 'mentions', 'blocks', 'decided_in', 'produced',
                        'follows_from')),
    dst_id      TEXT NOT NULL,
    confidence  REAL NOT NULL DEFAULT 1.0
        CHECK (confidence >= 0.0 AND confidence <= 1.0),
    assignment  TEXT NOT NULL DEFAULT 'confirmed'
        CHECK (assignment IN ('confirmed', 'suggested')),
    basis       TEXT NOT NULL DEFAULT '',
    updated_ms  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (src_id, kind, dst_id)
);

CREATE INDEX IF NOT EXISTS idx_brain_map_relations_dst
    ON brain_map_relations (dst_id, kind);

-- Entity -> the segment it was read from, with the verbatim (redacted,
-- bounded) quote and its character span inside that segment.
CREATE TABLE IF NOT EXISTS brain_map_evidence (
    evidence_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id        TEXT    NOT NULL,
    segment_id       TEXT    NOT NULL,
    source_kind      TEXT    NOT NULL,
    source_id        TEXT    NOT NULL,
    conversation_id  TEXT    NOT NULL DEFAULT '',
    message_ref      TEXT    NOT NULL DEFAULT '',
    message_id       TEXT    NOT NULL DEFAULT '',
    ts_ms            INTEGER NOT NULL DEFAULT 0,
    quote            TEXT    NOT NULL,
    char_start       INTEGER NOT NULL CHECK (char_start >= 0),
    char_end         INTEGER NOT NULL CHECK (char_end >= char_start),
    extractor        TEXT    NOT NULL DEFAULT '',
    confidence       REAL    NOT NULL DEFAULT 1.0
        CHECK (confidence >= 0.0 AND confidence <= 1.0),
    UNIQUE (entity_id, segment_id, char_start)
);

CREATE INDEX IF NOT EXISTS idx_brain_map_evidence_entity
    ON brain_map_evidence (entity_id);
CREATE INDEX IF NOT EXISTS idx_brain_map_evidence_segment
    ON brain_map_evidence (segment_id);

-- Every status change of an entity: the deterministic timeline.
CREATE TABLE IF NOT EXISTS brain_map_status_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id  TEXT    NOT NULL,
    ts_ms      INTEGER NOT NULL,
    status     TEXT    NOT NULL
        CHECK (status IN ('red', 'blue', 'yellow', 'green')),
    reason     TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_brain_map_status_history_entity
    ON brain_map_status_history (entity_id, ts_ms);

-- One row per indexer run: what it looked at and what it actually redid.
CREATE TABLE IF NOT EXISTS brain_map_runs (
    run_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger              TEXT    NOT NULL DEFAULT '',
    started_ms           INTEGER NOT NULL,
    finished_ms          INTEGER NOT NULL DEFAULT 0,
    sources_seen         INTEGER NOT NULL DEFAULT 0,
    sources_changed      INTEGER NOT NULL DEFAULT 0,
    segments_seen        INTEGER NOT NULL DEFAULT 0,
    segments_processed   INTEGER NOT NULL DEFAULT 0,
    entities_changed     INTEGER NOT NULL DEFAULT 0,
    projects_recomputed  INTEGER NOT NULL DEFAULT 0,
    errors               INTEGER NOT NULL DEFAULT 0
);

COMMIT;
