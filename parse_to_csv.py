#!/usr/bin/env python3
"""Parse Claude Code OTel export (JSONL from the collector's file exporter) into CSVs.

Two outputs, because logs and spans carry different things:
  claude_usage.csv  <- log records (api_request / tool_result / tool_decision):
                       cost_usd, tool accept/prompt decisions, tool success.
  claude_spans.csv  <- trace spans: the call tree (parent_span_id), tokens, ttft,
                       stop_reason, and the model each span ran on. This is what
                       shows an Opus session dispatching Sonnet subagents: the
                       subagent's llm_request spans carry model=claude-sonnet-* and
                       hang off the parent turn's trace.

Spans only arrive when CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1 and
OTEL_TRACES_EXPORTER=otlp are set before the session starts; without them the
traces pipeline is live but receives nothing and claude_spans.csv stays empty.
"""
import argparse
import csv
import json
import os
from datetime import datetime, timezone

FIELDS = [
    "timestamp", "session_id", "event_type", "model", "tool_name", "decision",
    "source", "success", "error_type", "cost_usd", "duration_ms",
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens",
    "tool_use_id",
]

TARGET_EVENTS = {"api_request", "tool_result", "tool_decision"}

SPAN_FIELDS = [
    "timestamp", "trace_id", "span_id", "parent_span_id", "span_name",
    "session_id", "agent_id", "model", "duration_ms", "input_tokens", "output_tokens",
    "cache_read_tokens", "cache_creation_tokens", "ttft_ms", "success", "stop_reason",
]


def attr_value(v):
    for key in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if key in v:
            return v[key]
    return ""


def first_attr(attrs, *keys):
    """First non-empty value among candidate attribute keys (schemas drift)."""
    for k in keys:
        if k in attrs and attrs[k] != "":
            return attrs[k]
    return ""


def nanos_to_iso(nanos):
    try:
        ts = int(nanos) / 1e9
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


# ---- logs ----

def iter_log_records(obj):
    for rl in obj.get("resourceLogs", []):
        for sl in rl.get("scopeLogs", []):
            for record in sl.get("logRecords", []):
                yield record


def record_to_row(record):
    attrs = {a["key"]: attr_value(a["value"]) for a in record.get("attributes", [])}
    event_type = attrs.get("event.name")
    if event_type not in TARGET_EVENTS:
        return None
    return {
        "timestamp": attrs.get("event.timestamp", ""),
        "session_id": attrs.get("session.id", ""),
        "event_type": event_type,
        "model": attrs.get("model", ""),
        "tool_name": attrs.get("tool_name", ""),
        "decision": attrs.get("decision", ""),
        "source": attrs.get("source", "") or attrs.get("decision_source", ""),
        "success": attrs.get("success", ""),
        "error_type": attrs.get("error_type", ""),
        "cost_usd": attrs.get("cost_usd", ""),
        "duration_ms": attrs.get("duration_ms", ""),
        "input_tokens": attrs.get("input_tokens", ""),
        "output_tokens": attrs.get("output_tokens", ""),
        "cache_read_tokens": attrs.get("cache_read_tokens", ""),
        "cache_creation_tokens": attrs.get("cache_creation_tokens", ""),
        "tool_use_id": attrs.get("tool_use_id", ""),
    }


# ---- spans ----

def iter_spans(obj):
    for rs in obj.get("resourceSpans", []):
        # session.id / model can live on the resource, not the span.
        res_attrs = {a["key"]: attr_value(a["value"])
                     for a in rs.get("resource", {}).get("attributes", [])}
        for ss in rs.get("scopeSpans", []):
            for span in ss.get("spans", []):
                yield span, res_attrs


def span_to_row(span, res_attrs):
    attrs = {a["key"]: attr_value(a["value"]) for a in span.get("attributes", [])}

    duration_ms = first_attr(attrs, "duration_ms", "interaction.duration_ms")
    if duration_ms == "":
        start, end = span.get("startTimeUnixNano"), span.get("endTimeUnixNano")
        if start and end:
            try:
                duration_ms = round((int(end) - int(start)) / 1e6, 3)
            except (TypeError, ValueError):
                duration_ms = ""

    return {
        "timestamp": nanos_to_iso(span.get("startTimeUnixNano")),
        "trace_id": span.get("traceId", ""),
        "span_id": span.get("spanId", ""),
        "parent_span_id": span.get("parentSpanId", ""),
        "span_name": span.get("name", ""),
        "session_id": first_attr(attrs, "session.id") or res_attrs.get("session.id", ""),
        # Empty on the main agent's spans; set to the subagent's id on spans a
        # dispatched subagent creates. This is how one session's trace tells the
        # Opus main apart from the Sonnet subagents it spawned.
        "agent_id": first_attr(attrs, "agent_id"),
        "model": first_attr(attrs, "model", "gen_ai.request.model", "llm.model_name"),
        "duration_ms": duration_ms,
        "input_tokens": first_attr(attrs, "input_tokens", "gen_ai.usage.input_tokens"),
        "output_tokens": first_attr(attrs, "output_tokens", "gen_ai.usage.output_tokens"),
        "cache_read_tokens": first_attr(attrs, "cache_read_tokens"),
        "cache_creation_tokens": first_attr(attrs, "cache_creation_tokens"),
        "ttft_ms": first_attr(attrs, "ttft_ms", "time_to_first_token_ms"),
        "success": first_attr(attrs, "success"),
        "stop_reason": first_attr(attrs, "stop_reason"),
    }


def write_csv(path, fieldnames, rows):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-file", default="data/claude-events.jsonl")
    parser.add_argument("--output-file", default="claude_usage.csv")
    parser.add_argument("--spans-output-file", default="claude_spans.csv")
    args = parser.parse_args()

    if not os.path.exists(args.input_file):
        print(f"{args.input_file} does not exist yet (no telemetry received)")
        return

    log_rows, span_rows = [], []
    with open(args.input_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            for record in iter_log_records(obj):
                row = record_to_row(record)
                if row:
                    log_rows.append(row)
            for span, res_attrs in iter_spans(obj):
                span_rows.append(span_to_row(span, res_attrs))

    write_csv(args.output_file, FIELDS, log_rows)
    write_csv(args.spans_output_file, SPAN_FIELDS, span_rows)
    print(f"Wrote {len(log_rows)} log rows to {args.output_file}, "
          f"{len(span_rows)} span rows to {args.spans_output_file}")


if __name__ == "__main__":
    main()
