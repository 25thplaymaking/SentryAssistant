"""The operator-facing wrapper must expose the password handover.

DEFECT this pins: deploy/linux/provision-teammate.sh is the whole "add a
teammate" path, and it called scripts/provision_teammate.py with only
--display-name/--slug/--hermes-api-key. The python script now sets a username
and password, but the wrapper offered no way to choose them and its closing
message still told the operator that the enrollment code was how the teammate
signs in. The one document an operator actually reads would have kept describing
the old, worse handover.
"""

from pathlib import Path

WRAPPER = (
    Path(__file__).resolve().parents[3] / "deploy" / "linux" / "provision-teammate.sh"
)


def _text() -> str:
    return WRAPPER.read_text(encoding="utf-8")


def test_wrapper_accepts_a_username_and_password():
    text = _text()
    assert "--username)" in text
    assert "--password)" in text


def test_wrapper_forwards_them_to_the_gateway_script():
    """They must reach provision_teammate.py, not merely be accepted and
    dropped -- an option that parses and does nothing is worse than no option."""
    text = _text()
    assert 'PROVISION_ARGS+=(--username "$USERNAME")' in text
    assert 'PROVISION_ARGS+=(--password "$PASSWORD")' in text
    assert 'provision_teammate.py "${PROVISION_ARGS[@]}"' in text


def test_wrapper_documents_the_new_handover():
    """The closing message is what an operator copies from; it has to describe
    the door that is now the normal one."""
    text = _text()
    tail = text.split("Done.", 1)[1].lower()
    assert "username" in tail and "password" in tail


def test_wrapper_still_mentions_the_enrollment_code():
    """Codes remain the device-pairing path; dropping them from the docs would
    lose the only way in for someone with no password."""
    assert "enrollment code" in _text().lower()
