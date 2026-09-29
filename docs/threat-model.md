# STRIDE Threat Model

Scope: the ingestion path (OEM clouds -> Kafka -> pipeline) and the public API and dashboard.
"Status" is honest. **Implemented** means it is in the code and tested. **Designed** means it is in
the manifests or Terraform but not exercised in the hackathon environment. **Gap** means it is not
done.

| # | STRIDE | Threat | Control | Status |
|---|---|---|---|---|
| 1 | Spoofing | Forged API caller | JWT (HS256, 1 h expiry, issuer checked). PBKDF2-SHA256 password hashes (200K rounds). Login rate limit per client IP. | Implemented, tested (`tests/test_api.py`) |
| 2 | Spoofing | Forged OEM or device telemetry | mTLS between OEM clouds and the ingest endpoint. MSK client-broker TLS. The identity resolver validates each VIN (17 characters, no I/O/Q, ISO 3779 check digit) and routes invalid, unknown or VIN-less messages to `vehicle.dlq` with a reason. | VIN validation and DLQ implemented and tested (`tests/test_resolver_dlq.py`). TLS designed (Terraform MSK `client_broker = TLS`). mTLS is a gap. |
| 3 | Tampering | Replayed or duplicated events inflate risk | Dedup identity (vehicle_id, seq): Bloom filter plus Redis authority. Atomic Lua dedup and feature update. Idempotent sinks. | Implemented, tested (parity with 15 percent duplicates) |
| 4 | Tampering | SQL injection through the API | Only bound parameters. Input patterns on query parameters (component enum, VIN charset). Opaque cursors decoded and type-cast in SQL. | Implemented. Semgrep and Bandit clean. |
| 5 | Repudiation | User denies acknowledging an alert or accessing data | `audit_log` row for every authenticated data request, every login, every acknowledgement, every alert created (rule and ML) and every erasure, with actor, action, path and time. | Implemented, tested |
| 6 | Information disclosure | Tenant A reads tenant B's vehicles | Every query is scoped through vehicle -> fleet -> tenant. Admin is the only cross-tenant role. | Implemented, tested (404 across tenants) |
| 7 | Information disclosure | Precise location exposure (DPDP / GDPR) | Location rounded to about 1 km for non-admin roles. Driver erasure endpoint. No real personal data (synthetic only). | Implemented, tested |
| 8 | Information disclosure | Secrets in the repository or images | `.env` is gitignored and `.env.example` holds placeholders. External Secrets from the cloud secret manager in Kubernetes. Semgrep `p/secrets` in CI. | Implemented (0 secret findings) |
| 9 | Denial of service | API flooding | Redis fixed-window rate limit per principal (429 with Retry-After). Page size capped at 200. HPA on the API. | Implemented, tested |
| 10 | Denial of service | Burst of 3x telemetry | Kafka buffers the burst. Consumers commit only after processing (back-pressure through lag). Stream consumers scale up to the partition count. | Designed. Burst injection is not load-tested. |
| 11 | Elevation of privilege | Viewer performs write actions | Role checks on each endpoint: acknowledge needs ADMIN or FLEET_MANAGER; audit and erasure need ADMIN. | Implemented, tested (403) |
| 12 | Elevation of privilege | Container breakout | Non-root user (uid 10001), read-only root filesystem, all capabilities dropped, seccomp RuntimeDefault, restricted pod security, NetworkPolicy default deny. | Designed (manifests). The API image runs as non-root locally. |

## AI assistant (agentic component)

| Threat | Control | Status |
|---|---|---|
| Prompt injection makes the model reveal other tenants' data | Tools run the API's own tenant-scoped SQL with the tenant taken from the JWT; the model cannot pass a tenant | Implemented, tested (viewer cannot see a tenant 1 VIN) |
| Model invokes unintended actions | Allow-list of four read-only tools; unknown tools rejected; arguments validated (limits capped at 20, VIN regex, enums) | Implemented, tested |
| Runaway cost or latency | 500-character questions, at most 4 model steps, 20 s timeout, standard API rate limit | Implemented |
| Unaccountable AI answers | Every question, tool call, mode (LLM or rules) and latency is written to audit_log | Implemented, tested |
| LLM provider outage | Deterministic rules fallback answers from the same tools | Implemented, tested |

## Encryption
- **In transit:** TLS 1.3 at the ingress (annotation in `infra/k8s/base/api-service.yaml`); TLS to
  MSK and ElastiCache.
- **At rest:** RDS and ElastiCache encryption, and EKS secrets envelope encryption with KMS
  (AES-256).
- **Local docker compose** runs without TLS. That is a known gap for local development only.

## Top five residual risks
1. HS256 uses a shared secret. The production path is OIDC with RS256 tokens from an identity
   provider.
2. Device mTLS is not implemented.
3. The audit log is written asynchronously in batches (at most about 1 s of rows can be lost on a
   hard crash). A write-ahead outbox would close this.
4. The rate limiter fails open when Redis is down. This is a deliberate availability choice.
5. The dashboard stores the JWT in `sessionStorage`, which is exposed to XSS. All rendered strings
   are HTML-escaped, but httpOnly cookies with CSRF protection would be stronger.
