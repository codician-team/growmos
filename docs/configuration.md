# Configuration

All settings live in `.growmos/config.json`. Read/write them with `growmos config`:

```bash
growmos config                        # show all
growmos config max_docs_per_run       # show one
growmos config max_docs_per_run 500   # set (numbers, true/false, comma-lists are parsed)
```

| Key | Default | What it does |
|---|---|---|
| `preset` | `software` | entity-type vocabulary chosen at init (`software`, `general`, `research`, `business`) |
| `include` | preset globs (`README.md`, `docs/**/*.md`, `*.md`, ADR/RFC dirs…) | which files are *sources*. Docs only by default — never source code |
| `exclude` | build dirs, lockfiles, agent instruction files (`AGENTS.md`, `CLAUDE.md`, `.claude/**`…) | never scanned |
| `max_docs_per_run` | `50` | extraction packets per **calendar day**. A speed bump against runaway unattended runs, not a wall — see below. `0` = unlimited |
| `max_entities_per_doc` | `40` | hard cap per extraction; extra entities (and their edges) are dropped and reported |
| `chunk_chars` | `6000` | long docs are split at section boundaries with one-paragraph overlap; ~1.5k tokens per packet |
| `profile_min_degree` | `3` | only hubs with degree ≥ this get profiles (and only when their source set changes) |
| `resolve_batch_size` | `80` | max names per resolution packet; larger types are blocked by shared name tokens first |
| `gold_min` | `2` | how many gold files `growmos next` will produce for the eval loop |
| `review_days` | `7` | how often `growmos next` hands out a node-review packet |
| `provider` | empty | headless mode: `name`, `base_url`, `extract_model`, `reason_model` (keys via env vars, never here) |

## Big projects

A large repo (hundreds of docs) is fine; three knobs matter:

1. **Backfill speed.** `max_docs_per_run` counts extractions per day (default 50). When an agent or human is driving, just push through it:
   ```bash
   growmos next --force                    # skip the cap for this packet
   growmos config max_docs_per_run 0       # or lift it for good
   ```
   The cap only exists to bound *unattended* runs (`growmos ingest` on cron/CI with an API key). Agents are told the same in `AGENTS.md`/`CLAUDE.md`, so they will not stall on it.
2. **What counts as knowledge.** Tighten `include` to the docs that carry decisions (ADRs, design docs, READMEs) and let `exclude` drop generated or vendored markdown. Source code is deliberately *not* extracted — the graph is about what the code *means*, which agents write back with `growmos remember`/`link` while they work.
3. **Chunking.** Very long design docs → lower `chunk_chars` (e.g. `4000`) for tighter packets, or raise it if your docs are short and you want fewer round-trips.

Everything else scales sub-linearly by construction: resolution only touches provisional entities, profiles only hubs whose sources changed, queries only a k-hop subgraph, and unchanged files are never re-extracted (content-hashed).

## Multi-repo / monorepo

One `.growmos/` per repository (`growmos init` at the root; `--root` overrides). For a monorepo with independent domains, either one graph with `include` covering all packages, or one `.growmos/` per package directory run with `--root <dir>`; `growmos export` gives you the pieces to federate.
