"""Publishing a subscription as a pickable model must not corrupt the config.

attach-subscription.py edits the live Hermes config as text rather than through
a YAML round-trip, because that file's comments carry the measured reasoning for
the deployment (why threads=12, why q8_0 KV is a loss) and a round-trip would
silently delete all of them. Text surgery buys that at the cost of needing these
tests: the failure mode of a bad regex here is a config that still parses but
routes somewhere unintended.
"""

import importlib.util
from pathlib import Path

import pytest
import yaml

_SCRIPT = (
    Path(__file__).resolve().parents[3] / "deploy" / "linux" / "attach-subscription.py"
)
_spec = importlib.util.spec_from_file_location("attach_subscription", _SCRIPT)
attach = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(attach)


BASE = """model:
  provider: custom
  # a comment that must survive
  default: /models/local.gguf

platforms:
  api_server:
    extra:
      model_routes:
        qwen3.6-35b-local:
          model: /models/local.gguf
          provider: custom
          base_url: http://llama:8080/v1
          api_key: local
        gpt-5.4:
          model: gpt-5.4
          provider: openai-api
"""


def _routes(text: str) -> dict:
    return yaml.safe_load(text)["platforms"]["api_server"]["extra"]["model_routes"]


class TestAddsRoute:
    def test_new_alias_is_added(self):
        out = attach.upsert_model_route(BASE, "claude-subscription", "claude-opus-4-5", "anthropic")
        routes = _routes(out)
        assert routes["claude-subscription"] == {
            "model": "claude-opus-4-5",
            "provider": "anthropic",
        }

    def test_existing_routes_survive(self):
        out = attach.upsert_model_route(BASE, "claude-subscription", "claude-opus-4-5", "anthropic")
        routes = _routes(out)
        assert set(routes) == {"qwen3.6-35b-local", "gpt-5.4", "claude-subscription"}
        assert routes["qwen3.6-35b-local"]["base_url"] == "http://llama:8080/v1"
        assert routes["gpt-5.4"]["provider"] == "openai-api"

    def test_output_is_still_valid_yaml(self):
        out = attach.upsert_model_route(BASE, "chatgpt-codex", "gpt-5.4-codex", "openai-codex")
        assert yaml.safe_load(out) is not None

    def test_comments_are_preserved(self):
        """The whole reason this is text surgery and not a YAML round-trip."""
        out = attach.upsert_model_route(BASE, "chatgpt-codex", "gpt-5.4-codex", "openai-codex")
        assert "# a comment that must survive" in out


class TestIdempotent:
    def test_reattaching_replaces_rather_than_duplicates(self):
        once = attach.upsert_model_route(BASE, "claude-subscription", "claude-opus-4-5", "anthropic")
        twice = attach.upsert_model_route(once, "claude-subscription", "claude-opus-4-5", "anthropic")
        assert once == twice
        assert twice.count("claude-subscription:") == 1

    def test_changing_the_model_updates_in_place(self):
        once = attach.upsert_model_route(BASE, "claude-subscription", "old-model", "anthropic")
        twice = attach.upsert_model_route(once, "claude-subscription", "new-model", "anthropic")
        routes = _routes(twice)
        assert routes["claude-subscription"]["model"] == "new-model"
        assert twice.count("claude-subscription:") == 1

    def test_replacing_one_alias_leaves_neighbours_intact(self):
        out = attach.upsert_model_route(BASE, "gpt-5.4", "gpt-5.5", "openai-api")
        routes = _routes(out)
        assert routes["gpt-5.4"]["model"] == "gpt-5.5"
        assert routes["qwen3.6-35b-local"]["api_key"] == "local"
        assert len(routes) == 2


class TestRefusesRatherThanGuessing:
    def test_missing_block_raises(self):
        """Inventing the block in the wrong place would route nothing, silently."""
        with pytest.raises(ValueError, match="model_routes"):
            attach.upsert_model_route("model:\n  provider: custom\n", "x", "y", "anthropic")


class TestProviderTable:
    def test_only_subscription_providers_are_offered(self):
        # openai-codex left the table when the default flipped to Nous Portal.
        assert set(attach.SUBSCRIPTION_PROVIDERS) == {"anthropic", "nous"}

    def test_each_has_a_default_alias_and_label(self):
        for meta in attach.SUBSCRIPTION_PROVIDERS.values():
            assert meta["default_alias"] and meta["label"]


class TestAuthDetection:
    """Regression: keyword-matching `auth status` prose reported logged-OUT
    providers as ready, because the logged-out message itself contains the words
    "credentials" and "authenticate". A route was nearly published to a provider
    that could not answer. These are the real strings from this deployment."""

    LOGGED_OUT_CODEX = (
        "openai-codex: logged out (No Codex credentials stored. "
        "Run `hermes auth` to authenticate.)"
    )
    LOGGED_OUT_ANTHROPIC = "anthropic: logged out"
    AUTH_LIST_REAL = (
        "openai-api (1 credentials):\n"
        "  #1  OPENAI_API_KEY       api_key env:OPENAI_API_KEY <-\n"
    )

    def test_logged_out_prose_is_not_mistaken_for_credentials(self):
        assert attach.parse_authenticated_providers(self.LOGGED_OUT_CODEX) == set()
        assert attach.parse_authenticated_providers(self.LOGGED_OUT_ANTHROPIC) == set()

    def test_real_auth_list_is_parsed(self):
        assert attach.parse_authenticated_providers(self.AUTH_LIST_REAL) == {"openai-api"}

    def test_multiple_providers_are_all_found(self):
        out = (
            "openai-api (1 credentials):\n  #1 x\n"
            "anthropic (2 credentials):\n  #1 y\n  #2 z\n"
        )
        assert attach.parse_authenticated_providers(out) == {"openai-api", "anthropic"}

    def test_hyphenated_provider_ids_survive(self):
        out = "openai-codex (1 credentials):\n  #1 oauth\n"
        assert attach.parse_authenticated_providers(out) == {"openai-codex"}

    def test_empty_and_garbage_yield_nothing(self):
        assert attach.parse_authenticated_providers("") == set()
        assert attach.parse_authenticated_providers("no credentials configured") == set()


class TestEnvTokenDetection:
    """A subscription token in the environment counts as authenticated.

    `claude setup-token` mints a long-lived subscription token that the
    anthropic provider reads straight from the env chain, so requiring an OAuth
    entry in Hermes' auth store as well would refuse a setup that works.

    The marker test exists because the first version probed with SET/UNSET and
    checked `"SET" in output` -- and "SET" is a substring of "UNSET", so an
    ABSENT token reported as authenticated. That would publish a route to a
    provider holding no credential: an option in the picker that cannot answer,
    which is the exact defect the routing design exists to prevent.
    """

    def test_anthropic_declares_its_env_token(self):
        assert attach.SUBSCRIPTION_PROVIDERS["anthropic"]["env_token"] == "CLAUDE_CODE_OAUTH_TOKEN"

    def test_probe_markers_are_not_substrings_of_each_other(self):
        import inspect
        src = inspect.getsource(attach.provider_is_authenticated)
        assert "token_present" in src and "token_absent" in src
        assert "token_present" not in "token_absent"

    def test_nous_has_no_env_token_and_must_use_oauth(self):
        assert "env_token" not in attach.SUBSCRIPTION_PROVIDERS["nous"]
