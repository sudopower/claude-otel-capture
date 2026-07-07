# observe-claude: local OTel capture for Claude Code sessions.
# Wraps the collector lifecycle and a telemetry-enabled session so the setup
# is one command each. Telemetry env vars live in env.sh (single source of
# truth); `make session` sources it before launching claude.
#
# Typical flow, two terminals:
#   terminal 1:  make up          # start collector + csv-writer, leave running
#   terminal 2:  make session     # a Claude Code session exporting to it
# CSVs regenerate every 5s while the stack is up; `make down` when finished.

.PHONY: up down restart session csv ps logs reset clean help
.DEFAULT_GOAL := help

## up: start the collector + csv-writer (leave running)
up:
	docker compose up -d
	@echo "Collector up on localhost:4317. In another terminal: make session"

## down: stop and remove the collector + csv-writer
down:
	docker compose down

## restart: restart the collector (the safe way to reset; never rm the jsonl while it is live)
restart:
	docker compose restart otel-collector

## session: launch a telemetry-enabled Claude Code session (pass extra flags with ARGS=...)
session:
	. ./env.sh && claude $(ARGS)

## csv: regenerate both CSVs once from the current capture (the poller does this every 5s)
csv:
	python3 parse_to_csv.py --input-file data/claude-events.jsonl \
		--output-file claude_usage.csv --spans-output-file claude_spans.csv

## ps: show collector container status
ps:
	docker compose ps

## logs: follow the collector logs
logs:
	docker compose logs -f otel-collector

## reset: stop, clear the raw capture and CSVs, then start fresh
reset:
	docker compose down
	rm -f data/claude-events.jsonl claude_usage.csv claude_spans.csv
	docker compose up -d
	@echo "Fresh capture. Run: make session"

## clean: stop the stack and remove generated CSVs (keeps the raw capture)
clean:
	docker compose down
	rm -f claude_usage.csv claude_spans.csv

## help: list targets
help:
	@grep -E '^## ' $(MAKEFILE_LIST) | sed -e 's/## //'
