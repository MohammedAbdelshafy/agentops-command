# AgentOps Command

A Python CLI (standard library only) for tracking agent runs on a local JSON
store: register runs, append timestamped logs, set run states, record token
usage and cost, and produce rollup reports.

## Install

Requires Python 3.9+ and nothing else. Install from the repo to get the
`agentops` command, or run `agentops.py` directly:

```sh
pip install .
agentops --help

# or, without installing:
python3 agentops.py --help
```

## Usage

Store location defaults to `./.agentops/store.json`. Override with
`--store PATH` (before the subcommand) or the `AGENTOPS_STORE` environment
variable.

```sh
# Create a run (id must be unique)
python3 agentops.py register demo-run --agent batch-scorer \
    --meta dataset=leads.csv model=gemini-2.5-flash
# registered run demo-run

# Append timestamped log entries
python3 agentops.py log demo-run "started scoring batch"
python3 agentops.py log demo-run "rate limit hit, retrying" --level warn

# Record token usage and cost
python3 agentops.py cost demo-run --tokens-in 12000 --tokens-out 3000 --cost-usd 0.045

# Show status, or set state (succeeded/failed records ended_at)
python3 agentops.py status demo-run --state succeeded
# run:      demo-run
# agent:    batch-scorer
# state:    succeeded
# started:  2026-10-06T10:39:48.420873+00:00
# ended:    2026-10-06T10:39:48.880820+00:00
# tokens:   in=12000 out=3000
# cost_usd: 0.045
# logs:     2 entries
# meta:     dataset=leads.csv
# meta:     model=gemini-2.5-flash

# Compact table of all runs
python3 agentops.py list
# RUN ID                   AGENT            STATE       TOKENS IN  TOKENS OUT   COST USD
# demo-run                 batch-scorer     succeeded       12000        3000   0.045000

# Rollup report (markdown to stdout, or JSON with --format json; --out FILE to write to a file)
python3 agentops.py report
# # AgentOps Report
#
# Total runs: 1
#
# ## Runs by state
# - running: 0
# - succeeded: 1
# - failed: 0
#
# ## Totals
# - Tokens in: 12000
# - Tokens out: 3000
# - Cost (USD): 0.045000
#
# ## Failures
# - none
```

## Commands

| Command | What it does |
|---|---|
| `register <run-id> [--agent NAME] [--meta k=v ...]` | Create a run with id, agent name, metadata; records `started_at`. Errors if the id already exists. |
| `log <run-id> <message> [--level info\|warn\|error]` | Append a timestamped log entry. Errors if the run id is unknown. |
| `status <run-id> [--state running\|succeeded\|failed]` | Print the run. Setting a state updates it; `succeeded`/`failed` record `ended_at`, `running` clears it. |
| `cost <run-id> --tokens-in N --tokens-out M [--cost-usd X]` | Record token counts and optional USD cost on the run. |
| `report [--format md\|json] [--out FILE]` | Rollup across all runs: totals, counts by state, token/cost totals, and a failure list (failed runs with their last error log line, falling back to the last log line). `--out` creates missing parent directories. |
| `list` | One-line-per-run table. Long run ids and agent names are truncated with `...` to keep columns aligned. |

Exit code is non-zero with a one-line message on stderr for errors — never a
traceback. Errors include: duplicate register, unknown run id, empty run id,
malformed `k=v` metadata, negative token/cost values, a missing or corrupt
store file (bad JSON, top-level non-object, or non-object run entries), and
unreadable/unwritable paths.

## Tests

```sh
python3 tests/test_agentops.py
```

The suite runs the real CLI end to end in temp directories and covers:
register/log/status/cost round-trips, duplicate-register and
unknown-run errors, empty/whitespace run-id rejection, report math across
runs, failure listing, report-to-file (including parent-dir creation),
`list` output and long-id truncation, the `AGENTOPS_STORE` override, corrupt
and malformed stores failing cleanly (no tracebacks), `--store` pointing at a
directory, negative/non-integer token rejection, bad `--level`/`--state`
choices, `--help` examples, `--version`, `ended_at` set/clear semantics, and
store validity after every operation. 26 tests, all passing.

## Limits

- **Local single-machine store only.** The store is one JSON file on the
  local filesystem. There is no server, no sync, no multi-user support, and
  no locking — concurrent writers to the same store are not safe.
- Writes are crash-safe in the single-writer case: the store is written to a
  temp file in the same directory and atomically renamed over the old file.
- Cost figures are whatever you record with `cost`; the tool does not call
  any provider API or price table.
- This is ops bookkeeping, not observability: there is no tracing, no
  instrumentation agent, and no alerting.
