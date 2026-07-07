# observe-claude

Local capture of Claude Code session telemetry (tool usage, tokens, cost) into a CSV, no
manual export step required. ClickHouse ingestion is phase 2 — not wired up yet, the raw
JSONL is kept so it can be replayed into a different backend later without re-running any
Claude Code sessions.

## How it works

```
claude (OTLP) -> otel-collector -> data/claude-events.jsonl -> csv-writer -> claude_usage.csv + claude_spans.csv
```

`docker-compose.yml` runs two containers:
- **otel-collector**: receives OTLP logs/metrics/traces from Claude Code, writes the raw
  export to `data/claude-events.jsonl`.
- **csv-writer**: polls that file every 5s and regenerates `claude_usage.csv` (from log
  events) and `claude_spans.csv` (from trace spans), always up to date, nothing to
  trigger by hand.

## Usage

```bash
# 1. Start the collector + csv-writer (leave this running)
docker compose up -d

# 2. In the terminal where you'll run Claude Code:
source env.sh
claude
# ... use Claude Code normally ...
```

Or use the `Makefile`, which wraps the same steps (and sources `env.sh` for you):

```bash
make up        # start collector + csv-writer
make session   # launch a telemetry-enabled Claude Code session (ARGS=... to pass flags)
make down      # stop the stack when done
make help      # list all targets (csv, reset, logs, ps, ...)
```

That's it — both CSVs update themselves every 5 seconds while the stack is up.

Two things matter in `env.sh`: everything has to be exported *before* `claude` starts
(there is no way to enable telemetry for an already-running session), and traces are a
separate beta. `OTEL_TRACES_EXPORTER=otlp` alone does nothing; Claude Code only creates
spans when `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1` is also set. Without it the collector's
traces pipeline is live but never receives anything, and `claude_spans.csv` stays an empty
header row.

### claude_usage.csv (from logs)

One row per `api_request` (model, cost_usd, input/output/cache tokens), `tool_result`
(tool_name, success, duration_ms, error_type), and `tool_decision` (tool_name, decision,
source) event, joined by `session_id`. This is where cost and tool accept/prompt decisions
live; spans do not carry them.

### claude_spans.csv (from traces)

One row per span, with `parent_span_id` so a flat list rebuilds into the call tree
(`claude_code.interaction` -> `claude_code.tool` -> `claude_code.llm_request`), plus
per-span `model`, tokens, `ttft_ms`, and `stop_reason`.

The `agent_id` column is what makes subagents legible. When a session dispatches subagents
(for example an Opus main spawning Sonnet subagents), they all share one `trace_id` and one
`session.id`, so `session.id` cannot tell them apart. Instead, the main agent's spans have
an empty `agent_id` and each subagent's spans carry a distinct one. A subagent's spans nest
under the parent's `claude_code.tool.execution` span (the Task-tool call), so
`parent_span_id` gives you the nesting and `agent_id` gives you the attribution: which
subagent, on which model, under which parent turn.

## Sample output

Real rows from a live session (three `claude -p` calls, three models):

| timestamp | session_id | event_type | model | tool_name | decision | success | cost_usd | duration_ms |
|---|---|---|---|---|---|---|---|---|
| 2026-07-04T19:41:05.216Z | 4255b08c... | tool_decision | | Read | accept | | | |
| 2026-07-04T19:41:05.218Z | 4255b08c... | api_request | claude-sonnet-5 | | | | 0.076066 | 2958 |
| 2026-07-04T19:41:05.221Z | 4255b08c... | tool_result | | Read | | true | | 5 |
| 2026-07-04T19:41:46.045Z | 4a4f71f6... | tool_decision | | Grep | accept | | | |
| 2026-07-04T19:41:46.052Z | 4a4f71f6... | api_request | claude-opus-4-8 | | | | 0.26328 | 4415 |

Grouped by model and by tool, the same session looks like this:

![Cost by model and tool calls by tool, from a real claude_usage.csv](images/usage-chart.svg)

And the collector receiving it, end to end:

![docker compose ps and awk against a real claude_usage.csv](images/terminal-sample.svg)

One dispatch from `claude_spans.csv`, an Opus session spawning a Sonnet subagent (same
`trace_id` on every row):

| span_id | parent_span_id | span_name | agent_id | model | stop_reason |
|---|---|---|---|---|---|
| aaf45133... | 3148a886... | claude_code.tool | | | |
| f253fecb... | aaf45133... | claude_code.tool.execution | | | |
| 8c8a24ce... | f253fecb... | claude_code.llm_request | a9f513d5... | claude-sonnet-5 | tool_use |
| c1cd61df... | f253fecb... | claude_code.tool | a9f513d5... | | |
| 780673bc... | f253fecb... | claude_code.llm_request | a9f513d5... | claude-sonnet-5 | end_turn |

The top two rows are the main Opus agent calling the Task tool; the three below are the
Sonnet subagent's turn, nested under `tool.execution` and stamped with its `agent_id`.

## Gotchas

- **Telemetry env vars must be set before `claude` starts.** There's no way to turn on
  export retroactively for an already-running session.
- **`OTEL_TRACES_EXPORTER=otlp` alone gets you no spans.** Traces are a separate beta:
  Claude Code only creates spans when `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1` is set too.
  Nothing errors if you miss it, `claude_spans.csv` just stays an empty header row.
- **Don't delete `data/claude-events.jsonl` while the collector is running.** The exporter
  holds the file open; deleting it from the host leaves the collector writing into a
  deleted, invisible inode until the container restarts. Use `docker compose restart
  otel-collector` if you need to reset it, not `rm` while it's live.
- **OTLP ports are bound to `127.0.0.1` only, on purpose.** The receiver has no auth or
  TLS — do not change this to `0.0.0.0` unless you add authentication, or anyone on your
  network can inject fake telemetry into your collector.
- The captured JSONL includes `user.email` and account/org UUIDs (prompt/response text is
  redacted by default). Don't commit `data/` or `*.csv` — both are already gitignored.
- **The root `claude_code.interaction` span is missing until the session ends.** Spans
  export when they close, not when they open, so the collector only receives finished
  spans. Leaf spans (`llm_request`, `tool`) close per step and arrive within seconds, but
  the interaction span wrapping the whole session stays open until the session exits: a
  one-shot `claude -p` run has its root, a long-running interactive session does not (every
  captured span points at a `parent_span_id` that never arrives). Closing the session
  flushes the root on shutdown, so keep the collector up until then, and don't `make
  restart` right as you close (it truncates the JSONL and you lose that final span).

## Phase 2 (later)

`data/claude-events.jsonl` is the full raw OTLP export — nothing is discarded, so a second
collector pipeline can be pointed at it later (e.g. `filelog`/`otlpjsonfile` receiver ->
`clickhouse` exporter) to backfill ClickHouse without needing to re-run any Claude Code
sessions.
