"""Exporters: the schema maps directly onto property graphs and three SQL tables (§IX.D)."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from .graph import Graph
from .store import Store


def _q(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def to_json(store: Store) -> str:
    return json.dumps({
        "schema": store.schema,
        "entities": [store.entities[k] for k in sorted(store.entities)],
        "relations": [store.relations[k] for k in sorted(store.relations)],
        "aliases": [{"alias": store._alias_display.get(k, k[0]), "type": k[1], "entity": v}
                    for k, v in sorted(store.aliases.items(), key=lambda kv: kv[1])],
        "sources": [store.sources[k] for k in sorted(store.sources)],
    }, indent=2, ensure_ascii=False, sort_keys=True)


def to_dot(store: Store, max_nodes: int = 400) -> str:
    g = Graph(store)
    keep = {eid for eid, _ in g.hubs(max_nodes)}
    lines = ["digraph growmos {", '  rankdir=LR; node [shape=box, style="rounded,filled", fillcolor="#eef3ff"];']
    for eid in sorted(keep):
        e = store.entities[eid]
        size = 10 + min(g.degree(eid), 12)
        lines.append(f'  "{_q(eid)}" [label="{_q(e["name"])}\\n({e["type"]})", fontsize={size}];')
    for rel in store.relations.values():
        if rel["source"] in keep and rel["target"] in keep:
            lines.append(f'  "{_q(rel["source"])}" -> "{_q(rel["target"])}" [label="{_q(rel["predicate"])}"];')
    lines.append("}")
    return "\n".join(lines)


def to_mermaid(store: Store, max_nodes: int = 120) -> str:
    g = Graph(store)
    keep = {eid for eid, _ in g.hubs(max_nodes)}
    ids = {eid: f"n{i}" for i, eid in enumerate(sorted(keep))}
    lines = ["graph LR"]
    for eid, nid in ids.items():
        e = store.entities[eid]
        lines.append(f'  {nid}["{e["name"]} ({e["type"]})"]')
    for rel in store.relations.values():
        if rel["source"] in keep and rel["target"] in keep:
            lines.append(f'  {ids[rel["source"]]} -->|{rel["predicate"]}| {ids[rel["target"]]}')
    return "\n".join(lines)


def to_cypher(store: Store) -> str:
    lines = []
    for eid in sorted(store.entities):
        e = store.entities[eid]
        props = json.dumps({"id": eid, "name": e["name"], "description": e.get("description", ""),
                            "sources": e.get("sources", [])}, ensure_ascii=False)
        lines.append(f"MERGE (n:{e['type']} {{id: {json.dumps(eid)}}}) SET n += {props};")
    for rid in sorted(store.relations):
        r = store.relations[rid]
        rtype = "".join(ch if ch.isalnum() else "_" for ch in r["predicate"].upper()).strip("_") or "RELATED"
        props = json.dumps({"id": rid, "predicate": r["predicate"], "sources": r.get("sources", []),
                            "confidence": r.get("confidence", 1)}, ensure_ascii=False)
        lines.append(f"MATCH (a {{id: {json.dumps(r['source'])}}}), (b {{id: {json.dumps(r['target'])}}}) "
                     f"MERGE (a)-[e:{rtype} {{id: {json.dumps(rid)}}}]->(b) SET e += {props};")
    return "\n".join(lines)


def to_sql(store: Store) -> str:
    def lit(v: Any) -> str:
        if v is None:
            return "NULL"
        return "'" + str(v).replace("'", "''") + "'"
    lines = [
        "CREATE TABLE IF NOT EXISTS entities(id TEXT PRIMARY KEY, name TEXT, type TEXT, summary TEXT, sources TEXT);",
        "CREATE TABLE IF NOT EXISTS relations(id TEXT PRIMARY KEY, source_id TEXT, target_id TEXT, predicate TEXT, sources TEXT, confidence INTEGER);",
        "CREATE TABLE IF NOT EXISTS aliases(entity_id TEXT, alias TEXT, type TEXT);",
    ]
    for eid in sorted(store.entities):
        e = store.entities[eid]
        prof = store.get_profile(eid) or {}
        lines.append(f"INSERT OR REPLACE INTO entities VALUES ({lit(eid)}, {lit(e['name'])}, {lit(e['type'])}, "
                     f"{lit(prof.get('summary') or e.get('description', ''))}, {lit(json.dumps(e.get('sources', [])))});")
    for rid in sorted(store.relations):
        r = store.relations[rid]
        lines.append(f"INSERT OR REPLACE INTO relations VALUES ({lit(rid)}, {lit(r['source'])}, {lit(r['target'])}, "
                     f"{lit(r['predicate'])}, {lit(json.dumps(r.get('sources', [])))}, {int(r.get('confidence', 1))});")
    for (alias, etype), eid in sorted(store.aliases.items(), key=lambda kv: kv[1]):
        lines.append(f"INSERT INTO aliases VALUES ({lit(eid)}, {lit(store._alias_display.get((alias, etype), alias))}, {lit(etype)});")
    return "\n".join(lines)


def to_html(store: Store, focus: str = "") -> str:
    """Self-contained interactive explorer (force layout, cards, search). No external assets."""
    from pathlib import Path as _P
    from .util import now_iso
    g = Graph(store)
    d = g.diagnostics()
    profiles = {}
    for eid in store.entities:
        prof = store.get_profile(eid)
        if prof:
            prof = dict(prof)
            prof["stale"] = store.profile_is_stale(eid)
            profiles[eid] = prof
    aliases: Dict[str, List[str]] = {}
    for (a, t), eid in store.aliases.items():
        aliases.setdefault(eid, []).append(store._alias_display.get((a, t), a))
    data = {
        "entities": [{"id": e["id"], "name": e["name"], "type": e["type"], "description": e.get("description", ""),
                      "sources": e.get("sources", []), "provisional": bool(e.get("provisional"))}
                     for e in store.entities.values()],
        "relations": [{"id": r["id"], "source": r["source"], "target": r["target"], "predicate": r["predicate"],
                       "sources": r.get("sources", []), "confidence": r.get("confidence", 1)}
                      for r in store.relations.values()],
        "profiles": profiles,
        "aliases": {k: sorted(set(v)) for k, v in aliases.items()},
        "sources": {sid: rec.get("ref", sid) for sid, rec in store.sources.items()},
        "meta": {"components": d["components"], "density": d["density"], "schema_version": d["schema_version"],
                 "generated": now_iso()[:16].replace("T", " "), "focus": focus},
    }
    tpl = (_P(__file__).parent / "templates" / "view.html").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return tpl.replace("__TITLE__", store.root.name).replace("__DATA__", payload)


EXPORTERS = {"json": to_json, "html": to_html, "dot": to_dot, "mermaid": to_mermaid, "cypher": to_cypher, "sql": to_sql}
