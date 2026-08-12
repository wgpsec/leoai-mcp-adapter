# LeoAI 1.x / 2.x Dual Protocol Profile Spec

- Status: Accepted
- Date: 2026-08-12
- Scope: `leoai-mcp-adapter`

## 1. Problem

LeoAI currently publishes a deployable `1.0.1` release, while its repository
`main` branch contains the `2.0.0` backend API and only compiled frontend
assets. The two releases expose different HTTP contracts. In particular:

- LeoAI 1.x SQL requests use top-level `database` and `table` fields;
- LeoAI 2.x SQL requests use a structured `objectRef`;
- some 2.x endpoints, including SQL runtime capabilities and transfer task
  removal, do not exist in 1.0.1.

Sending the 2.x DTO to 1.0.1 can be accepted by Spring while silently dropping
unknown fields, after which the old service fails with a null table name. An
HTTP failure is therefore not a safe signal for retrying with another DTO,
especially for write actions.

## 2. Decision

One adapter build supports two explicit, process-scoped protocol profiles:

```text
LEOAI_PROTOCOL_PROFILE=1x
LEOAI_PROTOCOL_PROFILE=2x
```

The default remains `2x` so existing adapter deployments keep their current
request contract. One adapter process binds one LeoAI origin and one protocol
profile for its full lifetime. A deployment that connects to both generations
runs two adapter instances with separate configuration, credentials, ports and
MCP registrations.

The adapter never:

- guesses a protocol from a UI, Maven or display version;
- changes protocol after startup;
- retries a failed request using the other protocol;
- replays an action after a protocol or capability error;
- exposes raw SQL to compensate for an unavailable structured endpoint.

## 3. Compatibility Boundary

Shared endpoints keep the existing implementation. Only confirmed protocol
drift is profile-aware.

| Capability | `1x` profile | `2x` profile |
|---|---|---|
| Login, Session, terminal, file and system tools | Existing shared contract | Existing shared contract |
| SQL dialect list and connection test | Existing endpoint | Existing endpoint |
| SQL metadata/query/row actions | `database` + `table` DTO | structured `objectRef` DTO |
| SQL runtime capabilities | `leoai_capability_unsupported` without an upstream call | Existing endpoint |
| Project API | Existing runtime capability error when absent | Existing endpoint |
| Transfer `pause/resume/cancel/retry` | Existing endpoints | Existing endpoints |
| Transfer `remove` | `leoai_capability_unsupported` without an upstream call | Existing endpoint |

For the 1.x SQL mapping, the public MCP schema remains structured. The adapter
maps `schema`, or otherwise `catalog`, to the legacy `database` field and maps
`name` to `table`. A table operation without a namespace is allowed because
some 1.x runtimes use the connection's default database. Ambiguous input that
contains different non-empty `catalog` and `schema` values is rejected instead
of guessing which legacy namespace to use.

The MCP tool names and input schemas remain stable across profiles. Unsupported
tools return the existing stable `leoai_capability_unsupported` error. This
avoids changing PoJun tool discovery, Project configuration or OODA scheduling.

## 4. Configuration And Readiness

`LEOAI_PROTOCOL_PROFILE` accepts only `1x` or `2x`. Invalid values fail process
configuration before any network request. `/healthz` remains process-only and
`/readyz` continues to verify LeoAI authentication.

LeoAI does not currently expose a reliable public API revision endpoint, so
readiness must not claim that it has proven the configured generation merely
from a display version or an ambiguous missing-route response. Contract
correctness is established by profile-specific automated tests and the release
validation matrix. A future stable upstream revision endpoint can add a strict
readiness check without changing the profile model.

## 5. Error And Retry Semantics

- Unsupported profile capabilities fail locally with
  `leoai_capability_unsupported` and `retryable=false`.
- Query requests retain the existing bounded authentication retry.
- Actions retain `retry_on_auth_expiry=false`.
- A 1.x request is never followed by a 2.x request, or vice versa.
- Error output must not expose credentials, cookies, connection secrets or
  upstream stack traces.

## 6. TDD And Verification

Implementation proceeds as vertical behavior slices:

1. configuration accepts `1x` and `2x`, defaults to `2x`, and rejects unknown
   profiles;
