# `.growmos/` file format

All files are UTF-8, deterministic (sorted keys, sorted rows) and safe to commit. JSONL rows are
one JSON object per line.

## sources.jsonl
```json
{"id":"src_b33563055168","ref":"README.md","kind":"file","sha256":"…","title":"README.md",
 "status":"extracted","added":"2026-08-17T10:00:00Z","updated":"…","extracted_at":"…",
 "schema_version":1,"stats":{"entities":8,"relations":6}}
```
`kind` ∈ `file` (repo-relative path) · `url` · `text` (stdin/foreign file; body cached under `cache/`) · `note` (provenance label such as `session:2026-08-17`).
`status` ∈ `pending` · `extracted` · `note` · `missing`.

## mentions.jsonl (append-only)
```json
{"source":"src_…","chunk":0,"name":"A. Chen","type":"PERSON","description":"Approved ADR-001.","ts":"…","schema_version":1}
```

## entities.jsonl
```json
{"id":"person/alice-chen","name":"Alice Chen","type":"PERSON","description":"Owner of the Scheduler component.",
 "sources":["src_a","src_b"],"mentions":2,"provisional":false,"created":"…","updated":"…","schema_version":1}
```
Ids are stable (`<type>/<slug-of-first-seen-name>`); `name` is the canonical display form and may change on resolution.

## aliases.jsonl
```json
{"alias":"A. Chen","type":"PERSON","entity":"person/alice-chen"}
```

## relations.jsonl
```json
{"id":"r_4e1920f801f6","source":"dependency/postgres","predicate":"replaces","target":"dependency/redis",
 "sources":["src_a","src_b"],"confidence":2,"created":"…","updated":"…","schema_version":1,
 "when":{"start":"2026-08","end":"ongoing"}}
```
`id` = hash of (source, normalized predicate, target). `confidence` = number of distinct sources (cross-document corroboration). `when` is optional.

## profiles/<id>.json
```json
{"entity":"component/scheduler","summary":"…","key_facts":["…"],"time_range":{"start":"unknown","end":"ongoing"},
 "sources_hash":"ab12cd34ef56","ts":"…"}
```
`sources_hash` is the hash of the entity's source set at write time; a mismatch marks the profile stale.

## schema.json
```json
{"version":1,"entity_types":["PERSON","ORGANIZATION","LOCATION","EVENT","ARTIFACT","COMPONENT",…],
 "predicate_hints":["depends on","calls",…],"history":[{"version":1,"ts":"…","note":"init with preset 'software'"}]}
```

## config.json
```json
{"preset":"software","include":["README.md","docs/**/*.md",…],"exclude":["node_modules/**",…],
 "max_docs_per_run":50,"max_entities_per_doc":40,"chunk_chars":6000,"profile_min_degree":3,
 "resolve_batch_size":80,"gold_min":2,"review_days":7,"provider":{"name":"","extract_model":"","reason_model":""}}
```

## state.json
Runs (last 200), `pending_resummarize`, `open_packets` (names handed out in the last resolution packet per type), `last_sample`, `last_eval`, counters.

## eval/
`gold/<doc>.json` — `{"source": "<ref>", "entities": [{"name","type"}], "relations": [{"source","target"}]}` ·
`aliases.json` — `{"<canonical form>": "<gold name>"}` · `last_report.json`.

## Packet payloads (what agents return)

Extraction: `{"entities":[{"name","type","description"}],"relations":[{"source","predicate","target"}]}`
Resolution: `{"clusters":[{"canonical","aliases":[…]}]}` (optionally `"input_names":[…]`)
Profile: `{"summary","key_facts":[…],"time_range":{"start","end"}}`

The strict JSON schemas (for structured-output APIs) are in `growmos.schema`.
