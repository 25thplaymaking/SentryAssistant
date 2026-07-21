"""Password hashing primitives.

The stored form has to survive a parameter increase: if raising the cost
invalidated every existing hash, nobody would ever raise it. These tests pin
the properties that make that possible (self-describing hash, per-hash salt)
rather than the exact cost, which is expected to grow.
"""

import pytest

from app.auth import passwords


def test_hash_never_contains_the_plaintext():
    stored = passwords.hash_password("correct horse battery staple")
    assert "correct horse battery staple" not in stored


def test_two_hashes_of_the_same_password_differ():
    """A per-hash random salt, so a stolen table cannot be attacked in bulk."""
    a = passwords.hash_password("correct horse battery staple")
    b = passwords.hash_password("correct horse battery staple")
    assert a != b


def test_hash_records_its_own_parameters():
    stored = passwords.hash_password("correct horse battery staple")
    assert stored.startswith("$argon2id$")
    # memory, time, and parallelism travel with the hash, so a future increase
    # can be applied to new hashes without locking anyone out of an old one.
    assert "m=" in stored and "t=" in stored and "p=" in stored


def test_verify_accepts_the_right_password():
    stored = passwords.hash_password("correct horse battery staple")
    assert passwords.verify_password(stored, "correct horse battery staple") is True


def test_verify_rejects_the_wrong_password():
    stored = passwords.hash_password("correct horse battery staple")
    assert passwords.verify_password(stored, "correct horse battery stapl") is False


def test_verify_rejects_a_corrupt_stored_hash_instead_of_raising():
    """A garbled row must fail closed, not 500 the login route."""
    assert passwords.verify_password("not-a-hash", "anything") is False
    assert passwords.verify_password("", "anything") is False


def test_equal_work_placeholder_is_available_for_unknown_accounts():
    # Called when the username does not exist so the timing of "no such user"
    # matches "wrong password".
    passwords.verify_dummy()  # must not raise


class TestPolicy:
    def test_minimum_length_is_at_least_twelve(self):
        assert passwords.PASSWORD_MIN_LENGTH >= 12

    def test_short_password_is_rejected_with_a_reason(self):
        problem = passwords.password_policy_error("short")
        assert problem and "12" in problem

    def test_long_enough_password_is_accepted(self):
        assert passwords.password_policy_error("a-long-enough-passphrase") is None

    def test_blank_password_is_rejected(self):
        assert passwords.password_policy_error("   ") is not None


class TestGeneratedPassword:
    def test_generated_password_satisfies_the_policy(self):
        assert passwords.password_policy_error(passwords.generate_password()) is None

    def test_generated_passwords_are_not_repeated(self):
        assert len({passwords.generate_password() for _ in range(20)}) == 20


class TestLockoutConstants:
    def test_lockout_numbers_are_sane(self):
        assert 3 <= passwords.MAX_FAILED_ATTEMPTS <= 10
        assert passwords.LOCKOUT_SECONDS >= 300


class TestThrottle:
    def test_allows_up_to_the_limit_then_refuses(self):
        throttle = passwords.AttemptThrottle(limit=3, window_seconds=60)
        clock = [1000.0]
        assert [throttle.allow("k", now=clock[0]) for _ in range(3)] == [True] * 3
        assert throttle.allow("k", now=clock[0]) is False

    def test_window_expiry_lets_the_caller_back_in(self):
        throttle = passwords.AttemptThrottle(limit=1, window_seconds=60)
        assert throttle.allow("k", now=1000.0) is True
        assert throttle.allow("k", now=1030.0) is False
        assert throttle.allow("k", now=1061.0) is True

    def test_keys_are_independent(self):
        throttle = passwords.AttemptThrottle(limit=1, window_seconds=60)
        assert throttle.allow("a", now=1000.0) is True
        assert throttle.allow("b", now=1000.0) is True


@pytest.mark.parametrize("bad", [None, 123, b"bytes"])
def test_hash_password_refuses_non_strings(bad):
    with pytest.raises((TypeError, AttributeError)):
        passwords.hash_password(bad)
