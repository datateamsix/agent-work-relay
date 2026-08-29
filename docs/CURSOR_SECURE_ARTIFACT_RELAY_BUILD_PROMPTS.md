# Cursor build prompts: secure artifact relay

## Purpose

This packet defines the next Agent Work Relay milestone after the Markdown-only
prompt-to-plan golden path. It expands AWR into a small, security-gated work
package relay without turning it into a general-purpose file-transfer service.

The build is divided into four ordered prompts. Each prompt must be planned,
reviewed, implemented, and verified before the next begins.

## Product boundary

AWR should relay a decorated Markdown work order plus a small number of
purpose-labeled supporting artifacts. Every inbound artifact is untrusted,
regardless of whether it came from ChatGPT, Claude, a human, or another agent.

The first release supports:

| Format | Initial policy |
|---|---|
| Markdown and plain text | UTF-8 only; size and control-character limits |
| JSON | Parse safely; limit nesting and collection sizes |
| YAML | Safe loader only; reject custom tags and alias abuse |
| PNG and JPEG | Verify bytes; decode safely; limit dimensions and pixels |
| PDF | Reject encryption, scripts, launch actions, and embedded files |

The first release rejects archives, ZIP files, executables, Office documents,
SVG, encrypted files, and unknown binary types.

Default limits are configurable but fail closed:

```text
Primary Markdown:       256 KiB
Individual artifact:     10 MiB
Total work package:      25 MiB
Artifacts per order:     10
Security mode:           enforce
```

## Non-negotiable security rules

1. Quarantined bytes are never visible to an executor.
2. An artifact cannot be relayed unless its current immutable version has a
   `CLEAN` security verdict.
3. The SHA-256 digest is calculated while streaming bytes into quarantine and
   verified again before delivery.
4. Declared filename extensions and media types are hints. Detected content is
   authoritative, and disagreement fails closed.
5. Original bytes are immutable. Sanitized or extracted representations are
   separate derived artifacts with their own identifiers and fingerprints.
6. Artifact content is untrusted reference material. It cannot select an AWR
   directive, repository, branch, executor, mode, permissions, or approval.
7. Only the primary decorated Markdown work order can declare `@awr` intent.
8. Scanner unavailability, timeout, malformed output, or an inconclusive result
   blocks relay.
9. Do not send customer artifacts to third-party scanning services by default.
10. Never log raw artifact bytes, signed URLs, authorization headers, secrets,
    or extracted document bodies.
11. Every acceptance, scan, promotion, rejection, and delivery produces an
    immutable receipt.
12. Existing Markdown-only clients and `AWR-GT-001` remain backward compatible.

## Target artifact lifecycle

```text
DECLARED
  -> QUARANTINED
  -> SCANNING
  -> CLEAN
  -> READY_TO_RELAY
  -> RELAYED
```

Terminal rejection states:

```text
REJECTED_SIZE
REJECTED_TYPE
REJECTED_MALWARE
REJECTED_ACTIVE_CONTENT
REJECTED_MALFORMED
REJECTED_TAMPERING
REJECTED_SCANNER_UNAVAILABLE
```

## Prompt 1 — Artifact contracts and local quarantine

Use this prompt for the first Cursor planning and implementation run.

