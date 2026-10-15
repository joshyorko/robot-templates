# Production deployment: immutable environments without Control Room

The local smoke intentionally uses unsigned artifacts; do not copy its
`--permissive-local` policy to a remote worker. For RCC >= v18.19.6,
use these independently owned boundaries:

| Component | Ownership |
| --- | --- |
| `robot.yaml` and `tasks.py` | Versioned source checkout or reviewed robot bundle |
| `sha256:` Environment Artifact / `.rcca` | Immutable, platform-specific environment |
| `rcc cache serve` | Your own provider host, loopback behind a TLS/auth proxy |
| Detached trust carrier | Separate trusted deployment channel for provenance, SBOM, signatures and revocations |
| Ed25519 public trust roots | Deployment-managed worker configuration; never learned from provider |
| `ROBOCORP_HOME` | A private path for each worker, its materialization and leases |

## Serve your own provider (operator-run)

```sh
export ROBOCORP_HOME=/var/lib/rcc/provider
rcc cache serve --listen 127.0.0.1:8787 \
  --root /var/lib/rcc/provider-objects --backend journal \
  --max-bytes 107374182400 --requests-per-second 100 --json
```

Use an authenticated HTTPS reverse proxy such as
`https://artifacts.example.com`. Never bind the raw provider to the
public interface. `--backend filesystem` is also supported, with immutable
identity unchanged. Provider byte/object/request caps are not tenant IAM.

## Publish with a deployment-owned signing key

A secret manager must provide `RCC_ARTIFACT_AUTHORIZATION` as the **complete**
Authorization header (scheme plus token), never a raw token. The CLI stores
only the *name* of the environment variable, not its contents.

```sh
export ROBOCORP_HOME=/var/lib/rcc/publisher
export RCC_HOLOTREE_MODE=private
export RCC_PROVIDER_AUTHORIZATION="$RCC_ARTIFACT_AUTHORIZATION"
rcc provider add prod --type http \
  --url https://artifacts.example.com \
  --authorization-env RCC_PROVIDER_AUTHORIZATION --json
rcc provider test prod --json
rcc env publish --robot robot.yaml --provider prod \
  --trust-carrier /var/lib/rcc/release-trust --trust-carrier-type filesystem \
  --signing-key /run/secrets/rcc-ed25519-private.key \
  --signing-key-id org-release-2026 --json
```

Record the exact `artifactDigest`. Transfer the entire detached carrier
through a **separate trusted channel**: the named provider profile does not
automatically identify or serve that carrier, and its Authorization header
does not authorize the trust endpoint.

## Strict remote worker

Deploy the approved carrier to `/etc/rcc/artifact-trust` and provision
`/etc/rcc/trust-roots.json` as JSON mapping signer IDs to base64-encoded
**32-byte Ed25519 public keys**, for example
`{"org-release-2026":"BASE64_PUBLIC_KEY_FROM_YOUR_PKI"}`.
Never commit private keys, credentials, or fake public trust roots.

```sh
export ROBOCORP_HOME=/var/lib/rcc/worker-01
export RCC_HOLOTREE_MODE=private
export RCC_PROVIDER_AUTHORIZATION="$RCC_ARTIFACT_AUTHORIZATION"
ARTIFACT_DIGEST=sha256:REPLACE_WITH_REAL_DIGEST
rcc provider add prod --type http \
  --url https://artifacts.example.com \
  --authorization-env RCC_PROVIDER_AUTHORIZATION --json
rcc env acquire --artifact "$ARTIFACT_DIGEST" --provider prod \
  --trust-carrier /etc/rcc/artifact-trust --trust-carrier-type filesystem \
  --trust-roots /etc/rcc/trust-roots.json --strict-remote --json

# Child streams remain intact; the typed receipt is installed after exit.
rcc env exec --artifact "$ARTIFACT_DIGEST" --provider prod \
  --trust-carrier /etc/rcc/artifact-trust --trust-carrier-type filesystem \
  --trust-roots /etc/rcc/trust-roots.json --strict-remote \
  --inherit-streams --receipt-file /var/lib/rcc/worker-01/exec-receipt.json \
  -- python -c 'import robocorp.tasks; print("trusted artifact interpreter")'
```

New leases re-evaluate revocation evidence. A warm local materialization
does not remove the need to supply the correct detached trust carrier.
Provider credentials authenticate transport, not signature authority.

## Offline .rcca and source bundles

```sh
# Publisher: export immutable environment from the named provider.
rcc env export --artifact "$ARTIFACT_DIGEST" --provider prod \
  --output environment.rcca

# New offline worker: use the transferred archive AND detached trust.
rcc env acquire --archive environment.rcca \
  --trust-carrier /etc/rcc/artifact-trust --trust-carrier-type filesystem \
  --trust-roots /etc/rcc/trust-roots.json --strict-remote --json

# Optionally package audited source alongside the archive.
rcc robot bundle -r robot.yaml -o cloud-native-bundle.py \
  --artifact-archive environment.rcca
```

`rcc robot bundle` embeds `environment/artifact.rcca` and retains its
traditional source+Holotree payload. Use `--artifact-index` only with a
canonical multi-platform index you built separately. Verify bundle replay
and trust enforcement in the target deployment; a bundle is not automatically
a universal executable or a substitute for strict worker policy.

## Operational inspection and advanced coordination

```sh
rcc provider list --json
rcc provider inspect prod --json
rcc env lifecycle inspect --artifact "$ARTIFACT_DIGEST" --json
rcc env lifecycle verify --artifact "$ARTIFACT_DIGEST" --json
# Use repair only after diagnosing a local state problem:
rcc env lifecycle repair --artifact "$ARTIFACT_DIGEST" --provider prod --json
```

Optional `rcc env coordinate claim|heartbeat|wait|release|prewarm`
coordinates bounded cold builds using JSON machine contracts. Claims and
worker capacity controls **never** make unverified bytes authoritative.
See [coordination](https://github.com/joshyorko/rcc/blob/main/docs/environment-build-coordination.md)
for owner/epoch fencing, validated completion receipts and prewarm policy.
Use private worker homes and verified artifact rollouts; no shared writable
fleet Holotree or Kubernetes broker is required.

The legacy `rccremote` `/parts` and `/delta` protocol remains compatible
but is a **different service**. This provider is not a renamed rccremote.
