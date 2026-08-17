"""growmos command-line interface."""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from . import schema as S
from .graph import Graph
from .store import Store, StoreError, DIRNAME
from .util import find_repo_root, now_iso, read_json, today, truncate, write_json


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _root(args: argparse.Namespace) -> Path:
    if getattr(args, "root", None):
        return Path(args.root).resolve()
    return find_repo_root()


def _store(args: argparse.Namespace) -> Store:
    st = Store(_root(args))
    if not st.exists:
        raise StoreError(f"no {DIRNAME}/ here. Run `growmos init` (optionally `--preset software|general|research|business`).")
    return st.load()


def _read_payload(path: str) -> Any:
    if path == "-":
        text = sys.stdin.read()
    else:
        p = Path(path)
        if not p.exists():
            raise StoreError(f"file not found: {path}")
        text = p.read_text(encoding="utf-8")
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise StoreError(f"payload is not valid JSON: {e}")


def _print(obj: Any, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(obj, indent=2, ensure_ascii=False))
    else:
        print(obj)


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else Path.cwd()
    st = Store.init(root, preset_name=args.preset, force=args.force)
    print(f"✓ {DIRNAME}/ ready at {root} (preset: {st.config.get('preset')}, schema v{st.schema_version})")
    from .integrate import integrate, detect_targets
    targets: List[str] = []
    if args.agent == "auto":
        targets = detect_targets(root) or ["generic"]
    elif args.agent and args.agent != "none":
        targets = [args.agent]
    for t in targets:
        for line in integrate(root, t):
            print("  " + line)
    if not args.no_scan:
        rep = st.scan()
        st.save()
        print(f"✓ scanned sources: {len(rep['new'])} new, {len(rep['changed'])} changed "
              f"({len(st.pending_sources())} pending)")
    print("\nNext: run `growmos next` (or let your agent do it) to start growing the graph. `growmos --help` for more.")
    return 0


def cmd_integrate(args: argparse.Namespace) -> int:
    from .integrate import integrate
    for line in integrate(_root(args), args.target, file=args.file or ""):
        print(line)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    st = _store(args)
    g = Graph(st)
    d = g.diagnostics()
    if args.json:
        _print(d, True)
        return 0
    print(f"growmos · {st.root.name} · schema v{d['schema_version']}")
    print(f"  nodes {d['nodes']} · edges {d['edges']} · density {d['density']} · components {d['components']} "
          f"(largest {d['largest_component']}) · isolated {d['isolated']}")
    print(f"  sources {d['sources']} ({d['pending_sources']} pending) · surface forms {d['surface_forms']} "
          f"(compression {d['compression_ratio']}) · provisional {d['provisional']} · stale profiles {d['stale_profiles']}")
    hubs = g.hubs(8)
    if hubs:
        print("  hubs: " + ", ".join(f"{st.entities[e]['name']} ({deg})" for e, deg in hubs))
    for s in d["signals"]:
        print(f"  ⚠ {s}")
    if not d["signals"]:
        print("  ✓ no warnings")
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    st = _store(args)
    g = Graph(st)
    d = g.diagnostics()
    budget = args.budget
    lines = [f"## growmos · knowledge graph of `{st.root.name}` (schema v{d['schema_version']})"]
    lines.append(f"{d['nodes']} nodes · {d['edges']} edges · {d['components']} component(s) · "
                 f"{d['sources']} sources ({d['pending_sources']} pending) · {d['provisional']} provisional")
    hubs = g.hubs(6 if args.brief else 12)
    if hubs:
        lines.append("Hubs: " + "; ".join(
            f"{st.entities[e]['name']} [{st.entities[e]['type']}, deg {deg}]" for e, deg in hubs))
    if not args.brief:
        for e, _ in hubs[:5]:
            ent = st.entities[e]
            if ent.get("description"):
                lines.append(f"- {ent['name']}: {truncate(ent['description'], 200)}")
    recent = sorted(st.relations.values(), key=lambda r: r.get("updated") or r.get("created") or "", reverse=True)[:5 if args.brief else 10]
    if recent:
        lines.append("Recent edges: " + " · ".join(
            f"({st.entities[r['source']]['name']}) --[{r['predicate']}]--> ({st.entities[r['target']]['name']})"
            for r in recent))
    tail = st.journal_tail(1 if args.brief else 3)
    if tail:
        lines.append("Journal (latest):")
        for t in tail:
            lines.append("  " + truncate(t.replace("\n", " "), 400 if args.brief else 800))
    if d["signals"]:
        lines.append("Health: " + "; ".join(d["signals"]))
    todo = []
    if d["pending_sources"]:
        todo.append("`growmos next` to extract pending sources")
    if d["provisional"] > 0:
        todo.append("`growmos resolve` to cluster provisional entities")
    if todo:
        lines.append("To do: " + "; ".join(todo))
    lines.append("Use: `growmos query \"…\"` · `growmos remember` · `growmos link` · `growmos journal` · `growmos check`")
    text = "\n".join(lines)
    if len(text) > budget * 4:
        text = text[: budget * 4] + "\n…(truncated; raise --budget)"
    print(text)
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    st = _store(args)
    rep = st.scan()
    st.save()
    if not args.quiet:
        print(f"scan: {len(rep['new'])} new, {len(rep['changed'])} changed, {len(rep['missing'])} missing; "
              f"{len(st.pending_sources())} pending")
        for r in rep["new"][:20]:
            print(f"  + {r}")
        for r in rep["changed"][:20]:
            print(f"  ~ {r}")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    st = _store(args)
    for ref in args.refs:
        if ref.startswith("http://") or ref.startswith("https://"):
            import urllib.request
            with urllib.request.urlopen(ref, timeout=60) as resp:
                text = resp.read().decode("utf-8", "replace")
            rec, changed = st.register_source(ref, text=text, kind="url", title=ref)
        elif ref == "-":
            text = sys.stdin.read()
            label = args.name or f"stdin:{today()}"
            rec, changed = st.register_source(label, text=text, kind="text", title=label)
        else:
            p = Path(ref)
            if not p.exists():
                raise StoreError(f"not found: {ref}")
            rel = p.resolve().relative_to(st.root).as_posix() if p.resolve().is_relative_to(st.root) else None
            if rel is None:
                text = p.read_text(encoding="utf-8", errors="replace")
                rec, changed = st.register_source(str(p.resolve()), text=text, kind="text", title=p.name)
            else:
                rec, changed = st.register_source(rel)
        print(f"{'queued' if changed else 'unchanged'}: {rec['ref']}  ({rec['id']})")
    st.save()
    return 0


