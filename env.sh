# Source this before launching a new Claude Code session:
#   source env.sh && claude
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_LOGS_EXPORTER=otlp
export OTEL_METRICS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=grpc
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317

# Traces are a separate beta. The two below are both required: OTEL_TRACES_EXPORTER
# alone gets you nothing, Claude Code only creates spans when the beta flag is set too.
export CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1
export OTEL_TRACES_EXPORTER=otlp
