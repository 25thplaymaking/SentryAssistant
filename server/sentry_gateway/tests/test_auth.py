import json
from datetime import timedelta

import pytest

from app.auth.tokens import (
    Audience,
    DeviceKind,
    TokenError,
    TokenService,
    create_enrollment_code,
    hash_secret,
)
from app.auth.work_order_signing import (
    NodeExpectation,
    sign_work_order,
    validate_work_order,
)
from app.routes.auth import _access_audience_for_device_kind
from app.workorders.transitions import WorkOrderMode

KEY = "test-signing-key-not-a-real-secret-padded-to-32-bytes-minimum"

NODE = NodeExpectation(
    node_id="node-1",
    node_owner_user_id="bryce",
    registered_workspaces=frozenset({"ws-sentry", "ws-25vid"}),
    allowed_harnesses=frozenset({"codex", "claude"}),
    team_members=frozenset({"bryce", "colleague"}),
)


def make_order(**overrides):
    params = dict(
        signing_key=KEY,
        work_order_id="wo-1",
        requesting_user_id="bryce",
        profile_id="p-1",
        team_id=None,
        execution_node_id="node-1",
        workspace_id="ws-sentry",
        harness="codex",
        mode=WorkOrderMode.READ_ONLY,
        correlation_id="corr-1",
    )
    params.update(overrides)
    return sign_work_order(**params)


class TestEnrollment:
    def test_code_is_single_use_and_short_lived(self):
        code = create_enrollment_code("bryce", DeviceKind.PHONE)
        assert code.expires_at > code.expires_at - timedelta(minutes=5)
        assert not code.is_expired()
        assert code.code_hash == hash_secret(code.code)

    def test_codes_are_unique(self):
        codes = {create_enrollment_code("bryce", DeviceKind.PHONE).code for _ in range(50)}
        assert len(codes) == 50

    def test_plaintext_code_is_not_recoverable_from_hash(self):
        code = create_enrollment_code("bryce", DeviceKind.DESKTOP)
        assert code.code not in code.code_hash


class TestTokens:
    def test_execution_node_refresh_keeps_node_audience(self):
        assert (
            _access_audience_for_device_kind(DeviceKind.EXECUTION_NODE.value)
            is Audience.NODE
        )
        assert _access_audience_for_device_kind(DeviceKind.DESKTOP.value) is Audience.CLIENT

    def test_access_token_round_trips(self):
        svc = TokenService(KEY)
        token = svc.issue_access_token(
            user_id="bryce", device_id="d-1", profile_id="p-1", audience=Audience.CLIENT
        )
        claims = svc.verify(token, audience=Audience.CLIENT)
        assert claims["sub"] == "bryce"
        assert claims["did"] == "d-1"
        assert claims["pid"] == "p-1"

    def test_client_token_is_rejected_at_the_node_audience(self):
        """A stolen client token must not work against the node API."""
        svc = TokenService(KEY)
        token = svc.issue_access_token(
            user_id="bryce", device_id="d-1", profile_id="p-1", audience=Audience.CLIENT
        )
        with pytest.raises(TokenError, match="audience"):
            svc.verify(token, audience=Audience.NODE)

    def test_revoked_device_is_refused_immediately(self):
        svc = TokenService(KEY)
        token = svc.issue_access_token(
            user_id="bryce", device_id="d-1", profile_id="p-1", audience=Audience.CLIENT
        )
        svc.verify(token, audience=Audience.CLIENT)
        svc.revoke_device("d-1")
        with pytest.raises(TokenError, match="revoked"):
            svc.verify(token, audience=Audience.CLIENT)

    def test_token_signed_with_another_key_is_refused(self):
        issued = TokenService(KEY).issue_access_token(
            user_id="bryce", device_id="d-1", profile_id="p-1", audience=Audience.CLIENT
        )
        with pytest.raises(TokenError):
            TokenService("a-different-key-also-padded-to-the-32-byte-minimum-length").verify(issued, audience=Audience.CLIENT)

    def test_nonce_cannot_be_reused(self):
        svc = TokenService(KEY)
        svc.consume_nonce("n-1")
        with pytest.raises(TokenError, match="already been used"):
            svc.consume_nonce("n-1")