def _next_packet(st: Store) -> Optional[Dict[str, Any]]:
    """Decide the next unit of judgment work. Order: extraction → resolution → profiles."""
    from .prompts import extraction_packet, resolution_blocks, resolution_packet, summarize_packet
    cap = int(st.config.get("max_docs_per_run", 25))
    runs_today = sum(1 for r in st.state.get("runs", []) if r.get("kind") == "extraction" and r.get("ts", "").startswith(today()))
    pend = st.pending_sources()
    if pend and runs_today < cap:
        text, meta = extraction_packet(st, pend[0]["id"])
        return {"kind": "extraction", "text": text, "meta": meta}
    if pend and runs_today >= cap:
        return {"kind": "capped", "text": f"Extraction cap reached for today ({cap}/day; see max_docs_per_run). "
                                             f"{len(pend)} source(s) still pending.", "meta": {}}
    for etype in st.entity_types:
        blocks = resolution_blocks(st, etype)
        if blocks:
            text, meta = resolution_packet(st, etype, blocks[0], 0)
            _remember_packet(st, etype, meta["names"])
            return {"kind": "resolution", "text": text, "meta": meta}
    g = Graph(st)
    min_deg = int(st.config.get("profile_min_degree", 3))
    for eid, deg in g.hubs(50):
        if deg >= min_deg and st.profile_is_stale(eid):
            text, meta = summarize_packet(st, eid)
            return {"kind": "profile", "text": text, "meta": meta}
    # ground truth: keep at least `gold_min` gold files (agent-reviewed; humans may overwrite)
    from .prompts import gold_packet, review_packet
    gold_min = int(st.config.get("gold_min", 2))
    have = {read_json(f, {}).get("source") for f in st.p("eval", "gold").glob("*.json")}
    if len(have) < gold_min:
        cands = [r for r in st.sources.values() if r.get("status") == "extracted" and r.get("kind", "file") == "file"
                 and r["ref"] not in have]
        cands.sort(key=lambda r: -(r.get("stats") or {}).get("entities", 0))
        if cands:
            text, meta = gold_packet(st, cands[0]["id"])
            return {"kind": "gold", "text": text, "meta": meta}
    # comprehension check: one node every `review_days`
    if st.entities and _days_since(st.state.get("last_sample")) is None or \
       (st.entities and _days_since(st.state.get("last_sample")) >= int(st.config.get("review_days", 7))):
        eid = g.sample(random.Random(len(st.relations)))
        if eid:
            text, meta = review_packet(st, eid)
            return {"kind": "review", "text": text, "meta": meta}
    return None


def _days_since(ts: Optional[str]) -> Optional[int]:
    if not ts:
        return None
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - dt).days


def _remember_packet(st: Store, etype: str, names: List[str]) -> None:
    """Cache the names handed out in a resolution packet so `apply` can enforce
    'every input name appears in exactly one cluster' against the real input."""
    st.state.setdefault("open_packets", {})[f"resolution:{etype.upper()}"] = names
    st.save()


