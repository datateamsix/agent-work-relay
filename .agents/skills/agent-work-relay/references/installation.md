# Installing the shared skill and MCP connection

The canonical portable skill is the complete `agent-work-relay/` directory.
Install the same directory in each agent environment; do not maintain divergent
planner and worker copies.

## Cursor and Cursor Cloud

Keep the skill in the destination repository at:

```text
.agents/skills/agent-work-relay/
```

Project-level storage is important for remote or cloud agents that cannot see a
developer machine's global skill directory. Connect the remote AWR Streamable
HTTP endpoint through Cursor's project or managed MCP configuration and complete
OAuth. Do not commit bearer tokens.

## ChatGPT and Codex

Install the skill bundle in the workspace or project skill catalog and connect
the authenticated AWR remote MCP server separately. The skill declares the MCP
dependency but does not contain credentials or create the connection.

## Claude Code

Install the canonical directory using Claude Code's supported project skill
location. If the installed version does not discover Agent Skills, reference the
canonical `SKILL.md` from the project's instruction file. Configure the same AWR
MCP endpoint through Claude Code's MCP configuration and OAuth flow.

## Gemini CLI

Use project `GEMINI.md` instructions to import or direct the agent to the
canonical skill, then configure AWR as a remote MCP server in project settings.
Keep the canonical skill as the single source of truth.

## Connection verification

Before a live relay, verify that the client can:

1. discover AWR tools;
2. complete OAuth without exposing tokens;
3. call a read-only tool;
4. see the expected protocol version and scopes;
5. submit an idempotent test message;
6. retrieve its receipt and timeline;
7. reject an unauthorized decision or execution attempt.
