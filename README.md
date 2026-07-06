# observe-claude

Local capture of Claude Code session telemetry (tool usage, tokens, cost, and trace spans)
into CSVs, no manual export step required. ClickHouse ingestion is phase 2 — not wired up
yet, the raw JSONL is kept so it can be replayed into a different backend later without
re-running any Claude Code sessions.

## How it works

```
claude (OTLP) -> otel-collector -> data/claude-events.jsonl -> csv-writer -> claude_usage.csv, claude_spans.csv
```

`docker-compose.yml` runs two containers:
- **otel-collector**: receives OTLP logs/metrics/traces from Claude Code, writes the raw
  export to `data/claude-events.jsonl`.
- **csv-writer**: polls that file every 5s and regenerates `claude_usage.csv` and
  `claude_spans.csv` — always up to date, nothing to trigger by hand.

Traces are gated behind a separate beta flag on top of `CLAUDE_CODE_ENABLE_TELEMETRY`.
Without `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1`, `OTEL_TRACES_EXPORTER` is silently
ignored and the traces pipeline in `otel-collector-config.yaml` never receives anything,
`claude_spans.csv` stays empty even though the collector is listening. `env.sh` sets both.

## Usage

```bash
# 1. Start the collector + csv-writer (leave this running)
docker compose up -d

# 2. In the terminal where you'll run Claude Code:
source env.sh
claude
# ... use Claude Code normally ...
```

That's it — the CSVs update themselves every 5 seconds while the stack is up.

`claude_usage.csv` has one row per `api_request` (model, cost_usd, input/output/cache tokens),
`tool_result` (tool_name, success, duration_ms, error_type), and `tool_decision`
(tool_name, decision, source) event, joined by `session_id`.

`claude_spans.csv` has one row per span: `claude_code.interaction` (the whole turn),
`claude_code.tool` / `claude_code.tool.execution` / `claude_code.tool.blocked_on_user`
(one tool call), and `claude_code.llm_request` (one model call, with tokens and `ttft_ms`).
`trace_id` ties every span in a turn together; `parent_span_id` gives the tree.

## Sample output

Real rows from a live session (`api_request`/`tool_result`/`tool_decision` events):

| timestamp | session_id | event_type | model | tool_name | decision | success | cost_usd | duration_ms |
|---|---|---|---|---|---|---|---|---|
| 2026-07-06T09:42:57.491Z | 830d0f1c... | tool_decision | | Read | accept | | | |
| 2026-07-06T09:42:57.498Z | 830d0f1c... | tool_result | | Read | | true | | 3 |
| 2026-07-06T09:42:57.511Z | 830d0f1c... | api_request | claude-sonnet-5 | | | | 0.107504 | 4691 |
| 2026-07-06T09:43:00.727Z | 830d0f1c... | api_request | claude-sonnet-5 | | | | 0.024125 | 3141 |
| 2026-07-06T09:43:08.752Z | 8b48513b... | tool_decision | | Bash | accept | | | |
| 2026-07-06T09:43:09.324Z | 8b48513b... | tool_result | | Bash | | true | | 571 |

And the trace spans for that same session, one full turn (`trace_id` truncated for width):

| trace_id | span_id | parent_span_id | span_name | model | duration_ms | ttft_ms | stop_reason |
|---|---|---|---|---|---|---|---|
| 68691a63... | b9627b1d... | *(root)* | claude_code.interaction | | 8437 | | |
| 68691a63... | 51fa875b... | b9627b1d... | claude_code.llm_request | claude-sonnet-5 | 4694 | 2740 | tool_use |
| 68691a63... | 7e062ead... | b9627b1d... | claude_code.tool | | 11 | | |
| 68691a63... | 8bdf191c... | 7e062ead... | claude_code.tool.execution | | 3 | | |
| 68691a63... | 3e556636... | b9627b1d... | claude_code.llm_request | claude-sonnet-5 | 3142 | 1984 | end_turn |

Same `trace_id` across every row: one model call decides to use a tool (`stop_reason:
tool_use`), the tool runs, a second model call ends the turn (`stop_reason: end_turn`) —
all children of the one `claude_code.interaction` root span.

Grouped by model and by tool, the same session looks like this:

![Cost by model and tool calls by tool, from a real claude_usage.csv](images/usage-chart.svg)

And the collector receiving it, end to end:

![docker compose ps and awk against a real claude_usage.csv](images/terminal-sample.svg)

## Gotchas

- **Telemetry env vars must be set before `claude` starts.** There's no way to turn on
  export retroactively for an already-running session.
- **`OTEL_TRACES_EXPORTER=otlp` alone does nothing.** Traces are a separate beta gate;
  without `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1` set too, no spans are ever generated
  and `claude_spans.csv` will exist but stay empty, with no error anywhere.
- **Don't delete `data/claude-events.jsonl` while the collector is running.** The exporter
  holds the file open; deleting it from the host leaves the collector writing into a
  deleted, invisible inode until the container restarts. Use `docker compose restart
  otel-collector` if you need to reset it, not `rm` while it's live.
- **OTLP ports are bound to `127.0.0.1` only, on purpose.** The receiver has no auth or
  TLS — do not change this to `0.0.0.0` unless you add authentication, or anyone on your
  network can inject fake telemetry into your collector.
- The captured JSONL includes `user.email` and account/org UUIDs (prompt/response text is
  redacted by default). Don't commit `data/` or `*.csv` — both are already gitignored.

## Phase 2 (later)

`data/claude-events.jsonl` is the full raw OTLP export — nothing is discarded, so a second
collector pipeline can be pointed at it later (e.g. `filelog`/`otlpjsonfile` receiver ->
`clickhouse` exporter) to backfill ClickHouse without needing to re-run any Claude Code
sessions.
