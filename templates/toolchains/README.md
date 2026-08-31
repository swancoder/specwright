# Toolchain templates

Reference `toolchain.json` files for non-Python stacks (Agent Harness — ADR-015, Stage 16).

## How to use

The harness's execution/validation layer (`run_tests`, the `run_toolchain_task` MCP tool, and the
mechanical completion gate in `bin/completion_checks.py`) is stack-agnostic. It looks for a
**`toolchain.json` in the target project root**:

- **Present** → the harness runs your declared `install` / `lint` / `test` / `build` commands.
- **Absent** → it falls back to the built-in **Python default** (`python -m venv .venv`,
  `pip install -r requirements.txt`, `mypy --strict`, `ruff check`, `pytest`).

To adopt one of these stacks, copy the matching file to your project root and rename it:

```bash
cp templates/toolchains/node-typescript.toolchain.json  <your-project>/toolchain.json
```

Then edit the commands to match your project's scripts. No harness change is needed — the
override is picked up automatically.

## Schema

```json
{
  "stack": "<identifier shown in gate output, e.g. node-typescript>",
  "commands": {
    "install": "<fetch dependencies>",
    "lint":    "<static analysis + style, non-zero exit on any finding>",
    "test":    "<run the test suite>",
    "build":   "<produce the shippable artifact>"
  }
}
```

Only the four keys above are used; omit a command (or leave it out) and that task is reported as
*skipped* rather than run.

## Execution notes (why the flags)

These commands are run **non-interactively by an MCP tool**, and the tool truncates output to 4 KiB
(head 20 lines + tail 50 lines, ANSI stripped). So every command must:

- **never prompt** — `--no-interaction` (Composer), `-B` / `--batch-mode` (Maven), `CI=true` (npm /
  vitest / jest / CRA all switch to a single non-watch run);
- **not draw progress bars / spinners** — `--no-progress` (Composer / npm), `-ntp` /
  `--no-transfer-progress` (Maven), `--no-color` where a tool insists on ANSI;
- **exit non-zero on any problem** — the gate treats a non-zero exit as a failure and feeds the
  (truncated) output back to the agent.

## Templates

| File | Stack | install | lint | test | build |
|------|-------|---------|------|------|-------|
| `php-js.toolchain.json` | PHP (Composer) + JS (npm) | `composer install` + `npm ci` | PHPStan + php-cs-fixer + `npm run lint` | PHPUnit + `npm test` | autoload dump + `npm run build` |
| `node-typescript.toolchain.json` | Node.js / TypeScript | `npm ci` | `tsc --noEmit` + ESLint | `npm test` (`CI=true`) | `npm run build` |
| `java-maven.toolchain.json` | Java (Maven) | `mvn dependency:go-offline` | Checkstyle + SpotBugs | `mvn test` | `mvn package` |
| `java-gradle.toolchain.json` | Java (Gradle) | `gradlew dependencies` | `gradlew check -x test` | `gradlew test` | `gradlew assemble` |

### Adapting per project

- **`npm run lint` / `npm run build`** assume your `package.json` defines those scripts (ESLint /
  `tsc` / your bundler). Point them at whatever your project actually uses.
- **PHP lint** assumes PHPStan and php-cs-fixer are dev dependencies; swap for Psalm / PHP_CodeSniffer
  if that's your setup.
- **Java with Gradle** instead of Maven — use `java-gradle.toolchain.json` (wrapper-based;
  `--console=plain` suppresses the progress UI, `--no-daemon` keeps it non-persistent,
  `check -x test` runs static verification without the test task).
- **Maven wrapper** — prefer `./mvnw` over `mvn` when the project ships one, for a pinned Maven version.

## The optional `fix` task (ADR-019)

Alongside `install` / `lint` / `test` / `build`, a stack may declare a **`fix`** command that applies an auto-fixer (`ruff check --fix`, `eslint --fix`, `php-cs-fixer fix`, …). It is a *mutating* task: only the implementer may run it (via `run_toolchain_task` task `fix`), never the read-only Verifier or the completion gate, so the mechanical check stays honest. Omit `fix` for stacks with no safe auto-fixer (the Java templates do).
