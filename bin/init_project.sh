#!/bin/bash
# Initialise a new target project the Agent Harness can drive (ADR-016).
#
# Usage: ./bin/init_project.sh --project-dir <path> [--stack <s>] [--backend <b>]
#                              [--model <m>] [--name <n>] [--spec-slug <slug>] [--yes] [--force]
#
#   --project-dir <path>   where to create the project (required)
#   --stack <s>            python | php-js | node-typescript | java-maven | java-gradle  (default: python)
#   --backend <b>          opencode | claude   (the agent "harness" to drive it; default: opencode)
#   --model <m>            model override (default: backend's default — gpt-oss:20b-32k / sonnet)
#   --name <n>             project name (default: basename of --project-dir)
#   --spec-slug <slug>     first feature slug (default: 001-feature)
#   --yes                  non-interactive: accept defaults, do not prompt
#   --force                allow a non-empty target directory
#
# Scaffolds: .github/constitution.md, CLAUDE.md, specs/template + specs/<slug>, toolchain.json
# (for non-Python stacks), .gitignore, a git baseline commit — then prints the run command.
set -euo pipefail

HARNESS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_DIR=""; STACK=""; BACKEND=""; MODEL=""; NAME=""; SPEC_SLUG=""; YES=0; FORCE=0

usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --project-dir)  PROJECT_DIR="${2:-}"; shift 2 ;;
    --project-dir=*) PROJECT_DIR="${1#*=}"; shift ;;
    --stack)        STACK="${2:-}"; shift 2 ;;
    --stack=*)      STACK="${1#*=}"; shift ;;
    --backend)      BACKEND="${2:-}"; shift 2 ;;
    --backend=*)    BACKEND="${1#*=}"; shift ;;
    --model)        MODEL="${2:-}"; shift 2 ;;
    --model=*)      MODEL="${1#*=}"; shift ;;
    --name)         NAME="${2:-}"; shift 2 ;;
    --name=*)       NAME="${1#*=}"; shift ;;
    --spec-slug)    SPEC_SLUG="${2:-}"; shift 2 ;;
    --spec-slug=*)  SPEC_SLUG="${1#*=}"; shift ;;
    --yes|-y)       YES=1; shift ;;
    --force)        FORCE=1; shift ;;
    -h|--help)      usage 0 ;;
    *) echo "init_project.sh: unknown argument '$1'" >&2; usage 2 ;;
  esac
done

# --- interactive prompts (only on a tty, only for missing values) --------------
interactive() { [ "$YES" -eq 0 ] && [ -t 0 ]; }
ask() { local prompt="$1" default="$2" reply; if interactive; then read -r -p "$prompt [$default]: " reply; echo "${reply:-$default}"; else echo "$default"; fi; }
choose() {  # $1=prompt  $2=default  $3..=options
  local prompt="$1" default="$2"; shift 2
  if ! interactive; then echo "$default"; return; fi
  echo "$prompt" >&2; local i=1; for o in "$@"; do echo "  $i) $o" >&2; i=$((i+1)); done
  local reply; read -r -p "choose [default $default]: " reply
  [ -z "$reply" ] && { echo "$default"; return; }
  if [ "$reply" -ge 1 ] 2>/dev/null && [ "$reply" -le $# ]; then eval "echo \${$reply}"; else echo "$reply"; fi
}

[ -n "$PROJECT_DIR" ] || PROJECT_DIR="$(ask "Project directory" "./my-project")"
[ -n "$STACK" ]   || STACK="$(choose "Choose a stack (toolchain):" "python" python php-js node-typescript java-maven java-gradle)"
[ -n "$BACKEND" ] || BACKEND="$(choose "Choose an agent harness (backend):" "opencode" opencode claude)"
NAME="${NAME:-$(basename "$PROJECT_DIR")}"
SPEC_SLUG="${SPEC_SLUG:-001-feature}"
case "$SPEC_SLUG" in *[!A-Za-z0-9._-]*|"") echo "init_project.sh: invalid --spec-slug '$SPEC_SLUG'" >&2; exit 2 ;; esac

# --- per-stack profile ---------------------------------------------------------
case "$STACK" in
  python)          LANG_ROW="Python 3.12+"; TEST_ROW="pytest + httpx"; LINT_ROW="Ruff + mypy (strict)"; TC=""; IGN=".venv/