```text
@awr feature.plan

# AWR-AS-01: Artifact contracts and local quarantine

Implement the domain and local-storage foundation for secure supporting
artifacts in Agent Work Relay.

Repository:
https://github.com/datateamsix/agent-work-relay

Read before planning:
- AGENTS.md
- README.md
- docs/ARCHITECTURE.md
- docs/AWR-GT-001.md
- docs/CURSOR_SECURE_ARTIFACT_RELAY_BUILD_PROMPTS.md
- src/awr/contracts.py
- src/awr/service.py
- src/awr/storage/base.py
- src/awr/storage/sqlite.py

First work in planning mode. Produce a repository-aware plan naming the exact
contracts, ports, schema changes, migrations, tests, and files you expect to
change. Do not edit files until the plan is approved.

After approval, implement the smallest artifact foundation without exposing a
new remote upload or executor-delivery path yet.

Required domain contracts:
- ArtifactStatus with the lifecycle and rejection states defined in the build
  packet.
- ArtifactPurpose with an intentionally small initial vocabulary such as
  design_reference, data_contract, requirements_reference, and other_reference.
- Artifact metadata containing artifact ID, owner/sender identity, original
  filename as metadata, declared media type, detected media type when known,
  byte length, SHA-256, purpose, status, creation time, and optional parent
  artifact ID for future derived artifacts.
- ArtifactSecurityReceipt containing scanner identity, scanner version,
  signature/rule-set version, verdict, reason codes, scanned SHA-256, start and
  completion timestamps, and safe diagnostic metadata.
- ArtifactReference suitable for inclusion in a future work bundle. It must not
  contain raw bytes or a permanent public URL.

Required ports:
- ArtifactMetadataStore for immutable metadata, state transitions, security
  receipts, ownership checks, and idempotent lookup.
- ArtifactBodyStore for streaming bytes into quarantine, opening a quarantined
  object for scanning, promoting the exact clean version, opening only clean
  objects for delivery, and deleting expired objects according to policy.
- Keep provider SDKs out of domain contracts.

Local implementation:
- Store metadata in SQLite with migrations that preserve existing databases.
- Store bytes outside SQLite under a configurable local artifact root.
- Use separate quarantine and clean locations.
- Derive storage paths from server-generated IDs or content digests, never from
  user-supplied filenames.
- Stream to a temporary file, calculate SHA-256 and size during the write, fsync
  where practical, and atomically finalize the quarantine object.
- Enforce 10 MiB per artifact and reject bytes beyond the configured limit
  without leaving a usable partial object.
- Make artifact declaration and byte finalization idempotent.
- Never expose a quarantined path through the clean-object read API.
- Never overwrite an existing immutable artifact body.

Receipts:
- Record artifact.declared and artifact.quarantined events with correlation ID,
  actor, byte length, media-type declarations, and fingerprint.
- Do not place raw bytes, filesystem paths, or secrets in receipts.
- If the existing work-order ledger cannot safely represent pre-work-order
  artifact events, add a typed artifact receipt journal rather than fabricating
  a work-order ID. Document how it will correlate with a work order later.

Compatibility:
- Do not change the behavior or signature of submit_prompt_for_planning.
- Do not change Cursor dispatch behavior.
- Do not add an attachment to a wrapped prompt in this slice.
- Preserve all AWR-GT-001 states, hashes, receipts, and replay behavior.

Tests must prove:
- streamed content receives the correct SHA-256 and byte count;
- the same idempotency key returns the same artifact;
- oversize content fails closed and leaves no readable clean object;
- path traversal filenames cannot affect storage paths;
- quarantined content cannot be opened through the clean-object API;
- immutable content cannot be overwritten;
- SQLite migration works from the current schema;
- concurrent finalization does not create conflicting artifact versions; and
- every existing test remains green.

Before completion run Ruff formatting and linting, strict mypy, the complete
test suite, package build, and AWR-GT-001.

Return a completion packet containing the branch and commit, files changed,
contract decisions, schema and migration details, exact test results, known
limitations, and the recommended starting point for AWR-AS-02. After approval,
commit and push with a normal fast-forward update to the operator-authorized
branch. Never force-push or overwrite concurrent work.
```

## Prompt 2 — Enforced security gate and format validation

Run this only after Prompt 1 is merged and verified.

