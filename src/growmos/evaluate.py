"""Evaluation harness (§VIII) and production-readiness checklist (Appendix D).

Gold set format — one JSON file per document in `.growmos/eval/gold/`:

    {
      "source": "docs/apollo11.md",            # ref of a registered source
      "entities":  [{"name": "Apollo 11", "type": "EVENT"}, ...],
      "relations": [{"source": "Apollo 11", "target": "Moon"}, ...]   # (source,target) pairs only
    }

Scorer alias map — `.growmos/eval/aliases.json`: {"Neil Alden Armstrong": "Neil Armstrong", ...}
Extend it whenever the resolver picks a canonical form the gold set does not recognize
(the playbook calls this a scoring artifact, not a resolver bug).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .graph import Graph
from .store import Store
from .util import norm_name, now_iso, read_json, write_json


def _prf(pred: Set[Any], gold: Set[Any]) -> Tuple[float, float, float]:
    tp = len(pred & gold)
    p = tp / len(pred) if pred else (1.0 if not gold else 0.0)
    r = tp / len(gold) if gold else 1.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return round(p, 3), round(r, 3), round(f, 3)


class Evaluator:
    def __init__(self, store: Store):
        self.store = store
        self.alias_map: Dict[str, str] = {norm_name(k): v for k, v in
                                          (read_json(store.p("eval", "aliases.json"), {}) or {}).items()}

    def canon(self, name: str) -> str:
        n = norm_name(name)
        return norm_name(self.alias_map.get(n, name))

    def gold_files(self) -> List[Path]:
        return sorted(self.store.p("eval", "gold").glob("*.json"))

    def evaluate(self) -> Dict[str, Any]:
        st = self.store
        rows: List[Dict[str, Any]] = []
        unrecognized: Set[str] = set()
        mentions = st.mentions()
        for gf in self.gold_files():
            gold = read_json(gf, {}) or {}
            ref = gold.get("source")
            sid = next((s for s, r in st.sources.items() if r.get("ref") == ref), None)
            if sid is None:
                rows.append({"doc": ref or gf.name, "error": "source not registered/extracted"})
                continue
            gold_ents = {self.canon(e["name"]) for e in gold.get("entities", []) if e.get("name")}
            gold_pairs = {(self.canon(r["source"]), self.canon(r["target"]))
                          for r in gold.get("relations", []) if r.get("source") and r.get("target")}
            raw = [m for m in mentions if m.get("source") == sid]
            raw_names = {self.canon(m["name"]) for m in raw}
            resolved_names: Set[str] = set()
            for m in raw:
                eid = st.resolve_name(m["name"], m.get("type"))
                if eid and eid in st.entities:
                    cname = st.entities[eid]["name"]
                    resolved_names.add(self.canon(cname))
                    if self.canon(cname) not in gold_ents and norm_name(m["name"]) in {norm_name(x) for x in
                                                                                       [e["name"] for e in gold.get("entities", [])]}:
                        unrecognized.add(cname)
            pred_pairs = set()
            for rel in st.relations.values():
                if sid in rel.get("sources", []):
                    a = self.canon(st.entities[rel["source"]]["name"])
                    b = self.canon(st.entities[rel["target"]]["name"])
                    pred_pairs.add((a, b))
            rp, rr, rf = _prf(raw_names, gold_ents)
            sp, sr, sf = _prf(resolved_names, gold_ents)
            # relations: undirected pair match is the upper bound (§VIII.C)
            und_pred = {frozenset(p) for p in pred_pairs}
            und_gold = {frozenset(p) for p in gold_pairs}
            lp, lr, lf = _prf(und_pred, und_gold)
            rows.append({
                "doc": ref, "raw": {"p": rp, "r": rr, "f1": rf},
                "resolved": {"p": sp, "r": sr, "f1": sf},
                "relations": {"p": lp, "r": lr, "f1": lf},
                "missed_entities": sorted(gold_ents - raw_names)[:10],
                "extra_entities": sorted(raw_names - gold_ents)[:10],
            })
        # Auto-extend the scorer alias map: a canonical form whose raw mention matched a gold
        # name is a scoring artifact, not a resolver bug — record canonical -> gold name.
        auto_added = {}
        if unrecognized:
            amap = read_json(st.p("eval", "aliases.json"), {}) or {}
            gold_names = {}
            for gf in self.gold_files():
                for e in (read_json(gf, {}) or {}).get("entities", []):
                    if e.get("name"):
                        gold_names[norm_name(e["name"])] = e["name"]
            for cname in unrecognized:
                eid = st.resolve_name(cname)
                if not eid:
                    continue
                for (alias, _t), aeid in st.aliases.items():
                    if aeid == eid and alias in gold_names and cname not in amap:
                        amap[cname] = gold_names[alias]
                        auto_added[cname] = gold_names[alias]
                        break
            if auto_added:
                write_json(st.p("eval", "aliases.json"), amap)
                self.alias_map = {norm_name(k): v for k, v in amap.items()}
                return self.evaluate()  # rescore once with the extended map
        report = {"ts": now_iso(), "docs": rows, "unrecognized_canonicals": sorted(unrecognized),
                  "schema_version": st.schema_version}
        write_json(st.p("eval", "last_report.json"), report)
        st.state["last_eval"] = {"ts": report["ts"], "docs": len(rows)}
        return report


def format_eval(report: Dict[str, Any]) -> str:
    lines = [f"growmos eval · schema v{report.get('schema_version')} · {report.get('ts')}", ""]
    if not report["docs"]:
        lines.append("No gold files. Add `.growmos/eval/gold/<doc>.json` (see docs/evaluation.md).")
        return "\n".join(lines)
    lines.append(f"{'document':38} {'raw F1':>7} {'P':>5} {'R':>5} | {'resolved R':>10} | {'rel F1':>6}")
    for r in report["docs"]:
        if "error" in r:
            lines.append(f"{str(r['doc'])[:38]:38} {r['error']}")
            continue
        lines.append(f"{str(r['doc'])[:38]:38} {r['raw']['f1']:>7} {r['raw']['p']:>5} {r['raw']['r']:>5} | "
                     f"{r['resolved']['r']:>10} | {r['relations']['f1']:>6}")
        if r.get("missed_entities"):
            lines.append(f"    missed: {', '.join(r['missed_entities'])}")
        if r.get("extra_entities"):
            lines.append(f"    extra:  {', '.join(r['extra_entities'])}")
    if report.get("unrecognized_canonicals"):
        lines.append("")
        lines.append("Canonical forms the gold set does not recognize (extend eval/aliases.json):")
        for c in report["unrecognized_canonicals"]:
            lines.append(f"  - {c}")
    lines.append("")
    lines.append("Loop: edit .growmos/prompts/extract.md → re-extract → growmos eval → watch F1 move.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Doctor — the ten-item production readiness checklist
# ---------------------------------------------------------------------------

def doctor(store: Store) -> List[Dict[str, str]]:
    st = store
    g = Graph(st)
    d = g.diagnostics()
    checks: List[Dict[str, str]] = []

    def add(name: str, ok: Optional[bool], detail: str, fix: str = "") -> None:
        checks.append({"item": name, "status": "ok" if ok else ("warn" if ok is None else "fail"),
                       "detail": detail, "fix": fix})

    gold_files = list(st.p("eval", "gold").glob("*.json"))
    gold_n = len(gold_files)
    reviewers = sorted({str((read_json(f, {}) or {}).get("_reviewed_by", "human")) for f in gold_files})
    add("Gold set", gold_n >= 2 if gold_n else False,
        (f"{gold_n} gold file(s), reviewed by: {', '.join(reviewers)}") if gold_n else "no gold files → prompt changes are blind",
        "growmos next hands out gold packets until gold_min is met (or hand-write .growmos/eval/gold/*.json)")
    amap = read_json(st.p("eval", "aliases.json"), None)
    last = read_json(st.p("eval", "last_report.json"), {}) or {}
    unrec = last.get("unrecognized_canonicals") or []
    add("Alias map", (amap is not None) and not unrec,
        "scorer alias map present" + (f", {len(unrec)} unrecognized canonical(s)" if unrec else ""),
        "extend .growmos/eval/aliases.json with the canonical forms listed by growmos eval")
    add("Schema version", bool(st.schema.get("version")) and bool(st.schema.get("history")),
        f"schema v{st.schema_version}, {len(st.schema.get('history', []))} revision(s); "
        f"entities/relations carry schema_version", "growmos schema bump --note '...'")
    cap = int(st.config.get("max_docs_per_run", 0) or 0)
    add("Extraction cap", cap > 0, f"max_docs_per_run={cap}, max_entities_per_doc={st.config.get('max_entities_per_doc')}",
        "set max_docs_per_run in .growmos/config.json")
    add("Resolution fallback", True, "unmatched names always become single-element (provisional) clusters — built in")
    bad_prov = [r["id"] for r in st.relations.values() if not r.get("sources") or not r.get("created")]
    add("Provenance tracking", not bad_prov,
        f"{len(st.relations) - len(bad_prov)}/{len(st.relations)} edges carry source + timestamp",
        "growmos compact; re-link edges without provenance")
    add("Incremental update", True, "sources are content-hashed; only changed docs go pending — built in")
    conn_ok: Optional[bool] = True if d["components"] <= 1 or d["nodes"] < 5 else None
    add("Connectivity monitor", conn_ok,
        f"{d['components']} component(s), largest {d['largest_component']}/{d['nodes']} nodes, density {d['density']}",
        "growmos resolve → apply clusters; islands usually mean unresolved surface forms")
    stale = d["stale_profiles"]
    add("Summarization trigger", True if not stale else None,
        "profiles are keyed to the entity's source-set hash; " + (f"{stale} stale/missing hub profile(s)" if stale else "all hub profiles fresh"),
        "growmos next (it will hand you profile packets)")
    ls = st.state.get("last_sample")
    days = None
    if ls:
        try:
            dt = datetime.fromisoformat(ls.replace("Z", "+00:00"))
            days = (datetime.now(timezone.utc) - dt).days
        except ValueError:
            days = None
    who = "human" if st.state.get("last_sample_entity") is None else ("agent" if any(
        r.get("kind") == "review" for r in st.state.get("runs", [])[-20:]) else "human")
    add("Human sample", (days is not None and days <= int(st.config.get("review_days", 7))) if ls else False,
        f"last review {days} day(s) ago ({who}; ok={st.state.get('last_sample_ok', '?')})" if days is not None
        else "no one has reviewed a node yet",
        "growmos next hands out a review packet every review_days (or growmos sample for a human pass)")
    return checks


def format_doctor(checks: List[Dict[str, str]]) -> str:
    icon = {"ok": "✅", "warn": "⚠️ ", "fail": "❌"}
    lines = ["growmos doctor — production readiness (playbook Appendix D)", ""]
    for c in checks:
        lines.append(f"{icon[c['status']]} {c['item']:24} {c['detail']}")
        if c["status"] != "ok" and c.get("fix"):
            lines.append(f"     fix: {c['fix']}")
    ok = sum(1 for c in checks if c["status"] == "ok")
    lines.append("")
    lines.append(f"{ok}/{len(checks)} green. A pipeline with all ten is production-ready; each missing item is a nameable risk.")
    return "\n".join(lines)
