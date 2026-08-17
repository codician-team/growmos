# Headless mode: cron, CI, overnight loops

Agent-native mode needs no API key. When no agent is at the keyboard — a nightly job, a CI step,
a batch backfill — growmos can call an LLM API itself. It is stdlib-only and provider-neutral.

## Configure

```bash
export ANTHROPIC_API_KEY=sk-ant-…            # provider auto-detected from whichever key is set
# or
export OPENAI_API_KEY=…                       # OpenAI
export XAI_API_KEY=…                          # xAI Grok
# or any OpenAI-compatible server:
export GROWMOS_PROVIDER=openai GROWMOS_BASE_URL=http://localhost:11434/v1 GROWMOS_API_KEY=x
# optional per-stage overrides:
export GROWMOS_EXTRACT_MODEL=… GROWMOS_REASON_MODEL=…
```
Or set `provider.name / base_url / extract_model / reason_model` in `.growmos/config.json`
(never commit an API key there — use env vars).

Model split follows the playbook's Table IV: a fast, cheap model for high-volume extraction
(`claude-haiku-4-5`, `gpt-4o-mini`, `grok-3-mini` by default) and a stronger reasoning model for
resolution, summarization and answering (`claude-sonnet-5`, `gpt-4o`, `grok-3`).
Structured outputs are requested (`output_config.format` json_schema on Anthropic,
`response_format json_schema` on OpenAI-compatible APIs) so payloads validate by construction.

## Run

```bash
growmos ingest --scan --limit 25   # extraction → resolution → hub profiles, respecting caps
growmos query "…" --auto           # grounded answer via the reasoning model
```

## Cron / GitHub Actions

```yaml
- run: pip install growmos
- run: growmos ingest --scan
  env: { ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }} }
- run: growmos doctor && growmos eval
- run: git add .growmos && git commit -m "growmos: overnight growth" || true
```

## Cost notes (from the playbook's scaling guidance)

- Extraction dominates for large corpora — cache the fixed prompt prefix and batch when your
  provider offers it; growmos already caps documents per run.
- Resolution is one call per entity type per block (blocks of ≤ `resolve_batch_size`), not per document.
- Summarization is per hub node and only when its source set changed.
- Querying cost is proportional to subgraph size — tune `--hops` and `--max-triples`.
