# Reachable-model routing and a truthful model picker

**Status:** design accepted 2026-08-18. Project **A** of three (see Scope).

## The problem

The WebUI model picker was doing two dishonest things at once:

1. **Offering models that cannot answer.** The Gateway has no model-selection
   concept anywhere in `app/` — the only `"model"` it sends to Hermes is
   `instance.profile_name`, an addressing label. Which model answers a turn is
   decided solely by `model.provider` / `model.default` in that profile's
   `config.yaml`. So every option in the picker routed to the same place.
2. **Hiding the model that can.** `get_available_models()` discovers by reading
   Hermes' `config.yaml`, which is deliberately absent from the WebUI container
   (the WebUI never mounts Hermes' home). Discovery therefore fell through to a
   hardcoded OpenRouter-style catalogue — a list of the world, not of this
   deployment. Local Qwen appeared nowhere.

## The mechanism we already had

Hermes' `api_server` supports a `model_routes` block, and `GET /v1/models`
advertises `hermes-agent` **plus every configured alias**. An incoming `model`
field matching an alias is routed to that alias' provider/model/base_url.

Config path: `platforms.api_server.extra.model_routes`. Host/port/key stay on
`API_SERVER_*` env (compose); adding this block does not disturb them.

**So the route table IS the integration registry.** A provider with no route
cannot be selected; adding a route publishes it everywhere at once. This is
exactly the "only integrated models show up" rule, with no second registry to
keep in sync — and no catalogue of models we cannot reach.

Verified on this host before designing anything further:

```
/v1/models -> ['hermes-agent', 'qwen3.6-35b-local', 'gpt-5.4']
qwen3.6-35b-local -> "Model: /models/Qwen3.6-...gguf - Provider: custom"
gpt-5.4           -> "I'm running as gpt-5.4 via the openai-api provider"
```

Two aliases, two genuinely different backends.

## Design

Three layers, each a thin pass-through of the layer below. No new registry.

### 1. Hermes — declare what is reachable

`model_routes` in the profile's live config (`data/hermes/<slug>/config.yaml`,
which is what Hermes actually reads — the repo's `hermes/config.yaml` is only a
build-time seed):

- `qwen3.6-35b-local` — provider `custom`, `base_url http://llama:8080/v1`
- `gpt-5.4` — provider `openai-api`, **no `api_key`**, so the credential
  resolves through the provider chain from `OPENAI_API_KEY` rather than being
  duplicated into a config file.

### 2. Gateway — forward the choice, publish the list

- `RuntimeTurn` gains optional `model: str | None`.
- `POST /api/chat` accepts an optional `model`; it is **validated against what
  Hermes advertises and rejected with 400 if unknown**. Silently falling back to
  the default is precisely the "picker lies" failure this project exists to fix.
- `HermesRuntime` sends `request.model or instance.profile_name`, so an absent
  model keeps today's exact behaviour.
- `GET /api/models` proxies the caller's profile `/v1/models`, short-cached.

### 3. WebUI — show only what the Gateway reports

In the sentry dialect, `/api/models` returns the Gateway's list instead of
`get_available_models()`. The hardcoded catalogue is not consulted at all: an
empty list must render as empty, never as a fallback menu of unreachable
providers.

## Consequences

- Connecting a provider = adding a route. It then appears in the picker with no
  UI change, which is the growth path the operator asked for.
- Subscription-backed providers (Claude, ChatGPT) become routes in project B;
  nothing in A needs revisiting to accommodate them.
- Failure mode is honest: no route means no option, rather than an option that
  quietly answers from somewhere else.

## Scope

This document covers **A** only.

- **B** — subscription onboarding (Claude/ChatGPT sub becomes a route).
- **C** — daily import pipeline (sessions/transcripts, catalogues, usage/spend).

B and C each get their own design. A deliberately lands first because it defines
the route contract both of them feed.
