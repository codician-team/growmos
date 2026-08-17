"""The `.growmos/` store: git-friendly JSONL tables + state.

Layout (mirrors the playbook's three-table mapping, §IX.D):

    .growmos/
      config.json          growth settings (include globs, caps, provider)
      schema.json          versioned entity types + predicate hints (§XI.E "version the schema")
      state.json           the loop's state file: runs, pending work, last sample
      sources.jsonl        every document the organism has eaten (path/url, hash, status)
      mentions.jsonl       raw extraction output per source (never rewritten — provenance)
      entities.jsonl       canonical nodes
      aliases.jsonl        alias -> canonical entity (the alias map)
      relations.jsonl      typed, directed edges with provenance + corroboration
      profiles/*.json      synthesized entity profiles (summary, key facts, time range)
      prompts/*.md         editable prompt templates (tune -> rerun eval -> watch F1 move)
      eval/gold/*.json     hand-labelled gold sets;  eval/aliases.json scorer alias map
      journal.md           the shared memo: human+agent notes, append-only

Everything is deterministic and hand-editable. The CLI is a convenience; an agent
could maintain these files with a text editor.
"""

from __future__ import annotations

import fnmatch
import glob
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from . import schema as S
from .util import (
    append_jsonl, norm_name, norm_predicate, now_iso, read_json, read_jsonl, sha256_file,
    sha256_text, short_hash, slugify, today, write_json, write_jsonl,
)

DIRNAME = ".growmos"


class StoreError(Exception):
    pass


