# Evaluation: the loop that turns a demo into a production system

> Change the extraction prompt → rerun the scorer → watch F1 move. A team that ships without
> this loop cannot tell whether a prompt change improved or degraded quality.

## 1. Build a gold set (automatic by default)

`growmos next` hands out a **gold packet** for the richest extracted documents until `gold_min`
(default 2) files exist: the agent reads the document and writes the reference answer, and the file
records `_reviewed_by: agent`. Nothing manual is required. If you want a human pass, pre-fill and
edit — the file then records your name:

```bash
growmos gold-template docs/apollo-11.md      # pre-fill from current extraction
$EDITOR .growmos/eval/gold/apollo-11.json
# or: growmos apply gold my.json --source docs/apollo-11.md --reviewer human
```
Caveat worth knowing: an agent-written gold set shares the blind spots of the agent that wrote it;
it still catches regressions (prompt drift, resolver over-merging) reliably, which is what the loop
is for.

Gold format:
```json
{"source": "docs/apollo-11.md",
 "entities": [{"name": "Apollo 11", "type": "EVENT"}, {"name": "Neil Armstrong", "type": "PERSON"}],
 "relations": [{"source": "Neil Armstrong", "target": "Apollo 11"}]}
```
Relations are scored on (source, target) pairs, direction-agnostic, ignoring predicate wording —
an upper bound on relation recall that catches structural errors (missing/wrong connections),
which matter more than wording.

## 2. Scorer alias map (automatic)

If the resolver picks a canonical form the gold set doesn't use ("Neil Alden Armstrong" vs
"Neil Armstrong"), resolved recall would drop — a scoring artifact, not a resolver bug.
`growmos eval` detects these (the raw mention matched a gold name) and **extends
`.growmos/eval/aliases.json` itself**, then rescores. You can also add entries by hand:
```json
{"Neil Alden Armstrong": "Neil Armstrong", "John F. Kennedy Space Center": "Kennedy Space Center"}
```

## 3. Run it

```bash
growmos eval
```
```
document                 raw F1     P     R | resolved R | rel F1
docs/apollo-11.md          0.75   1.0   0.6 |        0.6 |  0.833
    missed: columbia, eagle, president kennedy, saturn v
```
Precision 1.0 with recall < 1 is the *intended* posture: everything extracted was correct; the
misses are peripheral mentions ("Purdue University") or scope mismatches ("Saturn V" in the
Apollo 11 gold but extracted from the Saturn V article). Loosening "extract only central
entities" trades noise for recall — make that trade deliberately, and record it in the journal.

## 4. Tune

Edit `.growmos/prompts/extract.md` (or `resolve.md`, `summarize.md`), re-extract the gold
documents (`growmos scan` after touching them, or `growmos extract <ref>` → apply), rerun
`growmos eval`. Bump the schema (`growmos schema bump --note "loosened centrality rule"`) when
the change alters what the graph means, so rows produced under different prompts can be told apart.

## 5. Health signals worth graphing over time

`growmos status --json` exposes: `nodes`, `edges`, `density`, `components`, `compression_ratio`,
`pending_sources`, `provisional`, `stale_profiles`. Extraction rate per document is in each
source's `stats`. Wire them into CI (`growmos integrate ci`).

## 6. The periodic review (automatic)

Every `review_days` (default 7) `growmos next` hands out a **review packet**: one random
(degree-weighted) node with its edges, provenance and source excerpts; the agent verifies each edge,
fixes what's wrong, and reports ok/issues (recorded in the journal). `growmos sample` remains
available for a human pass; `growmos doctor` shows who reviewed last and when.