def cmd_next(args: argparse.Namespace) -> int:
    st = _store(args)
    if args.scan:
        st.scan()
        st.save()
    pkt = _next_packet(st)
    if pkt is None:
        print("✓ graph is up to date: no pending sources, no provisional entities, hub profiles fresh.\n"
              "  Tip: `growmos scan` after editing docs, `growmos remember`/`link` while you work, `growmos sample` to review a node.")
        return 0
    if args.json:
        _print(pkt, True)
    else:
        print(pkt["text"])
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    st = _store(args)
    payload = _read_payload(args.file)
    kind = args.kind
    if kind == "extraction":
        source = args.source or (payload.get("source") if isinstance(payload, dict) else None)
        if not source:
            raise StoreError("--source <src_id> is required (or a 'source' key in the JSON)")
        if source not in st.sources:
            # allow passing a ref instead of an id
            match = [s for s, r in st.sources.items() if r.get("ref") == source]
            if not match:
                raise StoreError(f"unknown source '{source}'")
            source = match[0]
        rep = st.apply_extraction(source, payload, chunk=args.chunk, final=not args.partial)
        st.record_run("extraction", {"source": source, "entities": rep["entities"], "relations": rep["relations"]})
        st.save()
        print(f"✓ extraction applied to {st.source_label(source)}: {rep['entities']} entities "
              f"({rep['new_entities']} new), {rep['relations']} relations ({rep['new_relations']} new)")
        for p in rep["problems"]:
            print(f"  ! {p}")
        prov = len(st.provisional_entities())
        if prov:
            print(f"  → {prov} provisional entit{'y' if prov == 1 else 'ies'} await resolution (growmos next)")
    elif kind == "resolution":
        etype = (args.type or (payload.get("type") if isinstance(payload, dict) else "") or "").upper()
        if not etype:
            raise StoreError("--type <ENTITY_TYPE> is required")
        cached = st.state.get("open_packets", {}).pop(f"resolution:{etype}", None)
        if isinstance(payload, dict) and isinstance(payload.get("input_names"), list):
            wanted = payload["input_names"]
        elif cached:
            wanted = cached
        else:
            # no packet on record: scope to the names the payload itself mentions
            wanted = []
            for c in payload.get("clusters", []) if isinstance(payload, dict) else []:
                for a in (c.get("aliases") or []) if isinstance(c, dict) else []:
                    if isinstance(a, str) and a not in wanted:
                        wanted.append(a)
        clusters, problems = S.validate_resolution(payload, wanted)
        rep = st.apply_resolution(etype, clusters)
        st.record_run("resolution", rep)
        st.save()
        print(f"✓ resolution applied for {etype}: {rep['clusters']} clusters, {rep['merged']} merged, {rep['renamed']} renamed")
        for p in problems:
            print(f"  ! {p}")
    elif kind == "profile":
        eid = args.entity or (payload.get("entity") if isinstance(payload, dict) else None)
        if not eid:
            raise StoreError("--entity <id> is required")
        if eid not in st.entities:
            r = st.resolve_name(eid)
            if not r:
                raise StoreError(f"unknown entity '{eid}'")
            eid = r
        prof, problems = S.validate_profile(payload)
        st.set_profile(eid, prof)
        st.record_run("profile", {"entity": eid})
        st.save()
        print(f"✓ profile saved for {st.entities[eid]['name']} ({len(prof['key_facts'])} key facts, "
              f"{prof['time_range']['start']}→{prof['time_range']['end']})")
        for p in problems:
            print(f"  ! {p}")
    elif kind == "gold":
        source = args.source or (payload.get("source") if isinstance(payload, dict) else None)
        if not source:
            raise StoreError("--source <src_id> is required")
        if source not in st.sources:
            match = [s for s, r in st.sources.items() if r.get("ref") == source]
            if not match:
                raise StoreError(f"unknown source '{source}'")
            source = match[0]
        ref = st.sources[source]["ref"]
        ents = [{"name": e["name"], "type": str(e.get("type", "")).upper()} for e in payload.get("entities", [])
                if isinstance(e, dict) and isinstance(e.get("name"), str) and e["name"].strip()]
        pairs = [{"source": r["source"], "target": r["target"]} for r in payload.get("relations", [])
                 if isinstance(r, dict) and isinstance(r.get("source"), str) and isinstance(r.get("target"), str)]
        out = st.p("eval", "gold", Path(ref).stem + ".json")
        write_json(out, {"source": ref, "entities": ents, "relations": pairs,
                         "_reviewed_by": args.reviewer or "agent", "_ts": now_iso()})
        st.record_run("gold", {"source": source, "reviewer": args.reviewer or "agent"})
        st.save()
        print(f"✓ gold set written for {ref}: {len(ents)} entities, {len(pairs)} relation pairs "
              f"(reviewed by {args.reviewer or 'agent'}; humans may edit {out.relative_to(st.root)})")
    elif kind == "review":
        eid = args.entity or (payload.get("entity") if isinstance(payload, dict) else None)
        if not eid or eid not in st.entities:
            r = st.resolve_name(eid or "")
            if not r:
                raise StoreError(f"unknown entity '{eid}'")
            eid = r
        ok = bool(payload.get("ok"))
        issues = [i for i in payload.get("issues", []) if isinstance(i, str)]
        fixes = [f for f in payload.get("fixes", []) if isinstance(f, str)]
        st.state["last_sample"] = now_iso()
        st.state["last_sample_entity"] = eid
        st.state["last_sample_ok"] = ok
        st.record_run("review", {"entity": eid, "ok": ok, "issues": len(issues), "reviewer": args.reviewer or "agent"})
        note = f"Review of {st.entities[eid]['name']} ({eid}): {'ok' if ok else 'issues found'}."
        if issues:
            note += "\n" + "\n".join(f"- {i}" for i in issues)
        if fixes:
            note += "\nFixes:\n" + "\n".join(f"- {f}" for f in fixes)
        st.journal(note, author=args.reviewer or "agent-review")
        st.save()
        print(f"✓ review recorded for {st.entities[eid]['name']}: {'ok' if ok else f'{len(issues)} issue(s)'}")
    else:
        raise StoreError("kind must be extraction | resolution | profile | gold | review")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    from .prompts import extraction_packet
    st = _store(args)
    sid = args.source
    if sid not in st.sources:
        match = [s for s, r in st.sources.items() if r.get("ref") == sid]
        if not match:
            raise StoreError(f"unknown source '{sid}' (growmos add / scan first)")
        sid = match[0]
    text, meta = extraction_packet(st, sid, chunk_index=args.chunk)
    print(text if not args.json else json.dumps({"text": text, "meta": meta}, indent=2))
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    from .prompts import resolution_blocks, resolution_packet
    st = _store(args)
    types = [args.type.upper()] if args.type else st.entity_types
    emitted = 0
    for etype in types:
        blocks = resolution_blocks(st, etype, only_provisional=not args.all)
        for i, block in enumerate(blocks):
            text, meta = resolution_packet(st, etype, block, i)
            _remember_packet(st, etype, meta["names"])
            print(text)
            print()
            emitted += 1
            if not args.every:
                break
        if emitted and not args.every:
            break
    if not emitted:
        print("✓ nothing to resolve (no provisional entities). Use --all to re-cluster everything.")
    return 0


