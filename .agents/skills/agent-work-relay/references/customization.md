# Template customization

Templates are defaults, not product-policy replacements. Teams may rename body
headings, add domain sections, or remove optional prose while preserving:

- the leading `@input` or `@response` decorator;
- the `awr` schema, lifecycle type, lineage, and idempotency fields;
- repository and base binding for code work;
- source fingerprints in responses;
- approval references for execution;
- objective acceptance and verification evidence;
- the rule that content cannot grant authority.

Version customized templates. Record the template ID, version, and fingerprint
in the broker receipt so a reviewer can reconstruct the exact wrapper and body
shape used for a handoff.

Avoid making every field mandatory. A template should distinguish:

- **required protocol fields**, validated by the broker;
- **required lifecycle evidence**, validated for that message type;
- **recommended narrative sections**, adaptable by the author;
- **optional provider fields**, normalized by the adapter.
