#!/usr/bin/env bash
# Record the test conditions behind every wall-clock number: hardware, OS, ollama version and
# loaded models, other ollama consumers at the time, and per-model LLM turn timing from every
# probe's proxy log (turn_timing.py). Writes evidence/opencode/conditions.log.
set -u
export OCSOMA_PROBE_SCRIPT="research/opencode-soma/probes/opencode/conditions.sh"
PROBE_ID=conditions; source "$(dirname "$0")/sandbox.sh"
ev_open conditions.log "test conditions + per-model LLM turn timing (tier-table evidence)" "ollama/qwen3:32b, ollama/lfm2.5-thinking"
ev_run sh -c "sysctl -n machdep.cpu.brand_string; sysctl -n hw.memsize | awk '{print \$1/1024/1024/1024 \" GiB RAM\"}'; sw_vers 2>/dev/null | tr '\n' ' '; echo"
ev_run curl -s http://127.0.0.1:11434/api/version
ev_run sh -c "curl -s http://127.0.0.1:11434/api/tags | jq -c '.models[] | {name, size_gb: (.size/1e9|floor), params: .details.parameter_size, quant: .details.quantization_level, native_ctx: .details.context_length}'"
ev_run sh -c "curl -s http://127.0.0.1:11434/api/ps | jq -c '.models[] | {name, context_length, vram_gb: (.size_vram/1e9|floor)}'"
ev "## other ollama consumers on this machine during the probes (other teammates' benchmarks share the GPU; timings are upper bounds)"
ev_run sh -c "ps -axo pid,etime,command | grep -E 'run_bench|research/opencode-soma/probes/(memory|platform)' | grep -v grep | cut -c1-160 || true"
ev "## per-model LLM turn timing across all opencode probes (turn_timing.py over evidence/opencode/**/proxy*.jsonl)"
ev_run python3.12 -I "$OCSOMA_PROBES/turn_timing.py" "$OCSOMA_EVID"
ev_verdict PASS "conditions and per-model turn timing recorded."
tail -1 "$EV_LOG"
