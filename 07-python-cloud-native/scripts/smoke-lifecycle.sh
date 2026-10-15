#!/usr/bin/env bash
# Producer -> loopback provider -> fresh online home -> offline .rcca import.
# Run from any directory. Requires Bash, jq and RCC v18.19.6 or newer.
set -Eeuo pipefail
umask 077
cd "$(dirname "$0")/.."

for tool in rcc jq; do
  command -v "$tool" >/dev/null 2>&1 || { echo "Missing $tool" >&2; exit 2; }
done
backend="${RCC_PROVIDER_BACKEND:-filesystem}"
case "$backend" in
  filesystem|journal) ;;
  *) echo "RCC_PROVIDER_BACKEND must be filesystem or journal" >&2; exit 2 ;;
esac
root="$(mktemp -d)"
producer="$root/producer"
online="$root/online-worker"
offline="$root/offline-worker"
trust="$root/detached-trust"
mkdir -p "$producer" "$online" "$offline" "$trust"
server_pid=""
stop_server() {
  if [[ -n "$server_pid" ]]; then
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
    server_pid=""
  fi
}
trap stop_server EXIT
export RCC_HOLOTREE_MODE=private
export ROBOCORP_HOME="$producer"

echo "[1/9] Execute source task using RCC"
rcc run -r robot.yaml -t RunTask --silent
test -s output/cloud-native-smoke.json

echo "[2/9] Start self-hosted loopback provider ($backend)"
rcc cache serve --listen 127.0.0.1:0 --backend "$backend" --json \
  > "$root/provider-start.json" 2> "$root/provider-stderr.log" &
server_pid="$!"
for _ in $(seq 1 100); do
  if jq -er '.url' "$root/provider-start.json" >/dev/null 2>&1; then break; fi
  if ! kill -0 "$server_pid" 2>/dev/null; then
    cat "$root/provider-stderr.log" >&2
    echo "Provider exited before startup" >&2
    exit 1
  fi
  sleep 0.1
done
provider_url="$(jq -er '.url | select(startswith("http://127.0.0.1:"))' "$root/provider-start.json")"
rcc provider add local-demo --type http --url "$provider_url" --json > "$root/producer-provider.json"
rcc provider test local-demo --json > "$root/provider-test.json"

echo "[3/9] Publish environment and detached trust metadata"
rcc env publish --robot robot.yaml --provider local-demo \
  --trust-carrier "$trust" --trust-carrier-type filesystem --json > "$root/publish.json"
digest="$(jq -er '.artifactDigest | select(test("^sha256:[0-9a-f]{64}$"))' "$root/publish.json")"

echo "[4/9] Export .rcca immutable offline archive"
rcc env export --artifact "$digest" --provider local-demo \
  --output "$root/environment.rcca" > "$root/export.json"
test -s "$root/environment.rcca"

echo "[5/9] Strict trust MUST reject unsigned local demo"
export ROBOCORP_HOME="$online"
rcc provider add local-demo --type http --url "$provider_url" --json > "$root/online-provider.json"
if rcc env acquire --artifact "$digest" --provider local-demo \
  --trust-carrier "$trust" --trust-carrier-type filesystem \
  --strict-remote --json > "$root/strict-rejection.log" 2>&1; then
  echo "FAIL: strict-remote accepted unsigned local artifact" >&2
  exit 1
fi

echo "[6/9] Acquire into online worker and run a leased interpreter"
rcc env acquire --artifact "$digest" --provider local-demo \
  --trust-carrier "$trust" --trust-carrier-type filesystem \
  --permissive-local --json > "$root/online-acquire.json"
jq -e --arg d "$digest" '.artifactDigest == $d and (.materializationId | length > 0)' \
  "$root/online-acquire.json" >/dev/null
rcc env exec --artifact "$digest" --provider local-demo \
  --trust-carrier "$trust" --trust-carrier-type filesystem \
  --permissive-local --inherit-streams --receipt-file "$root/online-exec.json" \
  -- python -c 'import robocorp.tasks, sys; print("online lease:", sys.version.split()[0])'
jq -e --arg d "$digest" \
  '.artifactDigest == $d and .status == "completed" and .exitCode == 0 and (.leaseId | length > 0)' \
  "$root/online-exec.json" >/dev/null
rcc env lifecycle inspect --artifact "$digest" --json > "$root/online-lifecycle.json"

echo "[7/9] Kill provider and verify warm reuse with same trust carrier"
stop_server
rcc env acquire --artifact "$digest" --provider local-demo \
  --trust-carrier "$trust" --trust-carrier-type filesystem \
  --permissive-local --json > "$root/warm-acquire.json"
jq -e --arg d "$digest" '.artifactDigest == $d' "$root/warm-acquire.json" >/dev/null

echo "[8/9] Third home: import .rcca without provider"
export ROBOCORP_HOME="$offline"
rcc env acquire --archive "$root/environment.rcca" \
  --trust-carrier "$trust" --trust-carrier-type filesystem \
  --permissive-local --json > "$root/offline-acquire.json"
jq -e --arg d "$digest" '.artifactDigest == $d' "$root/offline-acquire.json" >/dev/null

echo "[9/9] Execute from imported artifact with no provider"
rcc env exec --artifact "$digest" \
  --trust-carrier "$trust" --trust-carrier-type filesystem --permissive-local \
  --json -- python -c 'import robocorp.tasks, sys; assert sys.version_info[:2] == (3, 12)' \
  > "$root/offline-exec.json"
jq -e --arg d "$digest" \
  '.artifactDigest == $d and .status == "completed" and .exitCode == 0' \
  "$root/offline-exec.json" >/dev/null
echo "PASS: source -> publish -> strict refusal -> leased exec -> warm reuse -> .rcca offline"
echo "Artifact: $digest"
echo "Evidence and environment.rcca: $root"
echo "permissive-local is for local loopback development ONLY."