def cmd_summarize(args: argparse.Namespace) -> int:
    from .prompts import summarize_packet
    st = _store(args)
    g = Graph(st)
    if args.entity:
        eid = st.resolve_name(args.entity) or (args.entity if args.entity in st.entities else None)
        if not eid:
            raise StoreError(f"unknown entity '{args.entity}'")
        targets = [eid]
    else:
        min_deg = int(st.config.get("profile_min_degree", 3))
        targets = [e for e, d in g.hubs(100) if d >= min_deg and (args.force or st.profile_is_stale(e))][: args.limit]
    if not targets:
        print("✓ all hub profiles are fresh.")
        return 0
    for eid in targets:
        text, _ = summarize_packet(st, eid)
        print(text)
        print()
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    from .prompts import query_packet
    st = _store(args)
    text, meta = query_packet(st, args.question, seeds=args.seed, hops=args.hops, max_triples=args.max_triples)
    st.state["counters"]["queries"] = st.state["counters"].get("queries", 0) + 1
    st.save()
    if args.auto:
        from .providers import call_text
        answer = call_text(text.split("--- prompt ---\n", 1)[-1], st.config)
        print(answer)
        return 0
    if args.triples_only:
        print(text.split("<graph", 1)[-1].split("</graph>")[0].split(">", 1)[-1].strip())
        return 0
    print(text)
    return 0


def _parse_claims(text: str) -> List[Dict[str, str]]:
    import re
    claims = []
    for line in text.splitlines():
        m = re.search(r"\(?\s*(.+?)\s*\)?\s*--\[(.+?)\]-->\s*\(?\s*(.+?)\s*\)?\s*(\[.*)?$", line.strip())
        if m:
            claims.append({"source": m.group(1).strip("() "), "predicate": m.group(2).strip(), "target": m.group(3).strip("() ")})
    return claims


def cmd_check(args: argparse.Namespace) -> int:
    st = _store(args)
    g = Graph(st)
    text = sys.stdin.read() if args.text == "-" else args.text
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    claims = _parse_claims(text)
    if claims:
        results = [g.check_claim(c["source"], c["predicate"], c["target"]) for c in claims]
        if args.json:
            _print(results, True)
            return 0
        icon = {"supported": "✅", "pair_supported": "🟡", "contradicting_evidence": "❌", "absent": "❓"}
        for r in results:
            print(f"{icon[r['verdict']]} {r['claim']} → {r['verdict']}")
            for n in r["notes"]:
                print(f"    note: {n}")
            for e in r["evidence"][:6]:
                print(f"    {e}")
        return 0
    # free text: emit an evaluator packet with the surrounding subgraph
    from .prompts import check_packet
    pkt, meta = check_packet(st, text)
    print(f"=== growmos task packet: fact-check ({meta['nodes']} nodes, {meta['edges']} edges) ===\n"
          f"Tip: write claims as `(A) --[pred]--> (B)` lines for deterministic checking.\n\n--- prompt ---\n{pkt}")
    return 0


