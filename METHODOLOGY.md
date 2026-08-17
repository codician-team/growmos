# The Living Knowledge Graph — a methodology

*An abstract, tool-agnostic method for giving a repository (or any body of work) a knowledge
graph that behaves like a living organism: it eats new material, grows, resolves itself,
answers with citations, measures its own health, and never forgets. `growmos` is one
implementation; the method stands on its own and can be followed with a text editor.*

The method synthesizes the Anthropic knowledge-graph playbook (extraction → resolution →
assembly → querying, with an evaluation feedback loop) and Anthropic's composable agent
patterns (augmented LLM, prompt chaining, routing, orchestrator–workers, evaluator–optimizer),
and adds what a *living* graph in a *developing* repository needs: write paths for agents,
incremental growth, provenance from sessions, and operational discipline.

---

## 0. Principles

1. **The schema is the training data.** No trained NER, no relation classifier, no
   hand-tuned resolution heuristics. Every judgment stage is a prompt whose output must validate
   against a small typed schema. Adapting to a new domain = extending the type vocabulary and
   tuning a prompt.
2. **Keep the model for judgment; use deterministic code for everything else.** Storage,
   hashing, blocking, assembly, traversal, serialization, diagnostics and scoring are code.
   Extraction, resolution, summarization and answering are judgment.
3. **The graph is the session.** Durable, append-only, interrogable by slice. The agent forgets;
   the graph does not.
4. **Precision over recall.** A missing entity yields an incomplete but correct graph. A wrong
   entity spawns wrong edges that propagate through multi-hop reasoning.
5. **Nothing is silently lost, nothing is silently trusted.** Unmatched names become
   single-element clusters. Every edge carries provenance and a corroboration count. Claims that
   the graph cannot support are escalated, not guessed.
6. **The pipeline is not done when it runs; it is done when you can tell each morning whether
   what it produced overnight was right.** Gold set, provenance, and a human sample are judgment;
   everything else is infrastructure.

---

## 1. The organism's anatomy

| Organ | Purpose | growmos file |
|---|---|---|
| **Sources** | everything eaten: docs, ADRs, URLs, sessions; content-hashed, with status | `sources.jsonl` |
| **Mentions** | raw per-document extraction, never rewritten (provenance) | `mentions.jsonl` |
| **Entities** | canonical nodes: name, type, one-line description, sources, mention count, *provisional* flag | `entities.jsonl` |
| **Alias map** | every surface form → canonical node | `aliases.jsonl` |
| **Relations** | directed typed edges; `sources[]`; `confidence` = number of corroborating documents; optional `when` | `relations.jsonl` |
| **Profiles** | 2–3 paragraph synthesis + atomic key facts + time range for hub nodes, keyed to the node's source-set hash | `profiles/` |
| **Schema** | versioned entity types + predicate vocabulary; every row carries the version it was produced under | `schema.json` |
| **State** | the loop's state file: runs, pending re-summarization, last sample, last eval | `state.json` |
| **Journal** | the shared memo (humans + agents), append-only | `journal.md` |
| **Prompts** | the judgment prompts, as editable files | `prompts/` |
| **Gold + alias map** | hand-labelled evaluation set + scorer aliases | `eval/` |

Everything is plain text in the repository. Storage is an infrastructure decision (JSONL today,
Neo4j or three Postgres tables tomorrow); the pipeline does not change.

---

## 2. Metabolism: the four stages, plus the write path

### 2.1 Ingestion (deterministic)
Register sources by content hash. New or changed → *pending*. Long documents are chunked at
section boundaries with one paragraph of overlap so entities stay near their relations.
Cap the number of documents per run so an ingestion mistake cannot produce unbounded cost.

