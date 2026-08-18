# ik_llama.cpp — CPU inference runtime for Sentry's local model.
#
# ik_llama rather than mainline llama.cpp because it was MEASURED faster on this
# exact host (Ryzen 9 7950X3D, 96GB DDR5-5600, 2026-08-18), same model and quant:
#
#   engine                       prefill t/s   generation t/s
#   mainline llama.cpp                 139.9            14.75
#   mainline + draft-mtp               139.9            19.14
#   ik_llama -rtr                      382.7            17.45
#   ik_llama -rtr --spec-type mtp      382.7            25.99   <-- 2.7x / 1.8x
#
# Tool calling was verified working on both engines (returns `tool_calls` with
# `arguments` as a JSON string, per the OpenAI spec, which is what Hermes needs).
#
# Fallback: llama.cpp mainline carries the fix for issue #24807 (malformed
# tool-call XML on Qwen3.6-35B-A3B, ~1 in 128 tool-using requests, fixed in
# PR #24839). Whether ik_llama has ported that fix is UNVERIFIED. If tool calls
# start dropping, rebuild this stage from ggml-org/llama.cpp and change
# `--spec-type mtp` to `--spec-type draft-mtp` in compose.yaml. Nothing else changes.

FROM debian:bookworm-slim AS build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git ca-certificates libcurl4-openssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
# Pinned to a commit, not a branch: an inference runtime is never silently
# upgraded under a running agent. Bump deliberately, then re-run the benchmarks.
# 8337e4c is the commit the numbers above were measured against ("Fix Qwen35+ MTP").
ARG IK_LLAMA_REF=8337e4c
RUN git clone https://github.com/ikawrakow/ik_llama.cpp . \
    && git checkout "${IK_LLAMA_REF}" \
    && git rev-parse HEAD > /BUILD_COMMIT

# GGML_NATIVE=ON compiles for the build host's CPU. That is correct here because
# this image is built and run on the same machine, and Zen 4's AVX-512 + BF16 are
# a large part of the numbers above. It also means the image is NOT portable to a
# different CPU -- rebuild rather than copy it elsewhere.
RUN cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON -DLLAMA_CURL=OFF \
    && cmake --build build -j "$(nproc)" --target llama-server

# ik_llama's CMake scatters the shared objects across build/src, build/ggml/src
# and build/tools/* rather than collecting them beside the executable the way
# mainline does. Copying build/bin alone yields a binary that cannot resolve
# libmtmd.so / libllama.so / libggml.so at runtime, so stage them explicitly.
RUN mkdir -p /out/bin \
    && cp build/bin/llama-server /out/bin/ \
    && find build -name '*.so' -o -name '*.so.*' | xargs -r -I{} cp -a {} /out/bin/ \
    && ls -1 /out/bin

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 libstdc++6 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --create-home --shell /usr/sbin/nologin llama

COPY --from=build /out/bin/ /opt/llama/bin/
COPY --from=build /BUILD_COMMIT /opt/llama/BUILD_COMMIT
ENV LD_LIBRARY_PATH=/opt/llama/bin

USER llama
EXPOSE 8080
ENTRYPOINT ["/opt/llama/bin/llama-server"]