class Store:
    """In-memory view of `.growmos/` with explicit save()."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.dir = self.root / DIRNAME
        self.config: Dict[str, Any] = {}
        self.schema: Dict[str, Any] = {}
        self.state: Dict[str, Any] = {}
        self.sources: Dict[str, Dict[str, Any]] = {}
        self.entities: Dict[str, Dict[str, Any]] = {}
        self.aliases: Dict[Tuple[str, str], str] = {}   # (norm_alias, TYPE) -> entity id
        self.relations: Dict[str, Dict[str, Any]] = {}
        self._mentions_cache: Optional[List[Dict[str, Any]]] = None
        self._dirty: Set[str] = set()

    # ------------------------------------------------------------------ paths
    @property
    def exists(self) -> bool:
        return (self.dir / "schema.json").exists()

    def p(self, *parts: str) -> Path:
        return self.dir.joinpath(*parts)

    # ------------------------------------------------------------------ init
    @classmethod
    def init(cls, root: Path, preset_name: str = "software", force: bool = False) -> "Store":
        st = cls(root)
        if st.exists and not force:
            st.load()
            return st
        pr = S.preset(preset_name)
        st.dir.mkdir(parents=True, exist_ok=True)
        st.config = {
            "preset": preset_name,
            "include": list(pr["include"]),
            "exclude": [
                "node_modules/**", ".git/**", ".growmos/**", "dist/**", "build/**", "vendor/**",
                "**/CHANGELOG*.md", "**/LICENSE*", "**/*.lock", ".venv/**", "venv/**",
                # agent instruction files are protocol, not knowledge
                "AGENTS.md", "CLAUDE.md", "GEMINI.md", ".claude/**", ".cursor/**", ".codex/**", ".github/**",
            ],
            "max_docs_per_run": 25,
            "max_entities_per_doc": 40,
            "chunk_chars": 6000,
            "profile_min_degree": 3,
            "resolve_batch_size": 80,
            "gold_min": 2,
            "review_days": 7,
            "provider": {"name": "", "extract_model": "", "reason_model": ""},
        }
        st.schema = {
            "version": 1,
            "entity_types": list(pr["entity_types"]),
            "predicate_hints": list(pr["predicate_hints"]),
            "history": [{"version": 1, "ts": now_iso(), "note": f"init with preset '{preset_name}'"}],
        }
        st.state = {
            "created": now_iso(),
            "runs": [],
            "pending_resummarize": [],
            "last_sample": None,
            "last_eval": None,
            "counters": {"extractions": 0, "resolutions": 0, "profiles": 0, "queries": 0},
        }
        st._dirty |= {"config", "schema", "state"}
        for name in ("sources", "entities", "aliases", "relations", "mentions"):
            path = st.p(f"{name}.jsonl")
            if not path.exists():
                path.write_text("", encoding="utf-8")
        st.p("profiles").mkdir(exist_ok=True)
        st.p("eval", "gold").mkdir(parents=True, exist_ok=True)
        if not st.p("eval", "aliases.json").exists():
            write_json(st.p("eval", "aliases.json"), {})
        st.p("prompts").mkdir(exist_ok=True)
        from .prompts import install_default_prompts
        install_default_prompts(st.p("prompts"))
        if not st.p("journal.md").exists():
            st.p("journal.md").write_text(
                "# growmos journal\n\nShared memo between humans and agents. Append-only; newest at the bottom.\n\n",
                encoding="utf-8",
            )
        gi = st.p(".gitignore")
        if not gi.exists():
            gi.write_text("*.tmp\ncache/\n", encoding="utf-8")
        st.save()
        return st

    # ------------------------------------------------------------------ load/save
    def load(self) -> "Store":
        if not self.exists:
            raise StoreError(f"no {DIRNAME}/ found under {self.root}. Run `growmos init` first.")
        self.config = read_json(self.p("config.json"), {}) or {}
        self.schema = read_json(self.p("schema.json"), {}) or {}
        self.state = read_json(self.p("state.json"), {}) or {}
        self.state.setdefault("runs", [])
        self.state.setdefault("pending_resummarize", [])
        self.state.setdefault("counters", {})
        self.sources = {r["id"]: r for r in read_jsonl(self.p("sources.jsonl")) if "id" in r}
        self.entities = {r["id"]: r for r in read_jsonl(self.p("entities.jsonl")) if "id" in r}
        self.aliases = {}
        for r in read_jsonl(self.p("aliases.jsonl")):
            if "alias" in r and "entity" in r:
                self.aliases[(norm_name(r["alias"]), r.get("type", ""))] = r["entity"]
        self.relations = {r["id"]: r for r in read_jsonl(self.p("relations.jsonl")) if "id" in r}
        return self

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        write_json(self.p("config.json"), self.config)
        write_json(self.p("schema.json"), self.schema)
        write_json(self.p("state.json"), self.state)
        write_jsonl(self.p("sources.jsonl"), [self.sources[k] for k in sorted(self.sources)])
        write_jsonl(self.p("entities.jsonl"), [self.entities[k] for k in sorted(self.entities)])
        alias_rows = []
        for (alias, etype), eid in self.aliases.items():
            alias_rows.append({"alias": self._alias_display.get((alias, etype), alias), "type": etype, "entity": eid})
        alias_rows.sort(key=lambda r: (r["entity"], r["type"], r["alias"].lower()))
        write_jsonl(self.p("aliases.jsonl"), alias_rows)
        write_jsonl(self.p("relations.jsonl"), [self.relations[k] for k in sorted(self.relations)])
        self._dirty.clear()

    # alias display names (original casing) — rebuilt lazily
    @property
    def _alias_display(self) -> Dict[Tuple[str, str], str]:
        if not hasattr(self, "_alias_display_cache"):
            cache: Dict[Tuple[str, str], str] = {}
            for r in read_jsonl(self.p("aliases.jsonl")):
                if "alias" in r:
                    cache[(norm_name(r["alias"]), r.get("type", ""))] = r["alias"]
            self._alias_display_cache = cache  # type: ignore[attr-defined]
        return self._alias_display_cache  # type: ignore[attr-defined]

    def _set_alias(self, alias: str, etype: str, eid: str) -> None:
        key = (norm_name(alias), etype)
        self.aliases[key] = eid
        self._alias_display[key] = alias.strip()

    # ------------------------------------------------------------------ schema
    @property
    def entity_types(self) -> List[str]:
        return list(self.schema.get("entity_types") or S.BASE_ENTITY_TYPES)

    @property
    def schema_version(self) -> int:
        return int(self.schema.get("version", 1))

    def bump_schema(self, note: str, add_types: Iterable[str] = (), add_predicates: Iterable[str] = ()) -> int:
        v = self.schema_version + 1
        types = self.entity_types
        for t in add_types:
            t = t.strip().upper()
            if t and t not in types:
                types.append(t)
        preds = list(self.schema.get("predicate_hints") or [])
        for p in add_predicates:
            p = p.strip()
            if p and p not in preds:
                preds.append(p)
        self.schema.update({"version": v, "entity_types": types, "predicate_hints": preds})
        self.schema.setdefault("history", []).append({"version": v, "ts": now_iso(), "note": note})
        return v

    # ------------------------------------------------------------------ sources
    def _source_id(self, ref: str) -> str:
        return "src_" + short_hash(ref, 12)

    def iter_candidate_files(self) -> List[Path]:
        inc = self.config.get("include") or ["*.md"]
        exc = self.config.get("exclude") or []
        found: Set[Path] = set()
        for pat in inc:
            for m in glob.glob(str(self.root / pat), recursive=True):
                p = Path(m)
                if p.is_file():
                    found.add(p)
        out = []
        for p in sorted(found):
            rel = p.relative_to(self.root).as_posix()
            if any(fnmatch.fnmatch(rel, e) or fnmatch.fnmatch("./" + rel, e) for e in exc):
                continue
            if rel.startswith(DIRNAME + "/"):
                continue
            out.append(p)
        return out

    def register_source(self, ref: str, text: Optional[str] = None, title: Optional[str] = None,
                        kind: str = "file") -> Tuple[Dict[str, Any], bool]:
        """Register (or refresh) a source. Returns (record, changed).

        `ref` is a repo-relative path, a URL, or a logical id (e.g. "session:2026-08-17").
        A changed content hash flips status back to 'pending' — this is the incremental
        update rule: extract only what changed (§IX.C).
        """
        if kind == "file":
            ref = ref.replace("\\", "/")
        sid = self._source_id(ref)
        if text is None and kind == "file":
            path = self.root / ref
            if not path.exists():
                raise StoreError(f"source file not found: {ref}")
            digest = sha256_file(path)
        else:
            digest = sha256_text(text or "")
        rec = self.sources.get(sid)
        if rec and rec.get("sha256") == digest and rec.get("status") != "pending":
            return rec, False
        changed = True
        if rec is None:
            rec = {"id": sid, "ref": ref, "kind": kind, "added": now_iso()}
        rec.update({
            "sha256": digest,
            "title": title or rec.get("title") or Path(ref).name,
            "status": "pending",
            "updated": now_iso(),
        })
        if text is not None and kind != "file":
            # Non-file sources keep their text so packets can be rebuilt later.
            self.p("cache").mkdir(exist_ok=True)
            (self.p("cache") / f"{sid}.txt").write_text(text, encoding="utf-8")
        self.sources[sid] = rec
        return rec, changed

    def source_text(self, sid: str) -> str:
        rec = self.sources.get(sid)
        if not rec:
            raise StoreError(f"unknown source {sid}")
        if rec.get("kind", "file") == "file":
            path = self.root / rec["ref"]
            if not path.exists():
                raise StoreError(f"source file missing on disk: {rec['ref']}")
            return path.read_text(encoding="utf-8", errors="replace")
        cached = self.p("cache") / f"{sid}.txt"
        if cached.exists():
            return cached.read_text(encoding="utf-8")
        raise StoreError(f"no cached text for non-file source {sid}")

    def scan(self) -> Dict[str, List[str]]:
        """Register all include-globbed files. Returns {'new': [...], 'changed': [...], 'unchanged': n}."""
        report: Dict[str, List[str]] = {"new": [], "changed": [], "missing": []}
        seen: Set[str] = set()
        for path in self.iter_candidate_files():
            rel = path.relative_to(self.root).as_posix()
            sid = self._source_id(rel)
            existed = sid in self.sources
            seen.add(sid)
            _, changed = self.register_source(rel)
            if changed:
                (report["changed"] if existed else report["new"]).append(rel)
        for sid, rec in self.sources.items():
            if rec.get("kind", "file") == "file" and sid not in seen and not (self.root / rec["ref"]).exists():
                if rec.get("status") != "missing":
                    rec["status"] = "missing"
                    report["missing"].append(rec["ref"])
        return report

    def pending_sources(self) -> List[Dict[str, Any]]:
        return sorted((r for r in self.sources.values() if r.get("status") == "pending"), key=lambda r: r["ref"])

    # ------------------------------------------------------------------ mentions
    def mentions(self) -> List[Dict[str, Any]]:
        if self._mentions_cache is None:
            self._mentions_cache = read_jsonl(self.p("mentions.jsonl"))
        return self._mentions_cache

    # ------------------------------------------------------------------ entities
    def _new_entity_id(self, name: str, etype: str) -> str:
        base = f"{slugify(etype)}/{slugify(name)}"
        eid = base
        n = 2
        while eid in self.entities:
            eid = f"{base}-{n}"
            n += 1
        return eid

    def resolve_name(self, name: str, etype: Optional[str] = None) -> Optional[str]:
        """Exact-match resolution against the alias map (case/whitespace-insensitive)."""
        key = norm_name(name)
        if etype:
            eid = self.aliases.get((key, etype.upper()))
            if eid:
                return eid
        # Type-agnostic fallback (agent-written facts often omit types).
        hits = {eid for (a, t), eid in self.aliases.items() if a == key}
        if len(hits) == 1:
            return hits.pop()
        if etype is None and hits:
            # ambiguous across types: prefer the highest-degree entity
            return max(hits, key=lambda e: self.entities.get(e, {}).get("mentions", 0))
        return None

    def get_or_create_entity(self, name: str, etype: str, description: str = "",
                             source_id: Optional[str] = None, provisional: bool = True) -> Tuple[str, bool]:
        etype = etype.upper()
        eid = self.resolve_name(name, etype)
        created = False
        if eid is None:
            eid = self._new_entity_id(name, etype)
            self.entities[eid] = {
                "id": eid, "name": name.strip(), "type": etype, "description": description.strip(),
                "sources": [], "mentions": 0, "provisional": provisional,
                "created": now_iso(), "updated": now_iso(), "schema_version": self.schema_version,
            }
            self._set_alias(name, etype, eid)
            created = True
        ent = self.entities[eid]
        if description and (not ent.get("description") or len(description) > len(ent.get("description", "")) + 40):
            ent["description"] = description.strip()
        if source_id and source_id not in ent["sources"]:
            ent["sources"].append(source_id)
            self._invalidate_profile(eid)
        ent["updated"] = now_iso()
        return eid, created

    def _invalidate_profile(self, eid: str) -> None:
        pend = self.state.setdefault("pending_resummarize", [])
        if eid not in pend and (self.p("profiles") / self._profile_file(eid)).exists():
            pend.append(eid)

    def _profile_file(self, eid: str) -> str:
        return eid.replace("/", "__") + ".json"

    def profile_path(self, eid: str) -> Path:
        return self.p("profiles") / self._profile_file(eid)

    def get_profile(self, eid: str) -> Optional[Dict[str, Any]]:
        return read_json(self.profile_path(eid))

    def set_profile(self, eid: str, profile: Dict[str, Any]) -> None:
        ent = self.entities[eid]
        profile = dict(profile)
        profile["entity"] = eid
        profile["sources_hash"] = sha256_text(",".join(sorted(ent.get("sources", []))))[:12]
        profile["ts"] = now_iso()
        write_json(self.profile_path(eid), profile)
        pend = self.state.setdefault("pending_resummarize", [])
        if eid in pend:
            pend.remove(eid)
        self.state["counters"]["profiles"] = self.state["counters"].get("profiles", 0) + 1

    def profile_is_stale(self, eid: str) -> bool:
        prof = self.get_profile(eid)
        if not prof:
            return True
        ent = self.entities.get(eid, {})
        return prof.get("sources_hash") != sha256_text(",".join(sorted(ent.get("sources", []))))[:12]

    # ------------------------------------------------------------------ relations
    def _relation_id(self, s: str, p: str, t: str) -> str:
        return "r_" + short_hash(f"{s}|{norm_predicate(p)}|{t}", 12)

    def add_relation(self, source_eid: str, predicate: str, target_eid: str,
                     source_id: Optional[str] = None, when: Optional[Dict[str, str]] = None) -> Tuple[str, bool]:
        if source_eid not in self.entities or target_eid not in self.entities:
            raise StoreError("relation endpoints must be existing entities")
        rid = self._relation_id(source_eid, predicate, target_eid)
        rec = self.relations.get(rid)
        created = False
        if rec is None:
            rec = {
                "id": rid, "source": source_eid, "predicate": predicate.strip(), "target": target_eid,
                "sources": [], "created": now_iso(), "schema_version": self.schema_version,
            }
            self.relations[rid] = rec
            created = True
        if source_id and source_id not in rec["sources"]:
            rec["sources"].append(source_id)
        rec["confidence"] = max(1, len(rec["sources"]))  # cross-document corroboration (§XI.F)
        if when:
            rec["when"] = when
        rec["updated"] = now_iso()
        return rid, created

    # ------------------------------------------------------------------ extraction apply
    def apply_extraction(self, source_id: str, payload: Dict[str, Any], chunk: Optional[int] = None,
                         final: bool = True) -> Dict[str, Any]:
        """Ingest one ExtractedGraph for a source.

        Steps: validate -> record raw mentions (provenance) -> resolve each name against the
        canonical set by exact match (§IX.C: resolve against existing, not against each other)
        -> unmatched names become *provisional* single-element clusters (never lost) ->
        add edges. Returns a small report.
        """
        if source_id not in self.sources:
            raise StoreError(f"unknown source {source_id}; register it first (growmos add / scan)")
        cleaned, problems = S.validate_extraction(payload, self.entity_types)
        cap = int(self.config.get("max_entities_per_doc", 40))
        if len(cleaned["entities"]) > cap:
            problems.append(f"entity cap {cap} applied ({len(cleaned['entities'])} extracted)")
            keep = {e["name"].lower() for e in cleaned["entities"][:cap]}
            cleaned["entities"] = cleaned["entities"][:cap]
            cleaned["relations"] = [r for r in cleaned["relations"]
                                    if r["source"].lower() in keep and r["target"].lower() in keep]
        ts = now_iso()
        name_to_eid: Dict[str, str] = {}
        created_ents = 0
        for e in cleaned["entities"]:
            append_jsonl(self.p("mentions.jsonl"), {
                "source": source_id, "chunk": chunk, "name": e["name"], "type": e["type"],
                "description": e["description"], "ts": ts, "schema_version": self.schema_version,
            })
            eid, created = self.get_or_create_entity(e["name"], e["type"], e["description"], source_id)
            self.entities[eid]["mentions"] = self.entities[eid].get("mentions", 0) + 1
            created_ents += int(created)
            name_to_eid[e["name"].lower()] = eid
        self._mentions_cache = None
        created_rels = 0
        for r in cleaned["relations"]:
            s = name_to_eid.get(r["source"].lower())
            t = name_to_eid.get(r["target"].lower())
            if not s or not t or s == t:
                continue
            _, created = self.add_relation(s, r["predicate"], t, source_id)
            created_rels += int(created)
        rec = self.sources[source_id]
        if final:
            rec["status"] = "extracted"
            rec["extracted_at"] = ts
            rec["schema_version"] = self.schema_version
        rec["stats"] = {"entities": len(cleaned["entities"]), "relations": len(cleaned["relations"])}
        self.state["counters"]["extractions"] = self.state["counters"].get("extractions", 0) + 1
        return {
            "source": source_id, "entities": len(cleaned["entities"]), "relations": len(cleaned["relations"]),
            "new_entities": created_ents, "new_relations": created_rels, "problems": problems,
        }

    # ------------------------------------------------------------------ resolution
    def provisional_entities(self, etype: Optional[str] = None) -> List[Dict[str, Any]]:
        return [e for e in self.entities.values()
                if e.get("provisional") and (etype is None or e["type"] == etype)]

    def merge(self, from_id: str, into_id: str) -> None:
        """Fold `from_id` into `into_id`: aliases, sources, mentions, relations, profile."""
        if from_id == into_id:
            return
        if from_id not in self.entities or into_id not in self.entities:
            raise StoreError("merge: unknown entity id")
        src, dst = self.entities[from_id], self.entities[into_id]
        for sid in src.get("sources", []):
            if sid not in dst["sources"]:
                dst["sources"].append(sid)
        dst["mentions"] = dst.get("mentions", 0) + src.get("mentions", 0)
        if len(src.get("description", "")) > len(dst.get("description", "")):
            dst["description"] = src["description"]
        for key, eid in list(self.aliases.items()):
            if eid == from_id:
                self.aliases[key] = into_id
        self._set_alias(src["name"], dst["type"], into_id)
        # rewrite relations
        for rid, rel in list(self.relations.items()):
            if rel["source"] == from_id or rel["target"] == from_id:
                s = into_id if rel["source"] == from_id else rel["source"]
                t = into_id if rel["target"] == from_id else rel["target"]
                del self.relations[rid]
                if s == t:
                    continue
                new_id, _ = self.add_relation(s, rel["predicate"], t)
                new = self.relations[new_id]
                for sid in rel.get("sources", []):
                    if sid not in new["sources"]:
                        new["sources"].append(sid)
                new["confidence"] = max(1, len(new["sources"]))
                if rel.get("when") and not new.get("when"):
                    new["when"] = rel["when"]
        pf = self.profile_path(from_id)
        if pf.exists():
            pf.unlink()
        pend = self.state.setdefault("pending_resummarize", [])
        if from_id in pend:
            pend.remove(from_id)
        self._invalidate_profile(into_id)
        del self.entities[from_id]
        dst["updated"] = now_iso()

    def rename(self, eid: str, new_name: str) -> None:
        ent = self.entities[eid]
        self._set_alias(new_name, ent["type"], eid)
        ent["name"] = new_name.strip()
        ent["updated"] = now_iso()

    def apply_resolution(self, etype: str, clusters: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Apply validated clusters for one entity type. Names are aliases already in the map."""
        etype = etype.upper()
        merged = 0
        renamed = 0
        for c in clusters:
            eids: List[str] = []
            for a in c["aliases"]:
                eid = self.aliases.get((norm_name(a), etype))
                if eid and eid in self.entities and eid not in eids:
                    eids.append(eid)
            if not eids:
                continue
            canon_eid = self.aliases.get((norm_name(c["canonical"]), etype))
            if canon_eid not in eids:
                # survivor: most mentioned, then oldest
                canon_eid = sorted(eids, key=lambda e: (-self.entities[e].get("mentions", 0),
                                                        self.entities[e].get("created", "")))[0]
            for other in eids:
                if other != canon_eid:
                    self.merge(other, canon_eid)
                    merged += 1
            ent = self.entities[canon_eid]
            if norm_name(ent["name"]) != norm_name(c["canonical"]) and c["canonical"].strip():
                self.rename(canon_eid, c["canonical"])
                renamed += 1
            ent["provisional"] = False
        self.state["counters"]["resolutions"] = self.state["counters"].get("resolutions", 0) + 1
        return {"type": etype, "clusters": len(clusters), "merged": merged, "renamed": renamed}

    # ------------------------------------------------------------------ direct writes (agents)
    def remember(self, name: str, etype: str, description: str, source_ref: str) -> Tuple[str, bool]:
        """Agent/human writes a node directly (the 'shared memo' path). Provenance = source_ref."""
        rec, _ = self.register_source(source_ref, text=source_ref, kind="note", title=source_ref)
        rec["status"] = "note"
        eid, created = self.get_or_create_entity(name, etype, description, rec["id"], provisional=True)
        self.entities[eid]["mentions"] = self.entities[eid].get("mentions", 0) + 1
        append_jsonl(self.p("mentions.jsonl"), {
            "source": rec["id"], "chunk": None, "name": name, "type": etype.upper(),
            "description": description, "ts": now_iso(), "schema_version": self.schema_version,
        })
        self._mentions_cache = None
        return eid, created

    def link(self, source: str, predicate: str, target: str, source_ref: str,
             types: Tuple[Optional[str], Optional[str]] = (None, None),
             when: Optional[Dict[str, str]] = None) -> Tuple[str, bool]:
        rec, _ = self.register_source(source_ref, text=source_ref, kind="note", title=source_ref)
        rec["status"] = "note"
        s = self.resolve_name(source, types[0])
        t = self.resolve_name(target, types[1])
        if s is None:
            s, _ = self.get_or_create_entity(source, types[0] or self.default_type(), "", rec["id"])
        if t is None:
            t, _ = self.get_or_create_entity(target, types[1] or self.default_type(), "", rec["id"])
        return self.add_relation(s, predicate, t, rec["id"], when=when)

    def default_type(self) -> str:
        return "CONCEPT" if "CONCEPT" in self.entity_types else "ARTIFACT"

    def journal(self, text: str, author: str = "agent") -> None:
        entry = f"\n### {now_iso()} · {author}\n\n{text.strip()}\n"
        with open(self.p("journal.md"), "a", encoding="utf-8") as f:
            f.write(entry)

    def journal_tail(self, n: int = 3) -> List[str]:
        path = self.p("journal.md")
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8")
        parts = [p.strip() for p in re.split(r"\n(?=### )", text) if p.strip().startswith("### ")]
        return parts[-n:]

    # ------------------------------------------------------------------ runs
    def record_run(self, kind: str, detail: Dict[str, Any]) -> None:
        runs = self.state.setdefault("runs", [])
        runs.append({"ts": now_iso(), "kind": kind, **detail})
        if len(runs) > 200:
            del runs[: len(runs) - 200]
        self.state["last_run"] = now_iso()

    # ------------------------------------------------------------------ misc
    def source_label(self, sid: str) -> str:
        rec = self.sources.get(sid)
        return rec["ref"] if rec else sid

    def entity_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        eid = self.resolve_name(name)
        if eid is None:
            # allow id
            return self.entities.get(name)
        return self.entities.get(eid)

    def compact(self) -> Dict[str, int]:
        """Rewrite JSONL files sorted/deduped and prune dangling relations/aliases."""
        removed_rel = 0
        for rid, rel in list(self.relations.items()):
            if rel["source"] not in self.entities or rel["target"] not in self.entities:
                del self.relations[rid]
                removed_rel += 1
        removed_alias = 0
        for key, eid in list(self.aliases.items()):
            if eid not in self.entities:
                del self.aliases[key]
                removed_alias += 1
        # dedupe mentions file
        rows = self.mentions()
        seen = set()
        uniq = []
        for r in rows:
            k = json.dumps(r, sort_keys=True)
            if k in seen:
                continue
            seen.add(k)
            uniq.append(r)
        write_jsonl(self.p("mentions.jsonl"), uniq)
        self._mentions_cache = None
        return {"relations_removed": removed_rel, "aliases_removed": removed_alias, "mentions": len(uniq)}