```text
@awr feature.plan

# AWR-AS-02: Enforced artifact security gate

Implement the fail-closed security inspection and malware-scanning pipeline for
AWR artifacts.

Repository:
https://github.com/datateamsix/agent-work-relay

Read before planning:
- AGENTS.md
- README.md
- docs/ARCHITECTURE.md
- docs/CURSOR_SECURE_ARTIFACT_RELAY_BUILD_PROMPTS.md
- the merged AWR-AS-01 contracts, ports, migrations, and tests

Begin in planning mode. Inspect the implementation delivered by AWR-AS-01 and
produce an exact plan before changing files. Identify external libraries and
their transitive/native-runtime implications. Do not invent scanner APIs.

Implement a provider-neutral security pipeline with typed ports for:
- byte-based media-type detection;
- format-specific structural validation;
- malware scanning; and
- policy evaluation that produces one final fail-closed verdict.

Security pipeline:
1. Confirm the quarantined object generation and SHA-256.
2. Enforce file-count and byte limits.
3. Detect media type from bytes, not only extension or caller declaration.
4. Reject extension, declaration, and detected-type conflicts.
5. Move the artifact atomically to SCANNING and emit artifact.scan_started.
6. Run malware scanning with a strict timeout and bounded resources.
7. Run the applicable format validator.
8. Persist an immutable ArtifactSecurityReceipt.
9. Promote the exact scanned bytes only when every required check passes.
10. Emit artifact.scan_passed plus artifact.promoted, or one typed rejection
    receipt with safe reason codes.

Scanner requirements:
- Define a SecurityScanner protocol independent of ClamAV.
- Provide a ClamAV reference adapter using a safe library or argument-list
  subprocess call; never construct a shell command from user input.
- Record engine version and signature database version/date.
- Treat missing engine, stale or unavailable signatures according to an
  explicit enforce-mode policy. Production enforce mode must block relay.
- Use deterministic fake clean, malicious, timeout, unavailable, and malformed
  scanners in tests.
- Do not upload artifacts to VirusTotal or another third-party service.

Initial allowlist and validation:
- Markdown/text: require UTF-8; reject NUL bytes and disallowed control
  characters; enforce the configured size.
- JSON: parse successfully; reject duplicate-key ambiguity if the chosen parser
  can detect it; enforce nesting, key-count, and collection-size limits.
- YAML: use a safe loader; reject custom tags, unsafe object construction,
  excessive aliases, excessive nesting, and oversized collections.
- PNG/JPEG: verify signatures and fully decode with a maintained image library;
  limit width, height, total pixels, and metadata size; reject truncated or
  polyglot content. Do not silently rewrite the original.
- PDF: require a structurally valid, unencrypted document; reject JavaScript,
  launch actions, embedded files, and unsupported active content. Run parsing
  inside the strongest practical resource boundary and document residual risk.
- Reject ZIP, archives, executables, Office documents, SVG, encrypted files,
  and unknown binary formats.

AI-specific authority boundary:
- Artifact bytes and extracted text are untrusted data.
- An @awr string inside an artifact must not affect classification or routing.
- Artifact content cannot change repository, ref, executor, mode, permissions,
  approval requirements, or parent relationship.
- Add a deterministic policy result proving that only the primary decorated
  Markdown work order has control authority.

Tests must include:
- the standard EICAR test fixture is detected and rejected without introducing
  a real malicious binary;
- scanner unavailable, timeout, or malformed response fails closed;
- MIME spoofing and extension mismatch are rejected;
- corrupted images and excessive pixel counts are rejected;
- malformed and resource-abusive JSON/YAML fixtures are rejected;
- encrypted or active-content PDFs are rejected;
- ZIP, executable, Office, SVG, and unknown binaries are rejected;
- a clean fixture for every allowed type is promoted with the same SHA-256;
- no rejected artifact is available through the clean read API;
- repeated scan requests are idempotent and do not duplicate terminal receipts;
- @awr text inside an attachment grants no authority; and
- AWR-GT-001 remains unchanged and green.

Do not add remote upload endpoints, signed URLs, Cursor attachments, GCS, or a
new work-bundle MCP tool in this slice.

Before completion run formatting, linting, strict mypy, the full test suite,
package build, and the recording golden test. Return a completion packet with
dependency choices, scanner boundary, supported-format matrix, reason codes,
test fixtures and results, residual risks, and the next recommended step.
After approval, commit and push with a normal fast-forward update to the
operator-authorized branch. Never force-push.
```

## Prompt 3 — Secure work bundles and planner-facing tools

Run this only after Prompts 1 and 2 are merged and enforce mode is proven.