class TestWorkOrderValidation:
    def test_valid_order_is_accepted_once(self):
        order = make_order()
        seen: set[str] = set()
        claims = validate_work_order(
            token=order.token, signing_key=KEY, expectation=NODE, seen_nonces=seen
        )
        assert claims["wid"] == "wo-1"
        assert order.nonce in seen

    def test_native_runtime_options_are_integrity_protected_in_the_signed_order(self):
        options = {
            "action": "review",
            "collaboration_mode": "plan",
            "sandbox": "readOnly",
            "effort": "high",
        }
        order = make_order(runtime_options=options)
        claims = validate_work_order(
            token=order.token, signing_key=KEY, expectation=NODE, seen_nonces=set()
        )

        assert json.loads(claims["ropts"]) == options

    def test_replayed_order_is_refused(self):
        order = make_order()
        seen: set[str] = set()
        validate_work_order(
            token=order.token, signing_key=KEY, expectation=NODE, seen_nonces=seen
        )
        with pytest.raises(TokenError, match="nonce"):
            validate_work_order(
                token=order.token, signing_key=KEY, expectation=NODE, seen_nonces=seen
            )

    def test_expired_order_is_refused(self):
        order = make_order(ttl=timedelta(seconds=-1))
        with pytest.raises(TokenError, match="expired"):
            validate_work_order(
                token=order.token, signing_key=KEY, expectation=NODE, seen_nonces=set()
            )

    def test_order_for_another_node_is_refused(self):
        """Cross-user node selection must fail even with a valid signature."""
        order = make_order(execution_node_id="node-2")
        with pytest.raises(TokenError, match="different execution node"):
            validate_work_order(
                token=order.token, signing_key=KEY, expectation=NODE, seen_nonces=set()
            )

    def test_unregistered_workspace_is_refused(self):
        order = make_order(workspace_id="C:/Users/Bryce/Secrets")
        with pytest.raises(TokenError, match="not registered"):
            validate_work_order(
                token=order.token, signing_key=KEY, expectation=NODE, seen_nonces=set()
            )

    def test_disabled_harness_is_refused(self):
        order = make_order(harness="grok-build")
        with pytest.raises(TokenError, match="not enabled"):
            validate_work_order(
                token=order.token, signing_key=KEY, expectation=NODE, seen_nonces=set()
            )

    def test_elevated_order_from_non_owner_is_refused(self):
        order = make_order(
            requesting_user_id="colleague", mode=WorkOrderMode.APPROVED_ELEVATED
        )
        with pytest.raises(TokenError, match="node owner"):
            validate_work_order(
                token=order.token, signing_key=KEY, expectation=NODE, seen_nonces=set()
            )

    def test_elevated_order_from_owner_is_accepted(self):
        order = make_order(requesting_user_id="bryce", mode=WorkOrderMode.APPROVED_ELEVATED)
        validate_work_order(
            token=order.token, signing_key=KEY, expectation=NODE, seen_nonces=set()
        )

    def test_removed_team_member_is_refused(self):
        order = make_order(requesting_user_id="ex-colleague", team_id="team-1")
        with pytest.raises(TokenError, match="not a current member"):
            validate_work_order(
                token=order.token, signing_key=KEY, expectation=NODE, seen_nonces=set()
            )

    def test_forged_order_is_refused(self):
        order = make_order()
        with pytest.raises(TokenError):
            validate_work_order(
                token=order.token,
                signing_key="attacker-key-also-padded-to-the-32-byte-minimum-length-x",
                expectation=NODE,
                seen_nonces=set(),
            )
