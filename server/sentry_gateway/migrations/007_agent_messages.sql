-- Phase 3: the gated inter-agent messaging channel.
-- An agent may message another agent autonomously, but ONLY across an
-- allow-listed (sender -> recipient) profile pair, and only the redacted message
-- crosses — never memory, sessions, or context. The Gateway is the trust
-- boundary; there is no direct profile-to-profile path.

-- Allow-list of ordered pairs. Absence = no crossing (fail closed).
CREATE TABLE IF NOT EXISTS agent_message_grants (
    sender_profile_id    UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    recipient_profile_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    created_by           UUID REFERENCES users(id),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (sender_profile_id, recipient_profile_id),
    CHECK (sender_profile_id <> recipient_profile_id)
);

-- Delivered messages. The body is redacted at write time; the raw message is
-- never stored, so a database dump never reveals a sender's private content.
CREATE TABLE IF NOT EXISTS agent_messages (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sender_profile_id    UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    recipient_profile_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    body_redacted        TEXT NOT NULL,
    correlation_id       TEXT NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    read_at              TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS agent_messages_inbox
    ON agent_messages(recipient_profile_id, created_at DESC);