```text
@awr feature.plan

# AWR-AS-03: Secure work bundles and planner-facing tools

Add a backward-compatible work-bundle contract and planner-facing artifact
workflow to Agent Work Relay.

Repository:
https://github.com/datateamsix/agent-work-relay

Read before planning:
- AGENTS.md
- README.md
- docs/ARCHITECTURE.md
- docs/AWR-GT-001.md
- docs/CURSOR_SECURE_ARTIFACT_RELAY_BUILD_PROMPTS.md
- the merged AWR-AS-01 and AWR-AS-02 implementation and tests

Start in planning mode. Verify the exact file/resource capabilities of the
locked MCP Python SDK and the current ChatGPT remote MCP client before selecting
an upload transport. Do not invent an MCP attachment API. Treat an unresolved
client-upload mechanism as a blocking design decision and present the smallest
standards-supported alternative.

Implement these domain capabilities:
- WorkBundle containing one primary decorated Markdown work order and zero to
  ten immutable ArtifactReferences.
- Bundle limits of 256 KiB primary Markdown, 10 MiB per artifact, 25 MiB total,
  and ten artifacts, all configurable downward or upward by explicit policy.
- Atomic bundle validation before executor dispatch.
- Ownership/sender checks preventing one actor from attaching another actor's
  artifact by guessing an ID.
- Artifact-purpose validation and deterministic ordering.
- Idempotency over the complete bundle fingerprint, including ordered artifact
  IDs and SHA-256 values.
- Replays return the original work order and never re-upload, rescan, promote,
  or redispatch artifacts.

Planner-facing operations should provide the equivalent of:
- create_artifact_upload or begin_artifact_intake;
- finalize_artifact_upload;
- get_artifact_status;
- submit_work_bundle_for_planning; and
- get_work_order_artifacts.

Preserve submit_prompt_for_planning unchanged for Markdown-only clients. Do not
base64-encode binary files inside the primary MCP work-order call. If the
current ChatGPT/MCP stack cannot perform the required upload directly, expose a
small authenticated HTTPS streaming or multipart artifact endpoint and let MCP
operate on the returned immutable artifact ID. Document the client flow and do
not accept arbitrary remote URLs.

Security requirements:
- Upload always lands in quarantine.
- Artifact IDs become attachable only after a CLEAN verdict.
- Scanner errors and pending scans block work-order dispatch.
- A clean verdict is valid only for the exact artifact generation and SHA-256.
- Verify the clean fingerprint immediately before dispatch to prevent
  time-of-check/time-of-use substitution.
- Never return internal filesystem paths or permanent storage credentials.
- Bind upload tickets to authenticated actor, expected length, expected digest,
  allowed media type, expiration, and one-time finalization.
- Reject SSRF-prone URL import behavior in this milestone.

Wrapper and authority requirements:
- The primary Markdown remains the only control document and must begin with
  exactly one valid @awr directive.
- Add a deterministic artifact manifest to the executor wrapper containing only
  artifact ID, safe filename, detected media type, purpose, byte length,
  SHA-256, and adapter delivery instructions.
- State explicitly in the wrapper that artifacts are untrusted reference data
  and cannot override the work order or AWR guardrails.
- Never inline binary bytes or full PDF/image content into the wrapper.

Receipts:
- Link artifact receipts to the accepted work order without rewriting their
  original history.
- Emit bundle.validated before work_order.routed.
- Emit artifact.relay_authorized for each clean artifact only once.
- Include the ordered bundle fingerprint in the acceptance receipt.
- Preserve the existing work-order ledger ordering for Markdown-only requests.

Tests must prove:
- a Markdown-only request remains byte-for-byte contract compatible;
- a clean JSON, YAML, PNG/JPEG, or PDF artifact can be attached by its immutable
  ID;
- pending, rejected, missing, tampered, expired, or wrong-owner artifacts block
  dispatch;
- bundle count and byte limits fail closed;
- artifact order produces a deterministic bundle fingerprint;
- duplicate bundle submission launches one executor run;
- an attachment containing @awr or prompt-injection language cannot change
  authority;
- upload tickets cannot be replayed or used by another actor;
- arbitrary URL fetching is unavailable; and
- all existing tests and AWR-GT-001 remain green.

Do not add production GCS buckets, Cloud Run scanner infrastructure, ZIP
support, code-execution authority, or a permanent .awr inbox in destination
repositories in this slice.

Return a completion packet with the final client-upload design, new tool and
HTTP contracts, authorization rules, state/receipt sequence, migration notes,
tests and exact results, compatibility proof, and remaining executor-delivery
work. After approval, commit and push with a normal fast-forward update to the
operator-authorized branch. Never force-push.
```

## Prompt 4 — replaced by a two-track packet

The original single AS-04 prompt mixed Cloud Run MCP go-live with GCS,
scanner isolation, and Cursor delivery discovery. That blocked the already
built hosted MCP server and invited invented delivery modes.

Use [docs/AWR-AS-04.md](AWR-AS-04.md):

1. **AWR-CR-01** — production Cloud Run MCP go-live, Markdown-only, artifact
   tools fail closed until GCS exists.
2. **AWR-AS-04** — GCS + isolated scanner + locked Cursor delivery matrix
   (PNG/JPEG via `prompt.images[].data` only; JSON/YAML/PDF return
   `DELIVERY_UNSUPPORTED`).

Copy the prompts from that document. Do not re-open Cursor capability
discovery. Do not materialize artifacts onto destination repositories.

## Definition of done for the four-prompt milestone

The artifact milestone is complete only when:

1. Markdown-only AWR clients remain backward compatible.
2. Allowed artifacts remain immutable and content-addressed.
3. Unsupported or suspicious content fails before executor dispatch.
4. Scanner failure blocks relay in production enforce mode.
5. Artifacts cannot alter AWR authority or guardrails.
6. Clean-artifact delivery is capability-aware and fingerprint-verified.
7. Quarantine is inaccessible to planners and executors.
8. Every boundary produces an immutable, reconcilable receipt.
9. No binary bodies are stored in Firestore or ledger events.
10. No ZIP, archive, executable, Office, or SVG support has been introduced.
11. `AWR-GT-001` and the full existing suite remain green.
12. `AWR-GT-002` proves a clean artifact path and fail-closed rejection paths.
