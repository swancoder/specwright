# ADR-006: Agent Bootstrap and Open Code Integration

## Status
Accepted

- **Date:** 2026-08-26
- **Stage:** 7

## Context
The Agent Harness uses Open Code as the primary CLI agent and relies on a local LLM via Ollama (`gpt-oss:20b`). We need a deterministic bootstrap script to prepare the environment and ensure the orchestrator correctly passes the OpenAI-compatible environment variables to Open Code without requiring an intermediate translation proxy.

## Decision
1. **Bootstrap Script (`bin/bootstrap_env.sh`):**
   - Create a robust initialization script with strict error handling (`set -e`).
   - It must create a Python virtual environment (`.venv`) and install dependencies from `requirements.txt`.
   - It must ensure any required system or node dependencies for Open Code are available (or provide clear output on what is missing).
2. **Configuration Update (`config/llm_backends.yaml`):**
   - Update the default `ollama` profile to explicitly use the `gpt-oss:20b` model.
3. **Execution Script Tuning (`bin/run_agent.sh`):**
   - Ensure the script exports the standard `OPENAI_BASE_URL` and `OPENAI_API_KEY` variables mapped from the backend configuration.
   - Set `AGENT_CMD` to correctly invoke Open Code with the generated `.agent-harness/mcp.json` configuration and the target specification.

## Implementation notes
- `bin/bootstrap_env.sh [--python <exe>] [--check-only]`: `set -euo pipefail`; creates `.venv`
  (idempotent), upgrades `pip`, installs `requirements.txt`, then checks `node` (≥ 18), `npm`,
  `opencode`, `ollama` reachability at the configured `base_url`, and whether the configured
  model is pulled. Each check prints `ok` / `MISSING` with the exact command to fix it;
  missing optional tooling is reported and the script exits 1 only when the Python
  environment itself could not be prepared or `--check-only` found a gap.
- `config/llm_backends.yaml`: `ollama.model: gpt-oss:20b`. `bin/harness_config.py env` now emits
  only `LLM_BACKEND`, `LLM_PROVIDER`, `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `MODEL_NAME` and
  `extra_env`; the `ANTHROPIC_*` family from ADR-005 is removed.
- Open Code has no `--mcp-config` flag and does not read `mcpServers`-style JSON. `run_agent.sh`
  therefore still writes `<target>/.agent-harness/mcp.json` (ADR-005 contract) and *derives*
  `<target>/.agent-harness/opencode.json` from it: the same `command`/`args` as a local MCP
  server named `agent-harness`, a provider `<backend>` (`@ai-sdk/openai-compatible`,
  `baseURL = OPENAI_BASE_URL`, `apiKey = OPENAI_API_KEY`) exposing `MODEL_NAME`, and an agent
  named after the role whose `prompt` is the `roles.yaml` system prompt with Open Code's
  built-in mutating tools (`bash`, `edit`, `write`, `patch`, `multiedit`, `webfetch`)
  disabled so the persona's "MCP tools only" rule is enforced, not just requested.
- The generated provider model carries `limit: {context: <context_length>, output: <max_output_tokens>}` from the backend profile. Open Code resolves a missing limit to `0` (`limit?.context ?? 0`), which makes its overflow check fire on every turn and compact the transcript continuously — observed as an endless re-read loop in run 7.
- Launch: `OPENCODE_CONFIG=<opencode.json> opencode run --dir <target> -m <backend>/<model>
  --agent <role> "<prompt>"`. `AGENT_CMD` defaults to `opencode`; setting it to anything else
  falls back to the generic ADR-005 invocation (`--mcp-config … --append-system-prompt …`).
- `--dry-run` prints the resolved environment, generated files and command without launching.

## Alternatives considered
- **Translating proxy (Anthropic → OpenAI protocol) in front of Ollama** — extra moving part; Open
  Code speaks OpenAI-compatible natively, which is why it is the chosen CLI.
- **Registering the MCP server in the user's global `~/.config/opencode/opencode.jsonc`** —
  leaks per-target paths into user config and breaks with multiple targets; a per-run,
  per-target config selected by `OPENCODE_CONFIG` keeps ADR-001 isolation.
- **Installing Open Code from `bootstrap_env.sh`** — `npm install -g` touches the user's global
  prefix; the script prints the command instead of running it.

## Self-critique
- `OPENCODE_CONFIG` merges over the user's global config; a global `model`/`agent` entry with
  the same name would be overridden per run, which is intended but non-obvious.
- Disabling built-ins relies on Open Code honouring per-agent `tools` toggles; a model that
  ignores tool schemas can still produce text-only "edits", which the harness cannot prevent.
- `gpt-oss:20b` leaks reasoning into the visible text through the OpenAI-compatible endpoint;
  harmless for tool calls, noisy in transcripts.
- `bootstrap_env.sh` checks that Ollama answers and the model is pulled but does not pull it
  (multi-GB download); it prints `ollama pull <model>`.

## Consequences
- A fresh checkout is one command away from a working harness: `./bin/bootstrap_env.sh`, and it
  says exactly what is still missing (`opencode`, `ollama`, the model).
- Only OpenAI-compatible variables cross the harness → agent boundary; no proxy, no
  Anthropic-specific configuration anywhere in the repo.
- Every target project gets a regenerated, untracked `.agent-harness/{mcp.json,opencode.json}`;
  the user's global Open Code config is never modified by the harness.
- Swapping the model or endpoint is a YAML edit; swapping the agent CLI is `AGENT_CMD`.

## Prompt
`prompts-hist/006_bootstrap_and_opencode.txt` (local-only)
