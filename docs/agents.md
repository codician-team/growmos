# Using growmos with your agent CLI

growmos is **agent-native**: your CLI agent is the model. The CLI does the deterministic
work and hands judgment work over as *task packets*. No API key is required.

## The protocol every agent follows

1. **Session start** → `growmos context` (Claude Code does this automatically via a `SessionStart` hook).
2. **Cross-cutting question** → `growmos query "<question>"` and answer only from the triples, citing edge ids.
3. **Learned/decided something durable** → `growmos remember` / `growmos link` / `growmos journal`.
4. **Grow the graph** → `growmos next` → produce the JSON → run the printed `growmos apply …` → repeat.
5. **Before asserting facts** → `growmos check "<claims>"`.
6. **Session end** → `growmos journal "<summary>"`.

The block that teaches this to agents is written by `growmos integrate <target>` (or `growmos init --agent …`).

## Claude Code

```bash
growmos init --agent claude     # or: growmos integrate claude
```
Writes:
- `CLAUDE.md` — protocol block (marker-delimited, idempotent)
- `.claude/skills/growmos/SKILL.md` — a skill that triggers on graph-related asks and explains the packet rules
- `.claude/settings.json` — hooks: `SessionStart` runs `growmos context --brief` (injected into context), `Stop` runs `growmos scan --quiet` (queues changed docs)
- `.mcp.json` — registers `growmos mcp` as an MCP server (tools: `growmos_query`, `growmos_remember`, …)

Try: *"grow the knowledge graph until it's up to date"* · *"what does the graph say about the Store?"* · *"remember that the Scheduler now depends on Kafka"*.

## Codex CLI

```bash
growmos init --agent codex      # writes the AGENTS.md block
```
Codex reads `AGENTS.md`. To use MCP tools instead of shelling out, add to `~/.codex/config.toml`:
```toml
[mcp_servers.growmos]
command = "growmos"
args = ["mcp"]
```

## Grok CLI and other CLIs

```bash
growmos init --agent grok       # AGENTS.md block + .mcp.json
```
Most CLIs honour `AGENTS.md`. If yours uses a different instructions file, append the block:
```bash
growmos integrate file --file .grok/GROK.md
```
If it supports MCP, point it at `growmos mcp` (stdio).

## Cursor

```bash
growmos init --agent cursor     # .cursor/rules/growmos.mdc, alwaysApply: true
```

## Gemini CLI

```bash
growmos init --agent gemini     # GEMINI.md block
```

## Everything at once

```bash
growmos init --agent all        # claude + codex + gemini + cursor + git hooks + CI workflow
```

## Git hooks & CI

- `growmos integrate hooks` → `post-commit`, `post-merge`, `post-checkout` run `growmos scan --quiet` so edited docs are queued for the next session (safe no-op if growmos is absent; respects `core.hooksPath`).
- `growmos integrate ci` → `.github/workflows/growmos.yml` runs `status`, `doctor`, `eval` on PRs.

## Multi-agent teams (orchestrator–workers)

The graph is the blackboard. Give each worker its slice of sources (`growmos add …` per worker,
or separate include globs), let workers run `next`/`apply` in their own context windows, and let
the resolver step (`growmos resolve` → apply) merge surface forms across workers ("Acme Corp",
"ACME Corporation", "acme"). The synthesizer never re-reads the raw documents: it runs
`growmos query` and cites edges. Everything writes to the same JSONL files; commit and merge
like code.

## Packet anatomy

```
=== growmos task packet: extraction · docs/adr-001.md · chunk 1/1 ===
Respond with JSON of this shape (strict: no extra keys):
{"entities": [...], "relations": [...]}
Write it to `.growmos/cache/extract_src_….json` (or pipe it on stdin), then run:
    growmos apply extraction .growmos/cache/extract_src_….json --source src_… --chunk 0
--- prompt ---
<the prompt from .growmos/prompts/extract.md, rendered>
```
`growmos next --json` gives the same as structured data (`text`, `meta.apply`, `meta.out_file`).
