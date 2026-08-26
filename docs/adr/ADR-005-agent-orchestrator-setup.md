# ADR-005: Agent Orchestrator Setup

## Status
Accepted

- **Date:** 2026-08-26
- **Stage:** 6

## Context
The Agent Harness requires a reliable way to boot the AI agent, point it at a specific specification, inject the local LLM configuration (e.g., Ollama/FreeToken running Qwen or similar models), and wire it to our sandboxed MCP server. 

## Decision
1. **Configuration (`config/llm_backends.yaml`):** We will define a structured YAML configuration for LLM endpoints (defining provider, base_url, model name, and required API keys/tokens).
2. **Execution Script (`bin/run_agent.sh`):** 
   - Must accept `--spec <spec_id>` and `--target-dir <path>`.
   - Must parse `config/llm_backends.yaml` to export standard environment variables (e.g., `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `MODEL_NAME`) so the agent CLI can use an OpenAI-compatible endpoint (like Ollama).
   - Must dynamically generate an MCP configuration file (e.g., `mcp.json`) in the `.agent-harness/` directory of the target project. This JSON must point to our `bin/start_mcp.sh` with the correct `--target-dir`.
   - Must launch the agent CLI (using `npx` or a Python module) scoped to the target directory, automatically passing the prompt to read `specs/<spec_id>/01_spec.md` and begin execution.
3. **Agent Persona (`config/roles.yaml`):** Define a base system prompt for the agent instructing it to strictly rely on the MCP tools for planning and execution, and forbidding manual guessing of dependencies.

## Implementation notes
- `bin/harness_config.py` is a small stdlib+PyYAML helper called from the shell script:
  `env <backend>` prints `export KEY='value'` lines (evaluated by `run_agent.sh`),
  `role <name>` prints the persona's system prompt, `list` shows available backends/roles.
  Keeping YAML parsing in Python avoids a `yq` dependency and keeps the shell script short.
- Exported variables: `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `MODEL_NAME` (per the ADR), plus
  `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_MODEL` and `LLM_PROVIDER`
  so an Anthropic-protocol CLI (Claude Code behind a translating proxy) works from the
  same profile. `api_key_env` in the profile lets a real secret come from the caller's
  environment instead of the YAML file.
- `mcp.json` is written to `<target>/.agent-harness/mcp.json` on every run with an absolute
  path to `bin/start_mcp.sh`; `.agent-harness/` is already excluded from
  `git_commit_feature` staging (ADR-001) so it never leaks into the target repository.
- The agent command is a placeholder, overridable via `AGENT_CMD`
  (default `npx @anthropic-ai/claude-code`). The persona is passed with
  `--append-system-prompt`; `--dry-run` prints the resolved command and environment
  without launching anything, which is what `tests/test_run_agent.py` exercises.
- `--target-dir` is required (falls back to `$TARGET_DIR`); `--backend` / `--role` default to
  the `default:` keys in the YAML files.

## Alternatives considered
- **`yq`/`jq` in bash** — extra system dependency; PyYAML is already in `requirements.txt`.
- **Pure-Python orchestrator (`python -m agent_loop`)** — would own the agent loop entirely
  instead of delegating to an existing CLI; deferred until an agent CLI is settled.
- **Static `mcp.json` committed to the target** — breaks when the harness or target moves;
  regenerating per run is cheap and keeps harness paths out of the target repo.

## Self-critique
- Claude Code speaks the Anthropic Messages protocol, not the OpenAI one; pointing
  `ANTHROPIC_BASE_URL` at raw Ollama will not work without a translating proxy. The
  OpenAI-style variables serve OpenAI-compatible CLIs; the concrete CLI is still a placeholder.
- `export KEY='value'` output is `eval`ed by the shell; values are single-quote escaped by
  the helper, but the config files are trusted input by design.
- The persona and prompt are only as strong as the CLI's respect for
  `--append-system-prompt`; there is no enforcement that the agent actually calls the tools.

## Consequences
- One command boots a session: `./bin/run_agent.sh --spec <id> --target-dir <repo>`.
- LLM endpoints and personas are data (`config/*.yaml`), not code; adding a backend is a
  YAML entry, and the same profile feeds either variable family.
- The target project gains a regenerated, untracked `.agent-harness/mcp.json`; nothing else
  from the harness touches it.
- Swapping the agent CLI is an `AGENT_CMD` override, or a one-line change in `run_agent.sh`.

## Prompt
`prompts-hist/005_agent_orchestrator.txt` (local-only)
