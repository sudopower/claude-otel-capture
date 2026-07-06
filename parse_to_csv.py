#!/usr/bin/env python3
"""Parse Claude Code OTel export (JSONL from the collector's file exporter)
into two flat CSVs: log events (api_request / tool_result / tool_decision)
and trace spans (claude_code.interaction / claude_code.llm_request, beta)."""
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

SPAN_FIELDS = [
    "timestamp", "trace_id", "span_id", "parent_span_id", "span_name",
    "session_id", "model", "duration_ms", "input_tokens", "output_tokens",
    "cache_read_tokens", "cache_creation_tokens", "ttft_ms", "success", "stop_reason",
]

TARGET_EVENTS = {"api_request", "tool_result", "tool_decision"}


def attr_value(v):
    for key in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if key in v:
            return v[key]
    return ""


def iter_log_records(obj):
    for rl in obj.get("resourceLogs", []):
        for sl in rl.get("scopeLogs", []):
            for record in sl.get("logRecords", []):
                yield record


def iter_spans(obj):
    for rs in obj.get("resourceSpans", []):
        for ss in rs.get("scopeSpans", []):
            for span in ss.get("spans", []):
                yield span


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


def span_to_row(span):
    attrs = {a["key"]: attr_value(a["value"]) for a in span.get("attributes", [])}
    start_ns = int(span.get("startTimeUnixNano", 0) or 0)
    end_ns = int(span.get("endTimeUnixNano", 0) or 0)
    duration_ms = attrs.get("duration_ms") or attrs.get("interaction.duration_ms")
    if duration_ms in (None, "") and start_ns and end_ns:
        duration_ms = round((end_ns - start_ns) / 1_000_000)
    timestamp = (
        datetime.fromtimestamp(start_ns / 1_000_000_000, tz=timezone.utc).isoformat()
        if start_ns else ""
    )
    return {
        "timestamp": timestamp,
        "trace_id": span.get("traceId", ""),
        "span_id": span.get("spanId", ""),
        "parent_span_id": span.get("parentSpanId", ""),
        "span_name": span.get("name", ""),
        "session_id": attrs.get("session.id", ""),
        "model": attrs.get("model", ""),
        "duration_ms": duration_ms if duration_ms is not None else "",
        "input_tokens": attrs.get("input_tokens", ""),
        "output_tokens": attrs.get("output_tokens", ""),
        "cache_read_tokens": attrs.get("cache_read_tokens", ""),
        "cache_creation_tokens": attrs.get("cache_creation_tokens", ""),
        "ttft_ms": attrs.get("ttft_ms", ""),
        "success": attrs.get("success", ""),
        "stop_reason": attrs.get("stop_reason", ""),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-file", default="data/claude-events.jsonl")
    parser.add_argument("--output-file", default="claude_usage.csv")
    parser.add_argument("--spans-output-file", default="claude_spans.csv")
    args = parser.parse_args()
    in_path = args.input_file

    if not os.path.exists(in_path):
        print(f"{in_path} does not exist yet (no telemetry received)")
        return

    rows = []
    span_rows = []
    with open(in_path) as f:
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
                    rows.append(row)
            for span in iter_spans(obj):
                span_rows.append(span_to_row(span))

    with open(args.output_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    with open(args.spans_output_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SPAN_FIELDS)
        writer.writeheader()
        writer.writerows(span_rows)

    print(f"Wrote {len(rows)} rows to {args.output_file}, {len(span_rows)} rows to {args.spans_output_file}")


if __name__ == "__main__":
    main()
