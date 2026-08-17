"""Graph assembly, k-hop traversal, serialization, diagnostics and fact-checking.

Pure functions over a loaded Store. No graph library required (playbook §IX.D: NetworkX
is fine to a few hundred thousand edges; a dict-of-sets is fine well beyond what a repo
holds). Export to Neo4j/Postgres/DOT is in export.py.
"""

from __future__ import annotations

import random
from collections import defaultdict, deque
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .store import Store
from .util import norm_name, norm_predicate, tokens_of, truncate


class Graph:
    def __init__(self, store: Store):
        self.store = store
        self.out: Dict[str, List[str]] = defaultdict(list)   # eid -> [rid]
        self.inc: Dict[str, List[str]] = defaultdict(list)
        for rid, rel in store.relations.items():
            self.out[rel["source"]].append(rid)
            self.inc[rel["target"]].append(rid)

    # ------------------------------------------------------------ basics
    def degree(self, eid: str) -> int:
        return len(self.out.get(eid, ())) + len(self.inc.get(eid, ()))

    def neighbors(self, eid: str) -> Set[str]:
        st = self.store
        n: Set[str] = set()
        for rid in self.out.get(eid, ()):
            n.add(st.relations[rid]["target"])
        for rid in self.inc.get(eid, ()):
            n.add(st.relations[rid]["source"])
        n.discard(eid)
        return n

    def hubs(self, k: int = 10) -> List[Tuple[str, int]]:
        ranked = sorted(((eid, self.degree(eid)) for eid in self.store.entities), key=lambda x: (-x[1], x[0]))
        return ranked[:k]

    def components(self) -> List[Set[str]]:
        seen: Set[str] = set()
        comps: List[Set[str]] = []
        for eid in self.store.entities:
            if eid in seen:
                continue
            comp = {eid}
            dq = deque([eid])
            seen.add(eid)
            while dq:
                cur = dq.popleft()
                for nb in self.neighbors(cur):
                    if nb not in seen:
                        seen.add(nb)
                        comp.add(nb)
                        dq.append(nb)
            comps.append(comp)
        comps.sort(key=len, reverse=True)
        return comps

    # ------------------------------------------------------------ diagnostics (§V.E, §IX.F)
    def diagnostics(self) -> Dict[str, Any]:
        st = self.store
        n = len(st.entities)
        m = len(st.relations)
        comps = self.components()
        raw_forms = len(st.aliases)
        surface_forms = len({(a, t) for (a, t) in st.aliases})
        density = round(m / n, 2) if n else 0.0
        degrees = [self.degree(e) for e in st.entities]
        isolated = sum(1 for d in degrees if d == 0)
        pending = len(st.pending_sources())
        provisional = len(st.provisional_entities())
        stale = [e for e in st.entities if self.degree(e) >= int(st.config.get("profile_min_degree", 3))
                 and st.profile_is_stale(e)]
        signals = []
        if n >= 5 and len(comps) > 1:
            signals.append(f"{len(comps)} components — resolution may be missing cross-document links")
        if n and density < 1.0:
            signals.append("sparse graph (edges/nodes < 1.0) — many isolated entities")
        if provisional > max(5, n // 3):
            signals.append(f"{provisional} provisional entities awaiting resolution")
        if pending:
            signals.append(f"{pending} pending source(s) to extract")
        if stale:
            signals.append(f"{len(stale)} hub profile(s) stale or missing")
        return {
            "nodes": n, "edges": m, "components": len(comps),
            "largest_component": len(comps[0]) if comps else 0,
            "density": density, "isolated": isolated,
            "surface_forms": surface_forms,
            "compression_ratio": round(surface_forms / n, 2) if n else 0.0,
            "sources": len(st.sources), "pending_sources": pending,
            "provisional": provisional, "stale_profiles": len(stale),
            "schema_version": st.schema_version, "signals": signals,
        }

    # ------------------------------------------------------------ subgraph
    def khop(self, seeds: Iterable[str], hops: int = 2, max_nodes: int = 400) -> Set[str]:
        nodes: Set[str] = set(s for s in seeds if s in self.store.entities)
        frontier = set(nodes)
        for _ in range(max(0, hops)):
            nxt: Set[str] = set()
            for n in frontier:
                nxt |= self.neighbors(n)
            frontier = nxt - nodes
            nodes |= frontier
            if len(nodes) >= max_nodes:
                break
        return nodes

    def edges_within(self, nodes: Set[str]) -> List[Dict[str, Any]]:
        out = []
        for rel in self.store.relations.values():
            if rel["source"] in nodes and rel["target"] in nodes:
                out.append(rel)
        return out

    def serialize(self, nodes: Set[str], with_provenance: bool = True, max_triples: int = 300) -> Tuple[str, int]:
        """Serialize the induced subgraph as triples with edge ids + provenance (Appendix E)."""
        st = self.store
        rels = self.edges_within(nodes)
        rels.sort(key=lambda r: (-r.get("confidence", 1), st.entities[r["source"]]["name"], r["predicate"]))
        lines = []
        for rel in rels[:max_triples]:
            s = st.entities[rel["source"]]["name"]
            t = st.entities[rel["target"]]["name"]
            line = f"({s}) --[{rel['predicate']}]--> ({t})"
            if with_provenance:
                srcs = ", ".join(st.source_label(x) for x in rel.get("sources", [])[:3])
                line += f"  [{rel['id']}; from: {srcs or 'unknown'}]"
            lines.append(line)
        return "\n".join(lines), len(rels)

    def describe_nodes(self, nodes: Set[str], limit: int = 60) -> str:
        st = self.store
        ranked = sorted(nodes, key=lambda e: -self.degree(e))[:limit]
        lines = []
        for eid in ranked:
            e = st.entities[eid]
            lines.append(f"- {e['name']} ({e['type']}): {truncate(e.get('description', ''), 160)}")
        return "\n".join(lines)

    # ------------------------------------------------------------ seed selection
    def find_seeds(self, question: str, k: int = 4) -> List[str]:
        """Find entities mentioned in a question by alias containment, then token overlap."""
        st = self.store
        q = " " + norm_name(question) + " "
        scored: Dict[str, float] = {}
        for (alias, _t), eid in st.aliases.items():
            if len(alias) < 3:
                continue
            if f" {alias} " in q or (len(alias) > 5 and alias in q):
                scored[eid] = max(scored.get(eid, 0), 10 + len(alias))
        if len(scored) < k:
            qtoks = set(tokens_of(question))
            for eid, ent in st.entities.items():
                if eid in scored:
                    continue
                overlap = qtoks & set(tokens_of(ent["name"]))
                if overlap:
                    scored[eid] = max(scored.get(eid, 0), len(overlap) * 3 + min(self.degree(eid), 5) * 0.1)
        ranked = sorted(scored.items(), key=lambda kv: (-kv[1], -self.degree(kv[0])))
        return [eid for eid, _ in ranked[:k]]

    # ------------------------------------------------------------ fact-checking (§VII.B)
    def check_claim(self, source: str, predicate: str, target: str) -> Dict[str, Any]:
        """Check one (source, predicate, target) claim against the graph.

        Verdicts: 'supported' (edge exists, predicate matches loosely), 'pair_supported'
        (edge exists between the pair with a different predicate), 'contradicting_evidence'
        (no such edge, but the graph has related edges worth quoting), 'absent'.
        """
        st = self.store
        s = st.resolve_name(source)
        t = st.resolve_name(target)
        result: Dict[str, Any] = {"claim": f"({source}) --[{predicate}]--> ({target})", "verdict": "absent",
                                  "evidence": [], "notes": []}
        if s is None:
            result["notes"].append(f"'{source}' is not a known entity")
        if t is None:
            result["notes"].append(f"'{target}' is not a known entity")
        want = norm_predicate(predicate)
        want_toks = set(want.split())
        if s and t:
            for rid in self.out.get(s, []) + self.out.get(t, []):
                rel = st.relations[rid]
                if {rel["source"], rel["target"]} == {s, t}:
                    have = norm_predicate(rel["predicate"])
                    line = self._fmt(rel)
                    same_dir = rel["source"] == s
                    if same_dir and (have == want or want_toks & set(have.split())):
                        result["verdict"] = "supported"
                        result["evidence"].insert(0, line)
                    else:
                        if result["verdict"] != "supported":
                            result["verdict"] = "pair_supported"
                        result["evidence"].append(line)
        if result["verdict"] in ("absent",):
            # quote what the graph does say about each endpoint (the "Aldrin flew on Gemini 12" move)
            for eid in (s, t):
                if not eid:
                    continue
                for rid in (self.out.get(eid, []) + self.inc.get(eid, []))[:6]:
                    result["evidence"].append(self._fmt(st.relations[rid]))
            if result["evidence"]:
                result["verdict"] = "contradicting_evidence" if (s and t) else "absent"
        return result

    def _fmt(self, rel: Dict[str, Any]) -> str:
        st = self.store
        srcs = ", ".join(st.source_label(x) for x in rel.get("sources", [])[:2])
        return (f"({st.entities[rel['source']]['name']}) --[{rel['predicate']}]--> "
                f"({st.entities[rel['target']]['name']}) [{rel['id']}; from: {srcs or 'unknown'}]")

    # ------------------------------------------------------------ sampling (§XI.E)
    def sample(self, rng: Optional[random.Random] = None) -> Optional[str]:
        ids = list(self.store.entities)
        if not ids:
            return None
        rng = rng or random.Random()
        # weight toward connected nodes so the sample is informative
        weights = [1 + self.degree(e) for e in ids]
        return rng.choices(ids, weights=weights, k=1)[0]

    def entity_card(self, eid: str, max_edges: int = 25) -> str:
        st = self.store
        e = st.entities[eid]
        lines = [f"# {e['name']}  ({e['type']})  id={eid}", ""]
        if e.get("description"):
            lines.append(e["description"])
        prof = st.get_profile(eid)
        if prof:
            lines += ["", "## Profile", prof.get("summary", "")]
            if prof.get("key_facts"):
                lines.append("")
                lines += [f"- {f}" for f in prof["key_facts"]]
            tr = prof.get("time_range") or {}
            if tr:
                lines.append(f"\nTime range: {tr.get('start','?')} → {tr.get('end','?')}")
            if st.profile_is_stale(eid):
                lines.append("\n(⚠ profile is stale: source set changed since it was written)")
        lines += ["", f"## Edges (degree {self.degree(eid)})"]
        for rid in (self.out.get(eid, []) + self.inc.get(eid, []))[:max_edges]:
            lines.append("- " + self._fmt(st.relations[rid]))
        srcs = [st.source_label(s) for s in e.get("sources", [])]
        lines += ["", "## Sources"] + ([f"- {s}" for s in srcs] or ["- (none)"])
        aliases = [st._alias_display.get(k, k[0]) for k, v in st.aliases.items() if v == eid]
        if len(aliases) > 1:
            lines += ["", "## Aliases", ", ".join(sorted(set(aliases)))]
        return "\n".join(lines)
