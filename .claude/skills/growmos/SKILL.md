---
name: growmos
description: Use the repository's living knowledge graph (growmos, in .growmos/) as shared memory. Trigger when the user asks about how parts of the codebase relate, why a decision was made, who owns what, what depends on what; when you finish a meaningful piece of development and should record it; when `growmos next` reports pending work; or when the user says "update the knowledge graph", "remember this", "what does the graph say".
---

# growmos — the repository's living knowledge graph

`.growmos/` holds a knowledge graph built and maintained by agents and humans together:
canonical **entities** (typed nodes), **relations** (short-verb-phrase edges with provenance
and corroboration counts), **profiles** for hub nodes, a **journal**, and an **evaluation
harness**. It is the shared world model that survives context windows — read it first,
write to it as you develop.

## The loop you run

| When | Command | What you do |
|---|---|---|
| Session start | `growmos context` | Read the brief (hubs, health, pending, journal tail). |
| Cross-cutting question | `growmos query "…" [--seed X] [--hops 2]` | Answer **only** from the returned triples; cite edge ids like `[r_ab12…]`; say what the graph lacks. |
| Something durable learned | `growmos remember`, `growmos link`, `growmos journal` | Write it back with a grounded one-sentence description. |
| Graph needs feeding | `growmos next` | You get a *task packet*: prompt + JSON schema + apply command. Produce the JSON, run the apply command. Repeat. |
| About to assert facts | `growmos check "<text>"` | Verify claims against edges with provenance; escalate absent claims to the human. |
| Session end | `growmos journal "…"` | Leave the next session a note. |

## Rules for the judgment stages (from the Anthropic knowledge-graph playbook)

**Extraction packet** — extract only entities *central* to the document; write a one-sentence
description grounded in *this* document (it is the disambiguation signal for resolution);
predicates are short verb phrases ("commanded", "depends on", "part of"); every relation must
connect two entities you extracted; never invent facts. Precision beats recall: a wrong
entity spawns wrong edges that mislead multi-hop reasoning.

**Resolution packet** — cluster surface forms of the same real-world entity. Every input name
appears in exactly one cluster; genuinely distinct entities get single-element clusters; use
the descriptions, not just string similarity ("Edwin Aldrin" = "Buzz Aldrin"; "Gemini 12" ≠
"Project Gemini"); canonical = most complete unambiguous form.

**Profile packet** — 2–3 paragraph synthesis across sources, prefer the most specific claim
when sources disagree, 3–5 atomic key facts each traceable to a source, YYYY/YYYY-MM time range,
no invented facts.

**Query** — answer from the graph only, cite edges, flag gaps. On a private corpus only the
grounded answer works at all.

## Applying results

Write the JSON to the `out_file` named in the packet header (or pipe on stdin) and run the
printed command, e.g.

```bash
growmos apply extraction .growmos/cache/extract_src_xxx_0.json --source src_xxx --chunk 0
growmos apply resolution .growmos/cache/resolve_person_0.json --type PERSON
growmos apply profile .growmos/cache/profile_x.json --entity "component/graph-store"
```

The CLI validates against the schema, drops dangling relations, gives unmatched names a
single-element fallback cluster, records provenance, and updates health counters.

## Do / don't

- Do commit `.growmos/` with the code (it is plain JSONL, merge-friendly).
- Do keep `growmos doctor` green; do run `growmos sample` occasionally and read one node.
- Don't hand-edit `entities.jsonl` / `relations.jsonl` — use the CLI so provenance stays intact.
- Don't put secrets in the graph or the journal.