__pycache__/
*.py[cod]
*.db
*.sqlite*" ;;
  php-js)          LANG_ROW="PHP 8.2+ (backend) · JavaScript (frontend)"; TEST_ROW="PHPUnit + Vitest"; LINT_ROW="PHPStan + php-cs-fixer + ESLint"; TC="php-js"; IGN="/vendor/
/node_modules/
*.db
*.sqlite*" ;;
  node-typescript) LANG_ROW="TypeScript on Node.js 20+"; TEST_ROW="Vitest"; LINT_ROW="ESLint + tsc --noEmit"; TC="node-typescript"; IGN="/node_modules/
/dist/
*.log" ;;
  java-maven)      LANG_ROW="Java 21 (Maven)"; TEST_ROW="JUnit 5"; LINT_ROW="Checkstyle + SpotBugs"; TC="java-maven"; IGN="/target/
*.class" ;;
  java-gradle)     LANG_ROW="Java 21 (Gradle)"; TEST_ROW="JUnit 5"; LINT_ROW="Checkstyle + SpotBugs"; TC="java-gradle"; IGN="/build/
/.gradle/
*.class" ;;
  *) echo "init_project.sh: unknown --stack '$STACK' (python|php-js|node-typescript|java-maven|java-gradle)" >&2; exit 2 ;;
esac
[ -z "$BACKEND" ] && BACKEND=opencode
case "$BACKEND" in
  opencode) DEF_MODEL="gpt-oss:20b-32k"; RUN_PREFIX="AGENT_CMD=opencode " ;;
  claude)   DEF_MODEL="sonnet";          RUN_PREFIX="AGENT_CMD=claude " ;;
  *) echo "init_project.sh: unknown --backend '$BACKEND' (opencode|claude)" >&2; exit 2 ;;
esac
MODEL="${MODEL:-$DEF_MODEL}"

# --- create the directory ------------------------------------------------------
if [ -e "$PROJECT_DIR" ] && [ -n "$(ls -A "$PROJECT_DIR" 2>/dev/null)" ] && [ "$FORCE" -eq 0 ]; then
  echo "init_project.sh: '$PROJECT_DIR' is not empty (use --force to scaffold anyway)" >&2; exit 2
fi
mkdir -p "$PROJECT_DIR"
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
mkdir -p "$PROJECT_DIR/.github" "$PROJECT_DIR/specs/template" "$PROJECT_DIR/specs/$SPEC_SLUG" "$PROJECT_DIR/src" "$PROJECT_DIR/tests"

# --- constitution --------------------------------------------------------------
cat > "$PROJECT_DIR/.github/constitution.md" <<EOF
# Project Constitution

Non-negotiable rules for this repository. If a spec, plan, or instruction conflicts with the
constitution, **the constitution wins** and the conflict must be raised, not silently resolved.

