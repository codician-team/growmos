# Contributing to growmos

Thanks for helping the organism grow. growmos is MIT-licensed and maintained by
[Codician](https://codician.com).

## Ground rules

- **Zero runtime dependencies.** The core must stay stdlib-only so it drops into any repo.
  Optional integrations (providers, exporters) must degrade gracefully.
- **Deterministic files.** Store writes must be sorted and reproducible — the graph lives in git.
- **Keep the model for judgment.** If a change can be done in code (blocking, validation,
  traversal, scoring), do it in code. Prompts change through the eval loop, not on a hunch.
- **Every edge keeps provenance.** No feature may create an edge without a source.
- **Agent-agnostic.** New integrations go in `integrate.py` (marker blocks, idempotent JSON
  merges) or the MCP server; the protocol block in `templates/integrations/agents-block.md` is
  the single source of truth for what agents are told.

## Dev loop

```bash
git clone https://github.com/codician/growmos && cd growmos
pip install -e .              # or: uv tool install -e .
python -m unittest discover -s tests -v
./examples/apollo/run_demo.sh /tmp/apollo   # rebuild the playbook corpus end-to-end
```

This repo dogfoods itself: `.growmos/` here is growmos's own knowledge graph. When you change
architecture, run `growmos remember` / `growmos link` / `growmos journal`, and `growmos scan`
after editing docs.

## Ideas welcome (roadmap)

- Temporal filtering of subgraphs (`--at 2026-Q3`) using `when` ranges
- Embedding-based blocking for very large entity sets (optional extra)
- Graph-of-graphs: import/export between repositories, meta-graph of cross-repo edges
- Web viewer for `growmos export --format json`
- Provider adapters: Vertex/Bedrock/Foundry via the same structured-output shape

Open an issue before large changes so we can agree on the shape.