def cmd_remember(args: argparse.Namespace) -> int:
    st = _store(args)
    src = args.source or f"session:{today()}"
    etype = args.type.upper()
    if etype not in st.entity_types:
        print(f"! type {etype} not in schema {st.entity_types}; adding it (schema v{st.schema_version + 1})")
        st.bump_schema(f"added type {etype} via remember", add_types=[etype])
    eid, created = st.remember(args.name, etype, args.desc or "", src)
    st.record_run("remember", {"entity": eid})
    st.save()
    print(f"{'✓ created' if created else '✓ updated'} {eid}  ← {src}")
    return 0


def cmd_link(args: argparse.Namespace) -> int:
    st = _store(args)
    src = args.source or f"session:{today()}"
    when = None
    if args.start or args.end:
        when = {"start": args.start or "unknown", "end": args.end or "ongoing"}
    rid, created = st.link(args.a, args.predicate, args.b, src,
                           types=(args.type_a, args.type_b), when=when)
    st.record_run("link", {"relation": rid})
    st.save()
    r = st.relations[rid]
    print(f"{'✓ created' if created else '✓ corroborated'} ({st.entities[r['source']]['name']}) --[{r['predicate']}]--> "
          f"({st.entities[r['target']]['name']})  [{rid}; confidence {r.get('confidence', 1)}]")
    return 0


def cmd_journal(args: argparse.Namespace) -> int:
    st = _store(args)
    if args.text is None:
        for t in st.journal_tail(args.tail):
            print(t)
            print()
        return 0
    text = sys.stdin.read() if args.text == "-" else args.text
    st.journal(text, author=args.author)
    print("✓ journal entry added")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    st = _store(args)
    g = Graph(st)
    eid = st.resolve_name(args.name) or (args.name if args.name in st.entities else None)
    if not eid:
        raise StoreError(f"unknown entity '{args.name}' (try `growmos search`)")
    if args.json:
        _print({"entity": st.entities[eid], "profile": st.get_profile(eid),
                "edges": [st.relations[r] for r in g.out.get(eid, []) + g.inc.get(eid, [])]}, True)
        return 0
    print(g.entity_card(eid))
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    st = _store(args)
    g = Graph(st)
    q = args.text.lower()
    hits = {}
    for (alias, etype), eid in st.aliases.items():
        if q in alias:
            hits[eid] = True
    for eid, e in st.entities.items():
        if q in e.get("description", "").lower():
            hits.setdefault(eid, False)
    rows = sorted(hits, key=lambda e: (-g.degree(e), e))[: args.limit]
    for eid in rows:
        e = st.entities[eid]
        print(f"{e['name']}  [{e['type']}, deg {g.degree(eid)}, id {eid}]  {truncate(e.get('description', ''), 100)}")
    if not rows:
        print("no matches")
    return 0


def cmd_sample(args: argparse.Namespace) -> int:
    st = _store(args)
    g = Graph(st)
    eid = g.sample(random.Random(args.seed) if args.seed is not None else None)
    if not eid:
        print("graph is empty")
        return 0
    print(g.entity_card(eid))
    print("\nReview: does every edge above trace to its source? If not: `growmos link`/`growmos remember` to fix, "
          "or edit the extraction prompt and re-extract.")
    st.state["last_sample"] = now_iso()
    st.state["last_sample_entity"] = eid
    st.save()
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from .evaluate import Evaluator, format_eval
    st = _store(args)
    rep = Evaluator(st).evaluate()
    st.save()
    if args.json:
        _print(rep, True)
    else:
        print(format_eval(rep))
    return 0


def cmd_gold_template(args: argparse.Namespace) -> int:
    st = _store(args)
    sid = args.source
    match = [s for s, r in st.sources.items() if r.get("ref") == sid or s == sid]
    if not match:
        raise StoreError(f"unknown source '{sid}'")
    sid = match[0]
    ref = st.sources[sid]["ref"]
    ents = []
    pairs = []
    for m in st.mentions():
        if m.get("source") == sid:
            ents.append({"name": m["name"], "type": m["type"]})
    for rel in st.relations.values():
        if sid in rel.get("sources", []):
            pairs.append({"source": st.entities[rel["source"]]["name"], "target": st.entities[rel["target"]]["name"]})
    out = st.p("eval", "gold", Path(ref).stem + ".json")
    if out.exists() and not args.force:
        raise StoreError(f"{out} exists (use --force to overwrite)")
    write_json(out, {"source": ref, "entities": ents, "relations": pairs,
                     "_note": "Pre-filled from the current extraction. Hand-correct it: remove wrong entities, add missed ones."})
    print(f"✓ wrote {out} — now hand-correct it (this is the gold set; do not leave it as-is)")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from .evaluate import doctor, format_doctor
    st = _store(args)
    checks = doctor(st)
    st.save()
    if args.json:
        _print(checks, True)
    else:
        print(format_doctor(checks))
    return 0 if all(c["status"] == "ok" for c in checks) or not args.strict else 1