## 1. Architectural principles
1. **Spec before code.** No feature without \`specs/<feature>/01_intent.md\`, \`02_spec.md\`, \`03_plan.md\`.
2. **Modules own their boundaries.** Each module exposes one public entry point; cross-module use goes through it.
3. **Explicit over implicit.** No global mutable state, no hidden side effects.
4. **Fail loudly.** Errors are surfaced with context, never swallowed.
5. **Small, reversible changes.** Prefer several narrow commits over one broad one.

## 2. Tech stack (changing a row needs an ADR + human approval)
| Layer      | Choice           |
|------------|------------------|
| Language   | $LANG_ROW |
| Tests      | $TEST_ROW |
| Lint/Types | $LINT_ROW |

## 3. Hard constraints (never violate)
- No secrets in the repo — credentials come only from environment variables.
- All external input is validated at the boundary.
- No disabling of lint rules or type checks to make a build pass.
- No dependency additions without a stated reason.

## 4. Quality bars
- Every public function/class has a doc comment describing inputs, outputs, and errors.
- The lint and test suites pass cleanly before a change is considered done.
EOF

# --- CLAUDE.md -----------------------------------------------------------------
if [ -n "$TC" ]; then RUN_CMDS="Lifecycle commands are declared in \`toolchain.json\` (install / lint / test / build)."; else RUN_CMDS="Python default toolchain: \`python -m venv .venv\`, \`pip install -r requirements.txt\`, \`ruff check .\`, \`mypy src\`, \`pytest -q tests\`."; fi
cat > "$PROJECT_DIR/CLAUDE.md" <<EOF
# CLAUDE.md — developer context ($NAME)

Read this first, then the spec for the feature you are working on. The rules in
\`.github/constitution.md\` override anything here.

## Stack
$LANG_ROW · tests: $TEST_ROW · lint/types: $LINT_ROW.
$RUN_CMDS

## Workflow
1. **Spec-driven.** Never start coding without \`specs/<feature>/01..03\`.
2. **Tests are the contract.** Write/adjust tests in the "tests first" step; then implement.
3. **Small steps.** Follow \`03_plan.md\`; commit after each milestone.
4. **Verify.** Lint, type-check, and test must pass before a feature is done.

## Conventions
- Validate all external input at the boundary; trust nothing past it.
- Doc comment on every public symbol.
- Conventional Commits (\`feat:\`, \`fix:\`, \`test:\`, \`chore:\`) + spec ID.
EOF

# --- specs skeleton ------------------------------------------------------------
for f in 01_intent 02_spec 03_plan; do
  cat > "$PROJECT_DIR/specs/template/$f.md" <<EOF
# ${f#*_} template
> Replace this with the real content for a feature.
EOF
done
cat > "$PROJECT_DIR/specs/$SPEC_SLUG/01_intent.md" <<EOF
# ${SPEC_SLUG}: Intent
## Business problem
<why this feature exists>
## Goal
<what success looks like>
## Scope & constraints
<in / out of scope>
EOF
cat > "$PROJECT_DIR/specs/$SPEC_SLUG/02_spec.md" <<EOF
# ${SPEC_SLUG}: Technical specification
## 1. <component>
<requirements the implementation and its tests must satisfy>
## 2. Testing
<what tests must assert>
EOF
: > "$PROJECT_DIR/specs/$SPEC_SLUG/03_plan.md"   # filled by the plan phase, approved by a human

# --- toolchain.json (non-Python stacks) ----------------------------------------
if [ -n "$TC" ]; then
  cp "$HARNESS_DIR/templates/toolchains/$TC.toolchain.json" "$PROJECT_DIR/toolchain.json"
fi
[ -f "$PROJECT_DIR/src/.gitkeep" ] || : > "$PROJECT_DIR/src/.gitkeep"

# --- .gitignore ----------------------------------------------------------------
printf '%s\n' "$IGN" ".agent-harness/" > "$PROJECT_DIR/.gitignore"

# --- git baseline --------------------------------------------------------------
if [ ! -d "$PROJECT_DIR/.git" ]; then
  git -C "$PROJECT_DIR" init -q -b main
  git -C "$PROJECT_DIR" add -A
  GIT_AUTHOR_NAME="${GIT_AUTHOR_NAME:-Agent Harness}" GIT_AUTHOR_EMAIL="${GIT_AUTHOR_EMAIL:-harness@local}" \
  GIT_COMMITTER_NAME="${GIT_COMMITTER_NAME:-Agent Harness}" GIT_COMMITTER_EMAIL="${GIT_COMMITTER_EMAIL:-harness@local}" \
    git -C "$PROJECT_DIR" commit -q -m "chore: initialise $NAME ($STACK) project scaffold"
fi

# --- next steps ----------------------------------------------------------------
MODEL_ARG=""; [ "$BACKEND" = "claude" ] && MODEL_ARG=" --model $MODEL"
cat >&2 <<EOF

✓ Initialised '$NAME' in $PROJECT_DIR
    stack:   $STACK$([ -n "$TC" ] && echo " (toolchain.json)" || echo " (Python default)")
    harness: $BACKEND (model $MODEL)
    spec:    specs/$SPEC_SLUG/  (fill in 01_intent.md and 02_spec.md, then plan)

Next:
  1. edit  $PROJECT_DIR/specs/$SPEC_SLUG/01_intent.md and 02_spec.md
  2. plan:      ${RUN_PREFIX}$HARNESS_DIR/bin/run_agent.sh --spec $SPEC_SLUG --target-dir $PROJECT_DIR$MODEL_ARG --phase plan
  3. approve:   tick the Pre-flight checkboxes in specs/$SPEC_SLUG/03_plan.md, then commit
  4. implement: ${RUN_PREFIX}$HARNESS_DIR/bin/run_agent.sh --spec $SPEC_SLUG --target-dir $PROJECT_DIR$MODEL_ARG
EOF
