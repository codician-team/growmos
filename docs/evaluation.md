# Evaluation: the loop that turns a demo into a production system

> Change the extraction prompt → rerun the scorer → watch F1 move. A team that ships without
> this loop cannot tell whether a prompt change improved or degraded quality.

## 1. Build a gold set

Pick at least two representative documents. Pre-fill from the current extraction, then
**hand-correct** (remove wrong entities, add missed ones — the whole point is human judgment):

```bash
growmos gold-template docs/apollo-11.md
$EDITOR .growmos/eval/gold/apollo-11.json
```

Gold format:
```json
{"source": "docs/apollo-11.md",
 "entities": [{"name": "Apollo 11", "type": "EVENT"}, {"name": "Neil Armstrong", "type": "PERSON"}],
 "relations": [{"source": "Neil Armstrong", "target": "Apollo 11"}]}
```
Relations are scored on (source, target) pairs, direction-agnostic, ignoring predicate wording —
an upper bound on relation recall that catches structural errors (missing/wrong connections),
which matter more than wording.

## 2. Keep the scorer alias map current

If the resolver picks a canonical form the gold set doesn't use ("Neil Alden Armstrong" vs
"Neil Armstrong"), resolved recall drops — a scoring artifact, not a resolver bug.
`growmos eval` lists such canonicals; add them to `.growmos/eval/aliases.json`:
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

## 6. The human sample

`growmos sample` prints a random (degree-weighted) node with its profile, edges, provenance and
sources. Read it against the sources. `growmos doctor` turns red if nobody has done this in 7 days.
