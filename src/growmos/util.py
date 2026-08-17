"""Small, dependency-free helpers shared across growmos."""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def slugify(text: str, max_len: int = 64) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if not text:
        text = "unnamed"
    return text[:max_len].rstrip("-")


def norm_name(name: str) -> str:
    """Conservative surface-form normalization used for exact-match resolution.

    Deliberately weak: the hard cases ("Edwin Aldrin" vs "Buzz Aldrin") are left to the
    LLM resolver, exactly as the playbook prescribes.
    """
    n = unicodedata.normalize("NFKC", name or "").strip()
    n = re.sub(r"\s+", " ", n)
    n = n.rstrip(".").strip()
    return n.lower()


def norm_predicate(pred: str) -> str:
    p = re.sub(r"\s+", " ", (pred or "").strip().lower())
    p = p.replace("_", " ")
    return p


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def short_hash(text: str, n: int = 10) -> str:
    return sha256_text(text)[:n]


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                # Tolerate a corrupt line (e.g. merge-conflict residue) rather than losing the graph.
                continue
    return rows


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp, path)


def append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def approx_tokens(text: str) -> int:
    """Rough token estimate (chars/4). Good enough for context budgeting."""
    return max(1, len(text) // 4)


def tokens_of(name: str) -> List[str]:
    return [t for t in re.split(r"[^a-z0-9]+", norm_name(name)) if len(t) > 2]


def find_repo_root(start: Optional[Path] = None) -> Path:
    """Walk up until we find `.growmos/` or `.git/`; fall back to cwd."""
    cur = (start or Path.cwd()).resolve()
    for p in [cur, *cur.parents]:
        if (p / ".growmos").is_dir():
            return p
        if (p / ".git").exists():
            return p
    return cur


def relpath(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def truncate(text: str, n: int) -> str:
    text = text or ""
    return text if len(text) <= n else text[: n - 1] + "…"


def parse_bool(v: Any) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}
