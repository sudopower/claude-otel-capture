#!/usr/bin/env python3
"""Parse Claude Code OTel log events (JSONL from the collector's file exporter)
into a flat CSV: one row per api_request / tool_result / tool_decision event."""
import argparse
import csv
import json
import os

FIELDS = [
    "timestamp", "session_id", "event_type", "model", "tool_name", "decision",
    "source", "success", "error_type", "cost_usd", "duration_ms",
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens",
    "tool_use_id",
]

TARGET_EVENTS = {"api_request", "tool_result", "tool_decision"}


def attr_value(v):
    for key in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if key in v:
            return v[key]
    return ""


def iter_log_records(line):
    obj = json.loads(line)
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-file", default="data/claude-events.jsonl")
    parser.add_argument("--output-file", default="claude_usage.csv")
    args = parser.parse_args()
    in_path = args.input_file
    out_path = args.output_file

    if not os.path.exists(in_path):
        print(f"{in_path} does not exist yet (no telemetry received)")
        return

    rows = []
    with open(in_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                for record in iter_log_records(line):
                    row = record_to_row(record)
                    if row:
                        rows.append(row)
            except json.JSONDecodeError:
                continue

    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {out_path}")


if __name__ == "__main__":
    main()