### 2.2 Extraction (judgment; fast/cheap model)
Prompt per chunk. Four guidelines, each preventing a named failure mode:
- extract only **central** entities (controls recall/noise);
- one-sentence description **grounded in this document** (the disambiguation signal for resolution);
- predicates are **short verb phrases** (traversable, not vague);
- every relation connects **two extracted entities** (no dangling edges — the validator drops any that don't).

Record raw mentions (provenance), then resolve each name **against the existing canonical set
by exact match** (not against each other). Unmatched → new **provisional** node with a
single-element cluster. Add edges; a repeated edge from a new document raises `confidence`.

### 2.3 Resolution (judgment; reasoning model)
Only when there is something provisional. Block cheaply first (shared name tokens; small
graphs are one block), then ask the model to cluster the block **per entity type**, using the
descriptions. Constraints enforced by the validator: every input name in exactly one cluster;
distinct entities keep single-element clusters; canonical = most complete unambiguous form.
Applying a cluster = merge nodes (aliases follow, edges are rewritten and deduped, sources and
mention counts union, stale profile invalidated).

Two failure modes to watch: silent loss (prevented structurally) and over-merging (a
specific thing folded into a broader one — the prompt warns against it, the human sample
catches it).

### 2.4 Assembly & profiles (deterministic + judgment)
The graph is a multi-digraph: two nodes may share several predicates and direction matters.
Diagnostics after every change: **components** (one is the goal), **degree distribution**
(hubs), **edges/nodes density** (~1–2 is healthy), **compression ratio** (surface forms /
canonical nodes; ~1 means resolution is doing little, >2 means it is earning its cost).
Summarize only hub nodes (degree ≥ 3 by default), only when their source set changed.

### 2.5 Querying (judgment; reasoning model)
Seed entities from the question (alias containment, then token overlap), take the k-hop
neighbourhood (k=2 is the sweet spot), serialize as triples **with edge ids and provenance**,
and answer *only* from that context, citing edge ids and naming what the graph does not
contain. Return the subgraph alongside the answer so citations can be verified by string match.

### 2.6 The write path (agents developing)
This is what makes the graph *living* rather than *batch-built*: while working, an agent
records durable knowledge directly —
`remember <node>` (with a grounded description), `link <A> <predicate> <B>` (with optional
time range), `journal <note>` — each with provenance `session:<date>` (or a file path). Those
nodes enter as provisional and flow through the same resolution loop, so the memo and the
extracted graph converge.

---

## 3. Where the graph sits in agent architectures

| Pattern | Role of the graph | In growmos |
|---|---|---|
| Augmented LLM | retrieval source for multi-hop questions | `growmos query`, MCP `growmos_query` |
| Prompt chaining | gate: check new entities against existing nodes between steps | `growmos check`, `growmos search` |
| Routing | entity type & degree route to a specialist without an LLM call | `growmos show --json`, `status --json` |
| Orchestrator–workers | **shared memory**: workers read/write the graph, orchestrator's window stays clean | workers run `next`/`apply`/`remember`; the graph is the blackboard |
| Evaluator–optimizer | **grounding layer**: evaluator checks claims against edges with provenance | `growmos check` (deterministic verdicts + evidence), `check` packet |
| Overnight loop | **persistent world model**: survives context flushes | `state.json`, `growmos ingest`, git hooks |
| Hierarchical / federated | segment by domain (subgraphs), meta-graph across teams | one `.growmos/` per repo; export/import to federate |

---

## 4. Homeostasis: measurement and discipline

**Evaluation loop.** A gold set of at least two representative documents; a scorer alias map
so canonical forms the gold set doesn't recognize aren't counted as misses (a scoring artifact,
not a resolver bug). Score raw entities, resolved entities, and relations on (source, target)
pairs. *Change the prompt → rerun the scorer → watch F1 move.* A pipeline without this loop
drifts.

**Four monitoring signals.** Extraction rate per document (a sudden drop = domain shift, a
spike = over-extraction); resolution compression ratio; connectivity; query latency/cost.

**Ten-item readiness checklist** (each maps to a nameable failure): gold set · alias map ·
schema version · extraction cap · resolution fallback · provenance · incremental update ·
connectivity monitor · summarization trigger · human sample. `growmos doctor` prints it.

**Standing practices.** Sample a random node regularly and read it against its sources
(comprehension rot is the moment you can't explain an edge). Cap volume before you ship.
Version the schema alongside the graph and record the version on every row.

---

## 5. Growth rules ("expanding like a universe")

- **Accretion, not rebuild.** New material adds nodes and edges; existing structure is never
  regenerated wholesale. Re-extraction happens only for changed sources.
- **Constellations.** Hubs emerge from degree; profiles are written for hubs only. A flat
  degree distribution is a smell (either a homogeneous corpus or a prompt that treats every
  mention as central).
- **Epochs.** A schema version is an epoch. Rows carry their epoch, so entities extracted under
  different prompts can be compared or re-extracted.
- **Corroboration is gravity.** An edge seen in three independent documents is heavier than one
  seen once; serialization orders by it, evaluators may weight by it.
- **Time.** Relations may carry a `when` range; profiles carry a time range. Temporal
  filtering before reasoning is a natural extension.
- **Federation.** Every repository has its own graph; exports (`json`, `cypher`, `sql`) let a
  meta-graph connect domain graphs without merging schemas.

---

## 6. Limitations (stated plainly)

Extraction quality depends on prompt engineering — the loop must be run. Blocking heuristics
are domain-dependent even though the clustering prompt is universal. The graph is only as good
as the corpus: a biased or incomplete corpus produces a biased or incomplete graph. And the
graph is a fact store, not a decision-maker: what to query, whether a missing edge is an error
or a gap, what to do next — those remain with the agents and, ultimately, the humans who
designed them.

---

## 7. Attribution

The pipeline stages, the four prompts, the evaluation harness, Table II (agent patterns ×
graph roles), the scaling guidance and the readiness checklist are from the independently
compiled *Knowledge Graph Engineering for Multi-Agentic Systems: The Anthropic Playbook*
(2026), itself a synthesis of Anthropic's public knowledge-graph cookbook and *Building
Effective Agents*. growmos is developed by Codician (codician.com), MIT-licensed, and is not
affiliated with or endorsed by Anthropic.
