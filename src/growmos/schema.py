"""Schema presets, structured-output JSON schemas, and validators.

The schema *is* the training data (playbook §III): every judgment stage produces JSON
that must validate against one of the shapes below. Presets extend the five base
entity types with domain-specific ones — same prompt, extended vocabulary (Appendix B).
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

BASE_ENTITY_TYPES = ["PERSON", "ORGANIZATION", "LOCATION", "EVENT", "ARTIFACT"]

PRESETS: Dict[str, Dict[str, Any]] = {
    "general": {
        "description": "The playbook's five canonical types. Works on any prose corpus.",
        "entity_types": BASE_ENTITY_TYPES,
        "predicate_hints": ["part of", "located in", "commanded", "launched from", "founded", "works at"],
        "include": ["README.md", "docs/**/*.md", "*.md"],
    },
    "software": {
        "description": "For code repositories: architecture, decisions, people, tools and concepts.",
        "entity_types": BASE_ENTITY_TYPES
        + ["COMPONENT", "SERVICE", "DECISION", "CONCEPT", "TOOL", "DEPENDENCY", "ISSUE", "FEATURE"],
        "predicate_hints": [
            "depends on", "calls", "implements", "owned by", "decided", "replaces", "deployed to",
            "configured by", "tested by", "documented in", "part of", "introduced in", "fixes",
        ],
        "include": [
            "README.md", "CONTRIBUTING.md", "ARCHITECTURE.md", "CHANGELOG.md",
            "docs/**/*.md", "adr/**/*.md", "docs/adr/**/*.md", "design/**/*.md", "rfcs/**/*.md",
            "*.md",
        ],
    },
    "research": {
        "description": "Papers, notes, literature reviews.",
        "entity_types": BASE_ENTITY_TYPES + ["PAPER", "METHOD", "DATASET", "CONCEPT", "METRIC", "CLAIM"],
        "predicate_hints": ["proposes", "evaluated on", "outperforms", "cites", "extends", "authored by", "measures"],
        "include": ["notes/**/*.md", "papers/**/*.md", "*.md"],
    },
    "business": {
        "description": "Competitive intelligence, customers, products, pricing (Appendix B worked example).",
        "entity_types": BASE_ENTITY_TYPES + ["PRODUCT", "FEATURE", "PRICING", "FILING", "MARKET", "COMPETITOR"],
        "predicate_hints": ["priced at", "filed", "acquired", "competes with", "launched", "reported", "targets"],
        "include": ["docs/**/*.md", "reports/**/*.md", "*.md"],
    },
}


def preset(name: str) -> Dict[str, Any]:
    if name not in PRESETS:
        raise KeyError(f"unknown preset '{name}'. Available: {', '.join(PRESETS)}")
    return PRESETS[name]


# ---------------------------------------------------------------------------
# Structured-output JSON schemas (strict: additionalProperties=false, all required)
# ---------------------------------------------------------------------------

def extraction_schema(entity_types: List[str]) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "type": {"type": "string", "enum": list(entity_types)},
                        "description": {"type": "string"},
                    },
                    "required": ["name", "type", "description"],
                    "additionalProperties": False,
                },
            },
            "relations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "predicate": {"type": "string"},
                        "target": {"type": "string"},
                    },
                    "required": ["source", "predicate", "target"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["entities", "relations"],
        "additionalProperties": False,
    }


RESOLUTION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "clusters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "canonical": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["canonical", "aliases"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clusters"],
    "additionalProperties": False,
}

PROFILE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "key_facts": {"type": "array", "items": {"type": "string"}},
        "time_range": {
            "type": "object",
            "properties": {"start": {"type": "string"}, "end": {"type": "string"}},
            "required": ["start", "end"],
            "additionalProperties": False,
        },
    },
    "required": ["summary", "key_facts", "time_range"],
    "additionalProperties": False,
}

GOLD_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "entities": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "type": {"type": "string"}},
            "required": ["name", "type"], "additionalProperties": False}},
        "relations": {"type": "array", "items": {"type": "object", "properties": {
            "source": {"type": "string"}, "target": {"type": "string"}},
            "required": ["source", "target"], "additionalProperties": False}},
    },
    "required": ["entities", "relations"],
    "additionalProperties": False,
}

REVIEW_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
        "fixes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["ok", "issues", "fixes"],
    "additionalProperties": False,
}

ANSWER_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "cited_edges": {"type": "array", "items": {"type": "string"}},
        "not_in_graph": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["answer", "cited_edges", "not_in_graph"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Validators (deterministic, explain every failure)
# ---------------------------------------------------------------------------

def _is_str(x: Any) -> bool:
    return isinstance(x, str)


def validate_extraction(data: Any, entity_types: List[str]) -> Tuple[Dict[str, Any], List[str]]:
    """Validate an ExtractedGraph payload. Returns (cleaned, problems).

    Enforces playbook guideline 4: every relation must connect two extracted entities.
    Relations that reference unknown names are dropped and reported (never silently kept as
    dangling edges).
    """
    problems: List[str] = []
    if not isinstance(data, dict):
        return {"entities": [], "relations": []}, ["payload must be a JSON object"]
    ents_in = data.get("entities")
    rels_in = data.get("relations")
    if not isinstance(ents_in, list):
        problems.append("'entities' must be a list")
        ents_in = []
    if not isinstance(rels_in, list):
        problems.append("'relations' must be a list")
        rels_in = []

    allowed = set(entity_types)
    ents: List[Dict[str, Any]] = []
    seen = set()
    for i, e in enumerate(ents_in):
        if not isinstance(e, dict) or not _is_str(e.get("name")) or not e.get("name", "").strip():
            problems.append(f"entities[{i}]: missing name")
            continue
        etype = str(e.get("type", "")).strip().upper()
        if etype not in allowed:
            problems.append(f"entities[{i}] '{e['name']}': type '{etype}' not in schema; kept as ARTIFACT")
            etype = "ARTIFACT" if "ARTIFACT" in allowed else sorted(allowed)[0]
        desc = e.get("description") if _is_str(e.get("description")) else ""
        key = (e["name"].strip().lower(), etype)
        if key in seen:
            continue
        seen.add(key)
        ents.append({"name": e["name"].strip(), "type": etype, "description": desc.strip()})

    names = {e["name"].strip().lower() for e in ents}
    rels: List[Dict[str, Any]] = []
    for i, r in enumerate(rels_in):
        if not isinstance(r, dict):
            problems.append(f"relations[{i}]: not an object")
            continue
        s, p, t = r.get("source"), r.get("predicate"), r.get("target")
        if not (_is_str(s) and _is_str(p) and _is_str(t)) or not s.strip() or not t.strip() or not p.strip():
            problems.append(f"relations[{i}]: needs source/predicate/target strings")
            continue
        if s.strip().lower() not in names or t.strip().lower() not in names:
            problems.append(
                f"relations[{i}] ({s}) --[{p}]--> ({t}): endpoint not among extracted entities; dropped"
            )
            continue
        rels.append({"source": s.strip(), "predicate": p.strip(), "target": t.strip()})
    return {"entities": ents, "relations": rels}, problems


def validate_resolution(data: Any, input_names: List[str]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Validate ResolvedClusters. Every input name must land in exactly one cluster.

    Names left out get a single-element fallback cluster (playbook §IV.B, failure mode 1).
    Names appearing twice keep their first assignment (over-merge guard is left to the LLM;
    we only enforce structural constraints).
    """
    problems: List[str] = []
    if not isinstance(data, dict) or not isinstance(data.get("clusters"), list):
        return [{"canonical": n, "aliases": [n]} for n in input_names], ["payload must be {clusters: [...]}"]
    wanted = {n.strip().lower(): n for n in input_names}
    assigned: Dict[str, int] = {}
    clusters: List[Dict[str, Any]] = []
    for ci, c in enumerate(data["clusters"]):
        if not isinstance(c, dict) or not _is_str(c.get("canonical")):
            problems.append(f"clusters[{ci}]: missing canonical")
            continue
        aliases_raw = c.get("aliases") if isinstance(c.get("aliases"), list) else []
        aliases: List[str] = []
        for a in aliases_raw:
            if not _is_str(a):
                continue
            k = a.strip().lower()
            if k in assigned:
                problems.append(f"'{a}' appears in more than one cluster; kept first")
                continue
            if k not in wanted:
                problems.append(f"'{a}' was not in the input list; ignored")
                continue
            assigned[k] = len(clusters)
            aliases.append(wanted[k])
        canon = c["canonical"].strip()
        if canon.lower() not in {x.lower() for x in aliases}:
            # canonical must be a member; if the model invented a canonical, keep it as the
            # display name but ensure membership so nothing is lost.
            if canon.lower() in wanted and canon.lower() not in assigned:
                assigned[canon.lower()] = len(clusters)
                aliases.append(wanted[canon.lower()])
        if not aliases:
            continue
        clusters.append({"canonical": canon or aliases[0], "aliases": aliases})
    for k, original in wanted.items():
        if k not in assigned:
            problems.append(f"'{original}' missing from every cluster; single-element fallback applied")
            clusters.append({"canonical": original, "aliases": [original]})
    return clusters, problems


def validate_profile(data: Any) -> Tuple[Dict[str, Any], List[str]]:
    problems: List[str] = []
    if not isinstance(data, dict):
        return {"summary": "", "key_facts": [], "time_range": {"start": "unknown", "end": "unknown"}}, [
            "payload must be an object"
        ]
    summary = data.get("summary") if _is_str(data.get("summary")) else ""
    if not summary:
        problems.append("summary missing")
    facts = [f for f in (data.get("key_facts") or []) if _is_str(f) and f.strip()]
    tr = data.get("time_range") if isinstance(data.get("time_range"), dict) else {}
    start = tr.get("start") if _is_str(tr.get("start")) else "unknown"
    end = tr.get("end") if _is_str(tr.get("end")) else "unknown"
    return {"summary": summary.strip(), "key_facts": facts, "time_range": {"start": start, "end": end}}, problems
