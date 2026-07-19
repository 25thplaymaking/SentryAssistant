import pytest

from app.audit.service import AuditEvent, AuditService, Decision, evidence_hash, redact


class FakeConn:
    def __init__(self) -> None:
        self.rows: list[tuple] = []

    async def execute(self, query: str, *args):
        self.rows.append(args)
        return "INSERT 0 1"


class TestRedaction:
    """Audit rows carry a hash and a redacted summary, never the payload."""

    @pytest.mark.parametrize(
        "detail",
        [
            "user password is hunter2",
            "Authorization: Bearer abc.def",
            "OPENAI_API_KEY=sk-proj-xyz",
            "refresh_token rotated to xyz",
            "-----BEGIN PRIVATE_KEY-----",
        ],
    )
    def test_credential_shaped_detail_is_dropped(self, detail):
        assert redact(detail) == "[redacted: detail referenced a credential]"

    def test_ordinary_detail_survives(self):
        assert redact("assigned to node-1 for workspace ws-sentry").startswith("assigned")

    def test_detail_is_truncated(self):
        assert len(redact("a" * 5000)) == 500


class TestEvidenceHash:
    def test_hash_is_stable_regardless_of_key_order(self):
        assert evidence_hash({"a": 1, "b": 2}) == evidence_hash({"b": 2, "a": 1})

    def test_different_evidence_hashes_differently(self):
        assert evidence_hash({"a": 1}) != evidence_hash({"a": 2})

    def test_hash_does_not_contain_the_payload(self):
        digest = evidence_hash({"secret": "hunter2"})
        assert "hunter2" not in digest
        assert len(digest) == 64


class TestAuditWrite:
    async def test_event_is_written_with_redacted_detail(self):
        conn = FakeConn()
        await AuditService._write(
            conn,
            AuditEvent(
                action="workorder.assign",
                decision=Decision.ALLOWED,
                correlation_id="corr-1",
                detail="assigned to node-1",
                evidence={"node": "node-1"},
            ),
        )
        args = conn.rows[0]
        assert "workorder.assign" in args
        assert "allowed" in args
        assert "assigned to node-1" in args

    async def test_credential_detail_never_reaches_the_row(self):
        conn = FakeConn()
        await AuditService._write(
            conn,
            AuditEvent(
                action="connector.grant",
                decision=Decision.ALLOWED,
                correlation_id="corr-2",
                detail="stored api_key sk-proj-secret",
            ),
        )
        assert "sk-proj-secret" not in str(conn.rows[0])

    async def test_denied_decisions_are_recorded_too(self):
        conn = FakeConn()
        await AuditService._write(
            conn,
            AuditEvent(
                action="workorder.assign",
                decision=Decision.DENIED,
                correlation_id="corr-3",
                detail="observer attempted dispatch",
            ),
        )
        assert "denied" in conn.rows[0]
