"""Prompt templates and *task packets*.

A task packet is the unit of work growmos hands to whatever agent is driving it
(Claude Code, Codex, Grok, Cursor, a cron job calling an API…). It contains:

  1. the exact prompt (from the editable `.growmos/prompts/*.md` templates),
  2. the JSON schema the answer must validate against,
  3. the exact `growmos apply …` command that ingests the answer.

Prompts live as files so the evaluation feedback loop is real: edit the prompt, rerun
`growmos eval`, watch F1 move (§VIII.A).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import schema as S
from .graph import Graph
from .store import Store
from .util import approx_tokens, truncate

TEMPLATE_DIR = Path(__file__).parent / "templates" / "prompts"
PROMPT_NAMES = ["extract", "resolve", "summarize", "query", "check"]


def install_default_prompts(dest: Path, force: bool = False) -> List[str]:
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    for name in PROMPT_NAMES:
        src = TEMPLATE_DIR / f"{name}.md"
        dst = dest / f"{name}.md"
        if force or not dst.exists():
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            written.append(name)
    return written


def load_prompt(store: Store, name: str) -> str:
    path = store.p("prompts", f"{name}.md")
    if path.exists():
        return path.read_text(encoding="utf-8")
    return (TEMPLATE_DIR / f"{name}.md").read_text(encoding="utf-8")


def render(template: str, **kw: Any) -> str:
    """Safe format: only replaces {known} placeholders, leaves other braces alone."""
    def sub(m: re.Match) -> str:
        key = m.group(1)
        return str(kw[key]) if key in kw else m.group(0)
    return re.sub(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", sub, template)


# ---------------------------------------------------------------------------
# Chunking (§IX.E): section boundaries with one-paragraph overlap
# ---------------------------------------------------------------------------

def chunk_text(text: str, max_chars: int = 6000) -> List[str]:
    if len(text) <= max_chars:
        return [text]
    # split at markdown headings first, then paragraphs
    sections = re.split(r"\n(?=#{1,6}\s)", text)
    units: List[str] = []
    for sec in sections:
        if len(sec) <= max_chars:
            units.append(sec)
        else:
            paras = re.split(r"\n\s*\n", sec)
            buf = ""
            for p in paras:
                if len(buf) + len(p) + 2 > max_chars and buf:
                    units.append(buf)
                    buf = p
                else:
                    buf = (buf + "\n\n" + p) if buf else p
            if buf:
                units.append(buf)
    chunks: List[str] = []
    buf = ""
    prev_tail = ""
    for u in units:
        if len(buf) + len(u) + 2 > max_chars and buf:
            chunks.append(buf)
            prev_tail = _last_paragraph(buf)
            buf = (prev_tail + "\n\n" + u) if prev_tail else u
        else:
            buf = (buf + "\n\n" + u) if buf else u
    if buf:
        chunks.append(buf)
    # hard-split any oversize chunk (very long paragraph)
    out: List[str] = []
    for c in chunks:
        while len(c) > max_chars * 1.5:
            out.append(c[:max_chars])
            c = c[max_chars - 300:]
        out.append(c)
    return out


def _last_paragraph(text: str) -> str:
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    return paras[-1] if paras else ""


# ---------------------------------------------------------------------------
# Packets
# ---------------------------------------------------------------------------

def _shape(kind: str, entity_types: Optional[List[str]] = None) -> str:
    """Compact, human/agent-readable answer shape (the full JSON schema is in `--json` output)."""
    if kind == "extraction":
        types = "|".join(entity_types or [])
        return ('{"entities": [{"name": "…", "type": "' + types + '", "description": "one sentence grounded in this document"}],\n'
                ' "relations": [{"source": "<entity name>", "predicate": "short verb phrase", "target": "<entity name>"}]}')
    if kind == "resolution":
        return '{"clusters": [{"canonical": "most complete unambiguous form", "aliases": ["every input name in exactly one cluster"]}]}'
    if kind == "profile":
        return ('{"summary": "2-3 paragraphs", "key_facts": ["3-5 atomic facts traceable to sources"],\n'
                ' "time_range": {"start": "YYYY|unknown", "end": "YYYY|ongoing|unknown"}}')
    return "{}"


def _packet_header(title: str, apply_cmd: str, schema: Dict[str, Any], out_file: str,
                   kind: str = "", entity_types: Optional[List[str]] = None) -> str:
    return (
        f"=== growmos task packet: {title} ===\n"
        f"Respond with JSON of this shape (strict: no extra keys):\n{_shape(kind, entity_types)}\n\n"
        f"Write it to `{out_file}` (or pipe it on stdin), then run:\n\n    {apply_cmd}\n\n"
        f"--- prompt ---\n"
    )


def extraction_packet(store: Store, source_id: str, chunk_index: Optional[int] = None) -> Tuple[str, Dict[str, Any]]:
    rec = store.sources[source_id]
    text = store.source_text(source_id)
    chunks = chunk_text(text, int(store.config.get("chunk_chars", 6000)))
    if chunk_index is None:
        chunk_index = 0
    chunk_index = max(0, min(chunk_index, len(chunks) - 1))
    prompt = render(
        load_prompt(store, "extract"),
        source_ref=rec["ref"], text=chunks[chunk_index],
        entity_types=", ".join(store.entity_types),
        predicate_hints=", ".join(store.schema.get("predicate_hints", [])[:20]),
    )
    schema = S.extraction_schema(store.entity_types)
    out_file = f".growmos/cache/extract_{source_id}_{chunk_index}.json"
    final_flag = "" if chunk_index == len(chunks) - 1 else " --partial"
    apply_cmd = f"growmos apply extraction {out_file} --source {source_id} --chunk {chunk_index}{final_flag}"
    header = _packet_header(f"extraction · {rec['ref']} · chunk {chunk_index + 1}/{len(chunks)}",
                            apply_cmd, schema, out_file, kind="extraction", entity_types=store.entity_types)
    meta = {"source": source_id, "chunk": chunk_index, "chunks": len(chunks), "apply": apply_cmd,
            "out_file": out_file, "tokens": approx_tokens(prompt)}
    return header + prompt, meta


def resolution_blocks(store: Store, etype: str, only_provisional: bool = True) -> List[List[str]]:
    """Cheap deterministic blocking (§IX.B): group candidates by shared name tokens.

    Every block contains at least one provisional entity plus every existing entity of the
    same type sharing a token with it. Small graphs collapse into a single block.
    """
    from .util import tokens_of
    ents = [e for e in store.entities.values() if e["type"] == etype]
    if not ents:
        return []
    prov = [e for e in ents if e.get("provisional")] if only_provisional else ents
    if not prov:
        return []
    batch = int(store.config.get("resolve_batch_size", 80))
    if len(ents) <= batch:
        return [[e["id"] for e in ents]]
    tok_index: Dict[str, List[str]] = {}
    for e in ents:
        for t in tokens_of(e["name"]):
            tok_index.setdefault(t, []).append(e["id"])
    # union-find over provisional entities and their token-neighbours
    parent: Dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    for e in prov:
        find(e["id"])
        for t in tokens_of(e["name"]):
            for other in tok_index.get(t, []):
                union(e["id"], other)
    groups: Dict[str, List[str]] = {}
    for x in list(parent):
        groups.setdefault(find(x), []).append(x)
    blocks = [sorted(g) for g in groups.values() if any(store.entities[i].get("provisional") for i in g)]
    # split oversize blocks
    out: List[List[str]] = []
    for b in blocks:
        for i in range(0, len(b), batch):
            out.append(b[i:i + batch])
    return out


def resolution_packet(store: Store, etype: str, block: List[str], block_index: int = 0) -> Tuple[str, Dict[str, Any]]:
    lines = []
    names = []
    for eid in block:
        e = store.entities[eid]
        names.append(e["name"])
        flag = " (new)" if e.get("provisional") else ""
        lines.append(f"- {e['name']}{flag}: {truncate(e.get('description') or '(no description)', 220)}")
    prompt = render(load_prompt(store, "resolve"), entity_type=etype, entity_list="\n".join(lines))
    out_file = f".growmos/cache/resolve_{etype.lower()}_{block_index}.json"
    apply_cmd = f"growmos apply resolution {out_file} --type {etype}"
    header = _packet_header(f"resolution · {etype} · {len(block)} names", apply_cmd, S.RESOLUTION_SCHEMA, out_file,
                            kind="resolution")
    meta = {"type": etype, "names": names, "apply": apply_cmd, "out_file": out_file, "tokens": approx_tokens(prompt)}
    return header + prompt, meta


def summarize_packet(store: Store, eid: str, max_excerpt_chars: int = 1200) -> Tuple[str, Dict[str, Any]]:
    g = Graph(store)
    e = store.entities[eid]
    excerpts = []
    for sid in e.get("sources", [])[:8]:
        rec = store.sources.get(sid, {})
        try:
            text = store.source_text(sid)
        except Exception:
            text = ""
        snippet = _excerpt_around(text, e["name"], max_excerpt_chars) if text else "(text unavailable)"
        excerpts.append(f"[{rec.get('ref', sid)}]\n{snippet}")
    # also add raw mention descriptions (they are the per-document signal)
    descs = [m["description"] for m in store.mentions()
             if store.resolve_name(m["name"], m["type"]) == eid and m.get("description")]
    if descs:
        excerpts.append("[extraction descriptions]\n" + "\n".join(f"- {d}" for d in sorted(set(descs))[:12]))
    rel_lines = []
    for rid in (g.out.get(eid, []) + g.inc.get(eid, []))[:40]:
        rel_lines.append(g._fmt(store.relations[rid]))
    prompt = render(load_prompt(store, "summarize"), name=e["name"], etype=e["type"],
                    excerpts="\n\n".join(excerpts) or "(none)", relations="\n".join(rel_lines) or "(none)")
    out_file = f".growmos/cache/profile_{eid.replace('/', '__')}.json"
    apply_cmd = f"growmos apply profile {out_file} --entity \"{eid}\""
    header = _packet_header(f"profile · {e['name']}", apply_cmd, S.PROFILE_SCHEMA, out_file, kind="profile")
    meta = {"entity": eid, "apply": apply_cmd, "out_file": out_file, "tokens": approx_tokens(prompt)}
    return header + prompt, meta


def _excerpt_around(text: str, name: str, budget: int) -> str:
    low = text.lower()
    key = name.lower()
    idx = low.find(key)
    if idx < 0:
        # try first token
        toks = key.split()
        idx = low.find(toks[0]) if toks else -1
    if idx < 0:
        return truncate(text, budget)
    start = max(0, idx - budget // 3)
    end = min(len(text), idx + (budget * 2) // 3)
    return ("…" if start > 0 else "") + text[start:end].strip() + ("…" if end < len(text) else "")


def query_packet(store: Store, question: str, seeds: Optional[List[str]] = None, hops: int = 2,
                 max_triples: int = 300) -> Tuple[str, Dict[str, Any]]:
    g = Graph(store)
    seed_ids: List[str] = []
    for s in (seeds or []):
        eid = store.resolve_name(s) or (s if s in store.entities else None)
        if eid:
            seed_ids.append(eid)
    if not seed_ids:
        seed_ids = g.find_seeds(question)
    if not seed_ids:
        # fall back to hubs so the answer is still grounded
        seed_ids = [eid for eid, _ in g.hubs(3)]
    nodes = g.khop(seed_ids, hops=hops)
    triples, total = g.serialize(nodes, max_triples=max_triples)
    node_desc = g.describe_nodes(nodes)
    prompt = render(load_prompt(store, "query"), question=question, graph=triples or "(empty subgraph)",
                    nodes=node_desc, hops=hops,
                    seeds=", ".join(store.entities[s]["name"] for s in seed_ids))
    header = (
        f"=== growmos task packet: query ===\n"
        f"Seeds: {', '.join(store.entities[s]['name'] for s in seed_ids)} · hops={hops} · "
        f"{len(nodes)} nodes · {total} edges (showing {min(total, max_triples)}).\n"
        f"Answer the question from the graph only and cite edge ids. "
        f"Optionally verify citations with `growmos check`.\n\n--- prompt ---\n"
    )
    meta = {"seeds": seed_ids, "nodes": len(nodes), "edges": total, "tokens": approx_tokens(prompt)}
    return header + prompt, meta


def check_packet(store: Store, content: str, hops: int = 2) -> Tuple[str, Dict[str, Any]]:
    g = Graph(store)
    seeds = g.find_seeds(content, k=12)
    nodes = g.khop(seeds, hops=hops)
    triples, total = g.serialize(nodes)
    prompt = render(load_prompt(store, "check"), graph=triples or "(empty)", content=content)
    meta = {"seeds": seeds, "nodes": len(nodes), "edges": total, "tokens": approx_tokens(prompt)}
    return prompt, meta