2. existing 2.x SQL request fixtures remain byte-for-byte equivalent;
3. 1.x metadata and bounded row query requests use legacy DTO fields;
4. 1.x insert/update/delete actions use legacy DTO fields and preserve the
   no-replay contract;
5. 1.x-only unsupported capabilities fail without contacting LeoAI;
6. both profile matrices retain response sanitization and MCP tool schemas;
7. the complete adapter test suite and lint gate pass.

Release validation uses one official LeoAI 1.0.1 deployment and one controlled
LeoAI 2.x build. Actions use synthetic canaries only. A profile is documented as
verified only after its own matrix passes; success in one profile is not
evidence for the other.

### 6.1 LeoAI 1.0.1 Live Validation

On 2026-08-12, the `1x` profile was replayed against an official LeoAI `1.0.1`
deployment with a temporary loopback PHP Puppet and synthetic `/tmp` data. The
following paths passed through the MCP transport and the real LeoAI runtime:

- authentication, 68-Tool discovery, Puppet connectivity and Session lifecycle;
- capability, basic information and legacy file-profile fallback;
- file create, bounded read, content verification and delete;
- terminal open, write, incremental read and stop. LeoAI 1.x commits PTY input
  with `\r` and returns terminal bytes as Base64; the decoded output contained
  the expected canary;
- SQLite connection test, database/table/column metadata, bounded query, and
  insert/update/delete with final empty-state verification;
- local `leoai_capability_unsupported` gates for SQL runtime capabilities and
  transfer removal, with no cross-profile upstream fallback.

The replay removed its database connection, Session, Puppet, loopback server
and synthetic files. A final read-only probe reported zero Puppets and zero
Sessions.

### 6.2 LeoAI 2.0.0 Release Validation

On 2026-08-12, the `2x` profile was replayed against the published
`LeoAi-2.0.0.jar`. Authentication, 68-Tool discovery, Project and Session APIs,
Session capabilities, basic host information, process listing, service listing,
network-connection listing, terminal lifecycle, database connection management,
database connection testing, and the 2.x SQL runtime-capabilities endpoint all
passed through the standard MCP transport. The transfer-remove probe reached
the 2.0.0 endpoint instead of the 1.x local capability gate.

The initially available Java Puppet was an existing remote target, not a Puppet
generated from this 2.0.0 deployment. Its terminal reported
`backend=unix-pipe` and `pty=false`; newline input produced the expected decoded
canary, while carriage return did not submit the command. The following checks
failed on that mixed-version target:

- file profile and directory creation failed because the Puppet required an
  integer action while the 2.0.0 Server sent `profile` and `createDirectory`;
  the legacy `/file/list-root` fallback is absent in 2.0.0, so the Adapter must
  not hide this runtime mismatch with another fallback;
- SQLite metadata and row operations could not proceed because that Java
  runtime reported `org.sqlite.JDBC` unavailable, although connection testing
  and runtime capability reporting behaved correctly;
- Docker information returned an upstream failure on that target.

To resolve attribution, the same `LeoAi-2.0.0.jar` runtime generator produced a
fresh protocol-v2 PHP Puppet using `inner_PHP_JSON_API_1.0.0`. It ran on PHP 8.2
with `pdo_sqlite` and passed the complete Adapter matrix:

- Puppet connectivity, Session lifecycle, capabilities and basic information;
- file profile, bounded create/read/content verification/delete;
- PTY terminal open/write/incremental read/stop with decoded canary output;
- SQLite connection test and runtime-capability reporting;
- 2.x `objectRef` database/table/column metadata, bounded query, insert, update,
  delete and final-state verification;
- transfer removal reached the 2.0.0 endpoint instead of the 1.x local gate.

This confirms that the Adapter's `2x` request contract is compatible with the
published 2.0.0 release. The earlier failures belong to a mixed Server/Puppet
runtime combination and missing target dependencies, not to Adapter DTO
mapping. Deployments must regenerate or otherwise version-match Puppets after
the 2.0.0 protocol change. All temporary Puppets, Sessions, database profiles,
terminal processes, loopback services and synthetic files were removed. PoJun
end-to-end integration remains a separate release check.

## 7. Non-Goals

- modifying or distributing LeoAI frontend source;
- maintaining a LeoAI backend fork as the adapter's production baseline;
- automatic protocol negotiation or per-request switching;
- adding an API gateway, sidecar or another compatibility service;
- implementing unsupported LeoAI features inside the adapter.