def cmd_export(args: argparse.Namespace) -> int:
    from .export import EXPORTERS
    st = _store(args)
    fn = EXPORTERS.get(args.format)
    if not fn:
        raise StoreError(f"format must be one of {', '.join(EXPORTERS)}")
    out = fn(st)
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        print(f"✓ wrote {args.out}")
    else:
        print(out)
    return 0


def cmd_schema(args: argparse.Namespace) -> int:
    st = _store(args)
    if args.action == "show":
        _print(st.schema, True)
        return 0
    if args.action == "bump":
        v = st.bump_schema(args.note or "manual bump", add_types=args.add_type or [], add_predicates=args.add_predicate or [])
        st.save()
        print(f"✓ schema bumped to v{v}; entity types: {', '.join(st.entity_types)}")
        return 0
    if args.action == "presets":
        for k, v in S.PRESETS.items():
            print(f"{k:10} {v['description']}\n{'':10} types: {', '.join(v['entity_types'])}")
        return 0
    raise StoreError("schema action must be show | bump | presets")


def cmd_compact(args: argparse.Namespace) -> int:
    st = _store(args)
    rep = st.compact()
    st.save()
    print(f"✓ compacted: {rep}")
    return 0


def cmd_prompts(args: argparse.Namespace) -> int:
    from .prompts import install_default_prompts, PROMPT_NAMES
    st = _store(args)
    if args.action == "list":
        for n in PROMPT_NAMES:
            p = st.p("prompts", f"{n}.md")
            print(f"{n:10} {p if p.exists() else '(default)'}")
    elif args.action == "reset":
        w = install_default_prompts(st.p("prompts"), force=True)
        print(f"✓ reset prompts: {', '.join(w)}")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    """Headless loop: run packets through the configured provider until nothing is left."""
    from .providers import call_structured, ProviderError
    from .prompts import extraction_packet, chunk_text, resolution_blocks, resolution_packet, summarize_packet
    st = _store(args)
    if args.scan:
        st.scan()
    done = 0
    limit = args.limit
    try:
        # extraction
        for rec in st.pending_sources():
            if done >= limit:
                break
            text = st.source_text(rec["id"])
            chunks = chunk_text(text, int(st.config.get("chunk_chars", 6000)))
            for ci in range(len(chunks)):
                pkt, meta = extraction_packet(st, rec["id"], ci)
                prompt = pkt.split("--- prompt ---\n", 1)[-1]
                data = call_structured(prompt, S.extraction_schema(st.entity_types), stage="extract", config=st.config)
                rep = st.apply_extraction(rec["id"], data, chunk=ci, final=(ci == len(chunks) - 1))
                print(f"extracted {rec['ref']} chunk {ci + 1}/{len(chunks)}: {rep['entities']} ents, {rep['relations']} rels")
            st.record_run("extraction", {"source": rec["id"], "auto": True})
            st.save()
            done += 1
        # resolution
        for etype in st.entity_types:
            for i, block in enumerate(resolution_blocks(st, etype)):
                pkt, meta = resolution_packet(st, etype, block, i)
                prompt = pkt.split("--- prompt ---\n", 1)[-1]
                data = call_structured(prompt, S.RESOLUTION_SCHEMA, stage="reason", config=st.config)
                clusters, problems = S.validate_resolution(data, meta["names"])
                rep = st.apply_resolution(etype, clusters)
                print(f"resolved {etype}: {rep}")
                st.record_run("resolution", {**rep, "auto": True})
                st.save()
        # profiles
        g = Graph(st)
        min_deg = int(st.config.get("profile_min_degree", 3))
        for eid, deg in g.hubs(50):
            if deg >= min_deg and st.profile_is_stale(eid):
                pkt, meta = summarize_packet(st, eid)
                prompt = pkt.split("--- prompt ---\n", 1)[-1]
                data = call_structured(prompt, S.PROFILE_SCHEMA, stage="reason", config=st.config)
                prof, _ = S.validate_profile(data)
                st.set_profile(eid, prof)
                st.save()
                print(f"profiled {st.entities[eid]['name']}")
        # gold + review, so the loop is complete without a human
        from .prompts import gold_packet, review_packet
        gold_min = int(st.config.get("gold_min", 2))
        have = {read_json(f, {}).get("source") for f in st.p("eval", "gold").glob("*.json")}
        cands = [r for r in st.sources.values() if r.get("status") == "extracted" and r.get("kind", "file") == "file"
                 and r["ref"] not in have]
        cands.sort(key=lambda r: -(r.get("stats") or {}).get("entities", 0))
        for rec in cands[: max(0, gold_min - len(have))]:
            pkt, meta = gold_packet(st, rec["id"])
            data = call_structured(pkt.split("--- prompt ---\n", 1)[-1], S.GOLD_SCHEMA, stage="reason", config=st.config)
            write_json(st.p("eval", "gold", Path(rec["ref"]).stem + ".json"),
                       {"source": rec["ref"], "entities": data.get("entities", []), "relations": data.get("relations", []),
                        "_reviewed_by": "agent (headless)", "_ts": now_iso()})
            print(f"gold written for {rec['ref']}")
        d = _days_since(st.state.get("last_sample"))
        if st.entities and (d is None or d >= int(st.config.get("review_days", 7))):
            eid = g.sample(random.Random(len(st.relations)))
            pkt, meta = review_packet(st, eid)
            data = call_structured(pkt.split("--- prompt ---\n", 1)[-1], S.REVIEW_SCHEMA, stage="reason", config=st.config)
            st.state["last_sample"] = now_iso()
            st.state["last_sample_entity"] = eid
            st.state["last_sample_ok"] = bool(data.get("ok"))
            st.journal(f"Headless review of {st.entities[eid]['name']}: {'ok' if data.get('ok') else 'issues'}\n"
                       + "\n".join(f"- {i}" for i in data.get("issues", [])), author="agent-review")
            print(f"reviewed {st.entities[eid]['name']}: {'ok' if data.get('ok') else 'issues found'}")
        from .evaluate import Evaluator
        Evaluator(st).evaluate()
    except ProviderError as e:
        st.save()
        print(f"provider error: {e}", file=sys.stderr)
        return 2
    st.save()
    print("✓ ingest complete")
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp import serve
    serve()
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="growmos",
        description="growmos — a living knowledge graph that grows with your repo (Codician, MIT).",
        epilog="Agent loop: growmos context → work → growmos remember/link/journal → growmos next → apply. Docs: https://github.com/codician-team/growmos",
    )
    p.add_argument("--root", help="repository root (default: auto-detect via .growmos/ or .git/)")
    p.add_argument("--version", action="version", version=f"growmos {__version__}")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("init", help="create .growmos/ in this repo (and wire your agent CLI)")
    sp.add_argument("--preset", default="software", choices=list(S.PRESETS))
    sp.add_argument("--agent", default="auto", help="claude|codex|gemini|cursor|grok|generic|all|auto|none")
    sp.add_argument("--force", action="store_true", help="re-initialize config/schema (keeps graph files)")
    sp.add_argument("--no-scan", action="store_true")
    sp.set_defaults(fn=cmd_init)

    sp = sub.add_parser("integrate", help="wire growmos into an agent CLI / git hooks / CI")
    sp.add_argument("target", help="claude|codex|gemini|cursor|grok|generic|file|hooks|ci|all")
    sp.add_argument("--file", help="instructions file to append the growmos block to (target=file)")
    sp.set_defaults(fn=cmd_integrate)

    sp = sub.add_parser("status", help="graph statistics + health signals")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_status)

    sp = sub.add_parser("context", help="compact brief for session start (inject into your agent)")
    sp.add_argument("--brief", action="store_true")
    sp.add_argument("--budget", type=int, default=1200, help="approx token budget")
    sp.set_defaults(fn=cmd_context)

    sp = sub.add_parser("scan", help="register include-globbed docs; changed ones become pending")
    sp.add_argument("--quiet", action="store_true")
    sp.set_defaults(fn=cmd_scan)

    sp = sub.add_parser("add", help="register files/URLs/stdin as sources")
    sp.add_argument("refs", nargs="+", help="paths, URLs, or - for stdin")
    sp.add_argument("--name", help="label for stdin sources")
    sp.set_defaults(fn=cmd_add)

    sp = sub.add_parser("next", help="print the next task packet (extraction → resolution → profile)")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--scan", action="store_true", help="scan for changed docs first")
    sp.set_defaults(fn=cmd_next)

    sp = sub.add_parser("apply", help="ingest a completed packet's JSON")
    sp.add_argument("kind", choices=["extraction", "resolution", "profile", "gold", "review"])
    sp.add_argument("file", help="JSON file or - for stdin")
    sp.add_argument("--source", help="source id/ref (extraction)")
    sp.add_argument("--chunk", type=int, help="chunk index (extraction)")
    sp.add_argument("--partial", action="store_true", help="more chunks follow; keep source pending")
    sp.add_argument("--type", help="entity type (resolution)")
    sp.add_argument("--entity", help="entity id or name (profile/review)")
    sp.add_argument("--reviewer", help="who reviewed (gold/review): agent (default) or human")
    sp.set_defaults(fn=cmd_apply)

    sp = sub.add_parser("extract", help="print the extraction packet for a source")
    sp.add_argument("source")
    sp.add_argument("--chunk", type=int, default=None)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_extract)

    sp = sub.add_parser("resolve", help="print resolution packet(s) for provisional entities")
    sp.add_argument("--type")
    sp.add_argument("--all", action="store_true", help="re-cluster all entities of the type(s)")
    sp.add_argument("--every", action="store_true", help="print every block, not just the first")
    sp.set_defaults(fn=cmd_resolve)

    sp = sub.add_parser("summarize", help="print profile packet(s) for hub entities")
    sp.add_argument("--entity")
    sp.add_argument("--limit", type=int, default=1)
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(fn=cmd_summarize)

    sp = sub.add_parser("query", help="serialize the k-hop subgraph around a question (grounded QA)")
    sp.add_argument("question")
    sp.add_argument("--seed", action="append", help="seed entity (repeatable)")
    sp.add_argument("--hops", type=int, default=2)
    sp.add_argument("--max-triples", type=int, default=300)
    sp.add_argument("--triples-only", action="store_true")
    sp.add_argument("--auto", action="store_true", help="answer via configured provider (headless)")
    sp.set_defaults(fn=cmd_query)

    sp = sub.add_parser("check", help="fact-check claims against the graph")
    sp.add_argument("text", nargs="?", default="-", help="claims text, `(A) --[p]--> (B)` lines, or - for stdin")
    sp.add_argument("--file")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_check)

    sp = sub.add_parser("remember", help="write a node directly (agent memo path)")
    sp.add_argument("name")
    sp.add_argument("--type", required=True)
    sp.add_argument("--desc", default="")
    sp.add_argument("--source", help="provenance label (default session:<date>)")
    sp.set_defaults(fn=cmd_remember)

    sp = sub.add_parser("link", help="write an edge directly: A predicate B")
    sp.add_argument("a")
    sp.add_argument("predicate")
    sp.add_argument("b")
    sp.add_argument("--type-a")
    sp.add_argument("--type-b")
    sp.add_argument("--source", help="provenance label")
    sp.add_argument("--start", help="temporal start (YYYY or YYYY-MM)")
    sp.add_argument("--end", help="temporal end")
    sp.set_defaults(fn=cmd_link)

    sp = sub.add_parser("journal", help="append to / read the shared journal")
    sp.add_argument("text", nargs="?", default=None)
    sp.add_argument("--author", default="agent")
    sp.add_argument("--tail", type=int, default=5)
    sp.set_defaults(fn=cmd_journal)

    sp = sub.add_parser("show", help="entity card: description, profile, edges, sources")
    sp.add_argument("name")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_show)

    sp = sub.add_parser("search", help="find entities by name/alias/description")
    sp.add_argument("text")
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(fn=cmd_search)

    sp = sub.add_parser("sample", help="random node for human review")
    sp.add_argument("--seed", type=int)
    sp.set_defaults(fn=cmd_sample)

    sp = sub.add_parser("eval", help="precision/recall/F1 against the gold set")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_eval)

    sp = sub.add_parser("gold-template", help="pre-fill a gold file from current extraction (then hand-correct)")
    sp.add_argument("source")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(fn=cmd_gold_template)

    sp = sub.add_parser("doctor", help="ten-item production readiness checklist")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--strict", action="store_true", help="exit 1 unless all green")
    sp.set_defaults(fn=cmd_doctor)

    sp = sub.add_parser("export", help="export graph: json|dot|mermaid|cypher|sql")
    sp.add_argument("--format", default="json")
    sp.add_argument("--out")
    sp.set_defaults(fn=cmd_export)

    sp = sub.add_parser("schema", help="show | bump | presets")
    sp.add_argument("action", choices=["show", "bump", "presets"])
    sp.add_argument("--note")
    sp.add_argument("--add-type", action="append")
    sp.add_argument("--add-predicate", action="append")
    sp.set_defaults(fn=cmd_schema)

    sp = sub.add_parser("prompts", help="list | reset prompt templates")
    sp.add_argument("action", choices=["list", "reset"])
    sp.set_defaults(fn=cmd_prompts)

    sp = sub.add_parser("compact", help="rewrite store files, prune dangling rows")
    sp.set_defaults(fn=cmd_compact)

    sp = sub.add_parser("ingest", help="headless: run all pending packets through an LLM provider")
    sp.add_argument("--limit", type=int, default=25)
    sp.add_argument("--scan", action="store_true")
    sp.set_defaults(fn=cmd_ingest)

    sp = sub.add_parser("mcp", help="run the MCP stdio server (tools for any MCP-capable agent CLI)")
    sp.set_defaults(fn=cmd_mcp)

    sp = sub.add_parser("hooks", help="alias: install git hooks")
    sp.set_defaults(fn=lambda a: cmd_integrate(argparse.Namespace(root=a.root, target="hooks", file="")))
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 0
    try:
        code = args.fn(args)
    except StoreError as e:
        print(f"growmos: {e}", file=sys.stderr)
        code = 1
    except KeyboardInterrupt:
        code = 130
    if argv is None:
        sys.exit(code)
    return code


if __name__ == "__main__":
    main()
