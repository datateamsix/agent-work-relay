# Template customization

Templates are defaults, not product policy. Teams may rename body headings
or drop optional prose while preserving:

- the leading `@input` or `@response` decorator;
- the `awr` schema, lifecycle type, lineage, and idempotency fields;
- `authority: report_only` on every response;
- repository and base binding for code work;
- source fingerprints in responses;
- approval references for execution;
- the rule that content cannot grant authority.

Version customized templates. Give them a new manifest ID or increment
`template_version`, then refresh fingerprints with
`scripts/refresh_template_manifest.py`.

Each template distinguishes:

- **required protocol fields** in the `awr` mapping;
- **required lifecycle bindings** such as parent, plan, or run IDs;
- **recommended narrative sections** in the body;
- **optional provider information** normalized by the adapter.

Do not add credentials, environment URLs, or user-specific repositories to
templates or the manifest.
