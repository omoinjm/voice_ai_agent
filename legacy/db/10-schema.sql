-- Voice agent records. Runs when the db volume is first created (all of db/ is mounted as docker-entrypoint-initdb.d)
-- and again on every release (scripts/deploy-server.sh), so it must stay idempotent: add new columns to the
-- CREATE TABLE for fresh databases *and* as ADD COLUMN IF NOT EXISTS at the end for existing ones.
-- The bot writes these as the call happens (agent/store.py), so a failed post-call step can't lose them.

CREATE TABLE IF NOT EXISTS calls (
    id          text PRIMARY KEY,
    channel     text NOT NULL,              -- whatsapp_call | web
    phone       text,                       -- caller's WhatsApp number (E.164 without +)
    started_at  timestamptz NOT NULL,
    ended_at    timestamptz,
    summary     text
);
CREATE INDEX IF NOT EXISTS calls_phone_idx ON calls (phone);
CREATE INDEX IF NOT EXISTS calls_started_at_idx ON calls (started_at DESC);

-- One row per spoken turn, in order.
CREATE TABLE IF NOT EXISTS call_turns (
    id           bigserial PRIMARY KEY,
    call_id      text NOT NULL REFERENCES calls (id) ON DELETE CASCADE,
    role         text NOT NULL CHECK (role IN ('caller', 'agent')),
    content      text NOT NULL,
    interrupted  boolean NOT NULL DEFAULT false,
    at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS call_turns_call_idx ON call_turns (call_id, id);

-- Every tool the agent ran, with what it sent and got back.
CREATE TABLE IF NOT EXISTS tool_calls (
    id       bigserial PRIMARY KEY,
    call_id  text NOT NULL REFERENCES calls (id) ON DELETE CASCADE,
    tool     text NOT NULL,
    input    jsonb NOT NULL,
    result   jsonb,
    at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tool_calls_call_idx ON tool_calls (call_id, id);

-- Bookings the agent confirmed to a caller (create_booking returned ok).
CREATE TABLE IF NOT EXISTS bookings (
    reference   text PRIMARY KEY,
    call_id     text REFERENCES calls (id) ON DELETE SET NULL,
    name        text NOT NULL,
    service     text NOT NULL,
    requested   text NOT NULL,              -- date/time as the caller gave it
    phone       text,
    status      text NOT NULL DEFAULT 'requested',   -- requested | cancelled
    created_at  timestamptz NOT NULL DEFAULT now(),
    rescheduled_at        timestamptz,      -- last time the caller moved it
    confirmation_sent_at  timestamptz       -- WhatsApp confirmation sent after the call (agent/notify.py)
);

-- Columns added after the first deploy (existing databases).
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS rescheduled_at timestamptz;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS confirmation_sent_at timestamptz;
