#!/usr/bin/env bash
set -euo pipefail

# Number of parallel workers (shards) can be supplied as the first argument.
# Defaults to 3 if not provided.
MAX_WORKERS="${1:-3}"

# Centralised location for all stage-specific log snapshots.
ARTIFACTS_DIR="${ROBOT_ARTIFACTS:-output}"

preserve_logs() {
  local dest_rel="${1:-}"
  if [ -z "$dest_rel" ]; then
    return 0
  fi

  local src_html="${ARTIFACTS_DIR}/log.html"
  if [ ! -f "$src_html" ]; then
    echo "[start] No log.html available to preserve for ${dest_rel}"
    return 0
  fi

  local dest_path="${ARTIFACTS_DIR}/${dest_rel}"
  mkdir -p "$(dirname "$dest_path")"
  cp -f "$src_html" "$dest_path"
  echo "[start] Preserved HTML log ${dest_rel}"

  local dest_stem="${dest_path%.*}"
  local src_robo="${ARTIFACTS_DIR}/output.robolog"
  if [ -f "$src_robo" ]; then
    cp -f "$src_robo" "${dest_stem}.robolog"
    echo "[start] Preserved robolog ${dest_stem}.robolog"
  fi

  local src_robo2="${ARTIFACTS_DIR}/output_2.robolog"
  if [ -f "$src_robo2" ]; then
    cp -f "$src_robo2" "${dest_stem}_2.robolog"
    echo "[start] Preserved robolog ${dest_stem}_2.robolog"
  fi
}

# Optional organization name can be provided via the ORG_NAME environment
# variable. It falls back to the value defined in the producer environment file.

mkdir -p devdata/work-items-in/input-for-producer

# Only create/overwrite work-items.json if ORG_NAME is provided OR if the file doesn't exist
WORK_ITEMS_FILE="devdata/work-items-in/input-for-producer/work-items.json"
if [ -n "${ORG_NAME:-}" ]; then
  echo "[{\"payload\": {\"org\": \"${ORG_NAME}\"}}]" > "$WORK_ITEMS_FILE"
elif [ ! -f "$WORK_ITEMS_FILE" ]; then
  # Create a default work item only when the file doesn't exist and ORG_NAME isn't provided.
  # The organization will then be read from env-for-producer.json
  # by the producer task.
  echo "[{\"payload\": {}}]" > "$WORK_ITEMS_FILE"
fi

# Run producer step
rcc run -t "Producer" -e devdata/env-for-producer.json
preserve_logs "producer-to-consumer/producer-logs.html"

# Generate shards based on the desired worker count
python3 scripts/generate_shards_and_matrix.py "$MAX_WORKERS"

# Iterate over generated shard files and run the consumer for each shard.
consumer_logs_captured=false
for SHARD_PATH in output/shards/work-items-shard-*.json; do
  [ -e "$SHARD_PATH" ] || continue
  SHARD_ID="$(basename "$SHARD_PATH" | grep -oE '[0-9]+')"
  SHARD_INPUT_DIR="output/file/shards/shard-${SHARD_ID}"
  CONSUMER_OUTPUT_DIR="output/file/consumer-to-reporter/shard-${SHARD_ID}"
  CONSUMER_ENV="output/file/runtime-env/env-for-consumer-${SHARD_ID}.json"

  rm -rf "$SHARD_INPUT_DIR" "$CONSUMER_OUTPUT_DIR"
  mkdir -p "$SHARD_INPUT_DIR" "$CONSUMER_OUTPUT_DIR" "$(dirname "$CONSUMER_ENV")"
  cp "$SHARD_PATH" "${SHARD_INPUT_DIR}/work-items.json"

  cat > "$CONSUMER_ENV" <<EOF
{
  "RC_WORKITEM_ADAPTER": "FileAdapter",
  "RC_WORKITEM_INPUT_PATH": "$SHARD_INPUT_DIR",
  "RC_WORKITEM_OUTPUT_PATH": "$CONSUMER_OUTPUT_DIR"
}
EOF

  echo "Running consumer for shard ${SHARD_ID} using ${SHARD_INPUT_DIR}"
  SHARD_ID="$SHARD_ID" rcc run -t "Consumer" -e "$CONSUMER_ENV"
  preserve_logs "consumer-to-reporter/consumer-shard-${SHARD_ID}-logs.html"
  consumer_logs_captured=true
done

if [ "$consumer_logs_captured" = true ]; then
  preserve_logs "consumer-to-reporter/consumer-logs.html"
fi

mkdir -p output/file/reporter-input output/file/runtime-env
python3 scripts/combine_workitems.py

# Create environment configuration for reporter
REPORTER_ENV="output/file/runtime-env/env-for-reporter.json"
cat > "$REPORTER_ENV" <<EOF
{
  "RC_WORKITEM_ADAPTER": "FileAdapter",
  "RC_WORKITEM_INPUT_PATH": "output/file/reporter-input",
  "RC_WORKITEM_OUTPUT_PATH": "output/file/reporter-final"
}
EOF

# Run reporter step to process all consumer outputs
echo "Running reporter step"
rcc run -t "Reporter" -e "$REPORTER_ENV"
preserve_logs "reporter-logs.html"
