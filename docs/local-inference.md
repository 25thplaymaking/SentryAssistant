# Local inference (HOSTED MODELS)

Sentry can run its own coding model on this box instead of sending every turn to
a cloud API. This document is the operating manual for that. **Read the warning
section before starting it.**

Everything here was measured on grain.silo (Ryzen 9 7950X3D, 96 GB DDR5-5600,
no GPU) on 2026-08-18, under the box's normal background load. These are
production numbers, not vendor claims.

**Deployed state:** `Qwen3.6-35B-A3B` Q4_K_M on ik_llama.cpp, ~25 tok/s
generation, 24.2 GB resident, 64K context, tool calling verified.

---

## ⚠️ This is not a normal service

The local model is the single most resource-hungry thing this server runs. It is
not a daemon you leave on and forget.

| Resource | What it takes |
|---|---|
| **RAM** | ~24 GB resident, locked (`--mlock` pins it; never paged out) |
| **CPU** | 12 compute threads, plus workers — effectively the whole machine |
| **Memory bandwidth** | **all of it** — see below |
| **Disk** | 21 GB of weights in `/srv/sentry/models` (outside the repo) |

### The part that surprises people: memory bandwidth

CPU inference is not CPU-bound, it is **DRAM-bandwidth-bound**. This box sustains
~54.5 GB/s of read bandwidth *in total*, across everything running on it. The
model consumes essentially all of that while generating.

So **the model does not slow down gracefully when it shares the box — it slows
down everything else instead.** Game servers tick late. Anything latency-
sensitive feels it.

**You cannot fix this with pinning.** We measured it:

| Configuration | Generation |
|---|---:|
| `cpuset 0-11` | 21.7 tok/s |
| `cpuset 0-15` | 23.7 tok/s |
| **no cpuset** | **25–27 tok/s** |

A cpuset makes the model slower and protects the other services *not at all*,
because what they contend for is the memory controller, not core time. There is
deliberately no `cpuset` in `compose.yaml`.

**If you need the box back, stop the service.** Do not try to box it in.
Conversely, before a long agent run, stop the heavy game servers — `dotnet.exe`
under Wine and `PalServer-Linux` are the two big consumers.

### It does not start by default

The service sits behind a Compose profile precisely so a routine
`docker compose up -d` cannot start it:

```bash
docker compose --profile local-model up -d llama     # start deliberately
docker compose --profile local-model stop llama      # give the box back
```

Nothing depends on it. Hermes only routes to it once you switch the provider
(below), so it can be started, benchmarked and stopped without touching chat.

---

## Measured performance

### Bandwidth ceiling (everything follows from this)

| Threads | Read GB/s |
|--------:|----------:|
| 1  | 13.4 |
| 4  | 48.1 |
| 8  | 53.5 |
| **12** | **54.5 ← peak** |
| 16 | 50.3 |
| 24 | 38.9 |
| 32 | 45.0 |

Bandwidth peaks at 12 threads and **degrades past 16**. Hence `--threads 12`.
"Give it more threads" makes it slower.

**This ceiling cannot be raised.** B650D4U is AM5 — dual channel. Filling all
four DIMM slots increases capacity but forces lower memory clocks
(DDR5-3600–4400), making generation *slower*. Buy RAM for capacity only.

### Engine choice: ik_llama.cpp, not mainline

| Engine | prefill t/s | generation t/s |
|---|---:|---:|
| mainline llama.cpp | 139.9 | 14.75 |
| mainline + `--spec-type draft-mtp` | 139.9 | 19.14 |
| ik_llama `-rtr` | 382.7 | 17.45 |
| **ik_llama `-rtr --spec-type mtp`** | **382.7** | **25.99** |

2.74× prefill and 1.76× generation. Tool calling verified working on both.

### Two settings that look right and are not

Both of these were in the first draft of this deployment and both were measured
to be losses. They are called out because they are exactly what a reasonable
person would configure.

**1. `--threads-batch 24` — costs 21%.**
`llama-bench` shows prefill scaling to 24 threads (pp512: 139.9 @12 → 150.2 @24),
which makes a higher batch thread count look free. It is not, because MTP
speculative decoding runs a *batched verify on every decode step*:

| | generation |
|---|---:|
| `-t 12` | 30.92 tok/s |
| `-t 12 -tb 24` | 24.46 tok/s |

Leave `--threads-batch` unset.

**2. Quantised KV cache — costs 17%.**

| | generation |
|---|---:|
| f16 KV | 30.92 tok/s |
| q8_0 KV | 25.57 tok/s |

The usual reason to quantise KV is memory, and there is no memory pressure to
relieve: this model carries a growing KV cache on only ~10 of its 40 layers
(3:1 Gated-DeltaNet : gated-attention), so 64K of f16 KV is a few hundred MB
against ~65 GB free. `q4_0` additionally degrades tool-call accuracy. Keep f16.

### Why this model and not a "better" one

Decode speed is `54.5 GB/s ÷ bytes-read-per-token`, so **active** parameter count
decides everything. Measured at Q4_K_M:

| Model | Active | Generation |
|---|---|---:|
| Qwen3.6-35B-A3B | 3B | 14.9 t/s (25 with MTP) |
| Qwen3-Coder-Next 80B | 3B | 14.3 t/s |
| Qwen3.8-27B (dense) | 27B | **2.8 t/s** |

A big *sparse* MoE costs RAM, not speed. A dense model is unusable here no matter
how well it benchmarks — Qwen3.8-27B scores far higher on reasoning benchmarks
and would still be unusable at 2.8 tok/s.

### Prompt caching

ik_llama does **not** have mainline's `--cache-reuse`. It has something better
for this workload: a similarity-matched RAM prompt cache, enabled by default.
`-cram` is its size in MiB (set to 8192 here), `-crs` the similarity threshold
above which a cached prompt is reused (default 0.50).

This matters because Hermes' tool schemas are a measured **11,565-token floor on
every request** (see the note in `hermes/config.yaml`) — roughly 30 s of prefill
per turn if it were never cached.

---

## Switching Hermes to the local model

One block — but **edit the LIVE profile config, not the repo template**:

```
deploy/linux/data/hermes/personal/config.yaml     <- the file Hermes actually reads
deploy/linux/hermes/config.yaml                   <- build-time template, NOT read at runtime
```

`deploy/linux/hermes/config.yaml` is only a seed. Hermes reads its config from
`$HERMES_HOME`, which compose bind-mounts from `./data/hermes/<slug>` — so a
profile provisioned before a template change keeps the OLD content forever, and
editing the template changes nothing about a running profile. (Verified
2026-08-18: `personal` was still on the Jul 20 template and had never picked up
the `mcp_servers` block added to the template afterwards.)

**Provider must be `custom`**, not `openai-api` — pointing `OPENAI_BASE_URL` at
llama-server while leaving `openai-api` makes Hermes speak native-OpenAI dialect
and call `/v1/responses`, which llama-server does not implement.

```yaml
model:
  provider: custom
  base_url: http://llama:8080/v1
  # ik_llama has no --alias, so the served model id IS the file path.
  # Verify with: docker compose exec llama curl -s localhost:8080/v1/models
  default: /models/Qwen3.6-35B-A3B-MTP-UD-Q4_K_M.gguf
  api_key: local            # ignored by llama-server, required by Hermes
  context_length: 65536     # Hermes refuses anything under 64000
```

Then `docker compose restart hermes`.

MCP servers are wired into the same live file, but use the tool rather than
hand-editing — it validates entries with Hermes' own validator:

```bash
./manage-mcp.sh --slug personal --list
./manage-mcp.sh --slug personal --add server-control
```

**Rolling back is the same edit in reverse** (`provider: openai-api`,
`model: gpt-5.4`) plus a restart. The local runtime can keep running; nothing
else depends on it.

---

## Verifying it actually works

A healthy container is not proof. Tool calling is the whole job, and
llama-server silently returns tool calls as prose if `--jinja` is missing.

```bash
docker compose exec llama curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' -d '{
  "model":"/models/Qwen3.6-35B-A3B-MTP-UD-Q4_K_M.gguf","max_tokens":200,
  "messages":[{"role":"user","content":"Check free disk on /. Use the tool."}],
  "tools":[{"type":"function","function":{"name":"run_shell",
    "description":"Run a shell command",
    "parameters":{"type":"object","properties":{"cmd":{"type":"string"}},
    "required":["cmd"]}}}],"tool_choice":"auto"}' | python3 -m json.tool
```

Two things must hold:

1. `choices[0].message.tool_calls` exists — not prose in `content`.
2. `tool_calls[0].function.arguments` is a **JSON string**, not an object.
   (An object breaks the OpenAI SDK; llama.cpp #20198, fixed in #20213.)

Confirm MTP is actually engaged — it is worth ~49% and fails silently:

```bash
docker compose logs llama | grep -i mtp
#   srv init: MTP needs embeddings on decode, enabling
#   common_speculative_state_mtp: MTP context ready
#   statistics mtp: ... #gen drafts = 33, #acc drafts = 30   <- ~90% acceptance
```

## Known risk

llama.cpp issue **#24807**: Qwen3.6-35B-A3B occasionally emits malformed
tool-call XML (a duplicated `</parameter>`), and the parser then drops the whole
tool call and aborts the stream. Roughly **1 in 128 tool-using requests**. Fixed
upstream in PR #24839.

Mainline carries that fix. **Whether ik_llama has ported it is unverified** — a
single passing test does not rule it out.

**If tool calls start disappearing mid-session:** rebuild `llama/Dockerfile`
from `ggml-org/llama.cpp`, and change `--spec-type mtp` to `--spec-type
draft-mtp` (mainline's spelling). You lose ~2.7× prefill and ~35% generation,
and gain the fix. Mainline also supports `--alias`, so you can give the model a
clean id instead of the file path.

---

## What to send to the cloud anyway

The local model executes plans well and recognises bad plans poorly. Escalate
rather than losing 30 minutes:

- you cannot describe what a correct answer looks like in advance
- cross-cutting changes across >10 files with interacting invariants
- race conditions, deadlocks, "works locally / fails in prod"
- auth, crypto, or anything touching the Gateway's trust boundary
- changes expensive to detect when wrong: migrations, deletions, tunnel/DNS
