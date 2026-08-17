"""Plug & play: wire growmos into whatever agent CLI the user runs.

Supported targets (all idempotent, block-marker based, never clobber user content):

  claude   CLAUDE.md block + .claude/skills/growmos/SKILL.md + SessionStart/Stop hooks + .mcp.json
  codex    AGENTS.md block (Codex CLI reads AGENTS.md)
  gemini   GEMINI.md block
  cursor   .cursor/rules/growmos.mdc (alwaysApply)
  grok     AGENTS.md block (+ .mcp.json when the CLI supports MCP)
  generic  AGENTS.md block — the de-facto cross-tool convention
  file     append the block to any instructions file you name (--file PATH)
  hooks    git post-commit / post-merge hooks that queue changed docs
  ci       .github/workflows/growmos.yml
"""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Dict, List

from .util import read_json, write_json

TPL = Path(__file__).parent / "templates" / "integrations"
START = "<!-- growmos:start"
END = "<!-- growmos:end -->"

TARGETS = ["claude", "codex", "gemini", "cursor", "grok", "generic", "mcp", "hooks", "ci", "all"]


def _block() -> str:
    return (TPL / "agents-block.md").read_text(encoding="utf-8").strip() + "\n"


def upsert_block(path: Path, block: str) -> str:
    """Insert or replace the growmos block in a markdown instructions file. Returns action."""
    if path.exists():
        text = path.read_text(encoding="utf-8")
        if START in text and END in text:
            new = re.sub(re.escape(START) + r".*?" + re.escape(END) + r"\n?", block, text, flags=re.S)
            if new == text:
                return "unchanged"
            path.write_text(new, encoding="utf-8")
            return "updated"
        sep = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        path.write_text(text + sep + block, encoding="utf-8")
        return "appended"
    path.parent.mkdir(parents=True, exist_ok=True)
    title = f"# {path.stem}\n\n" if path.suffix == ".md" else ""
    path.write_text(title + block, encoding="utf-8")
    return "created"


def _merge_hooks(settings: Dict[str, Any]) -> bool:
    """Add SessionStart (context brief) and Stop (scan) hooks if absent. Returns changed."""
    hooks = settings.setdefault("hooks", {})
    changed = False
    wanted = {
        "SessionStart": "growmos hook session-start 2>/dev/null || true",
        "Stop": "growmos hook stop 2>/dev/null || true",
    }
    for event, cmd in wanted.items():
        arr = hooks.setdefault(event, [])
        present = False
        for entry in arr:
            if not isinstance(entry, dict):
                continue
            for h in entry.get("hooks", []):
                if isinstance(h, dict) and "growmos" in str(h.get("command", "")):
                    present = True
                    if h.get("command") != cmd:  # upgrade older growmos hook commands
                        h["command"] = cmd
                        changed = True
        if not present:
            arr.append({"matcher": "", "hooks": [{"type": "command", "command": cmd}]})
            changed = True
    return changed


def _merge_mcp(root: Path, path: Path = None) -> str:
    path = path or (root / ".mcp.json")
    data = read_json(path, {}) or {}
    servers = data.setdefault("mcpServers", {})
    if "growmos" in servers:
        return "unchanged"
    servers["growmos"] = {"command": "growmos", "args": ["mcp"]}
    write_json(path, data)
    return "updated" if path.exists() else "created"


def integrate(root: Path, target: str, file: str = "") -> List[str]:
    root = Path(root)
    block = _block()
    out: List[str] = []
    targets = [target] if target != "all" else ["claude", "codex", "gemini", "cursor", "hooks", "ci"]
    for t in targets:
        if t == "claude":
            out.append(f"CLAUDE.md: {upsert_block(root / 'CLAUDE.md', block)}")
            skill = root / ".claude" / "skills" / "growmos" / "SKILL.md"
            skill.parent.mkdir(parents=True, exist_ok=True)
            skill.write_text((TPL / "SKILL.md").read_text(encoding="utf-8"), encoding="utf-8")
            out.append(".claude/skills/growmos/SKILL.md: written")
            spath = root / ".claude" / "settings.json"
            settings = read_json(spath, {}) or {}
            if _merge_hooks(settings):
                write_json(spath, settings)
                out.append(".claude/settings.json: hooks set (SessionStart → brief + pending-work nudge, Stop → scan + finish-the-loop)")
            else:
                out.append(".claude/settings.json: hooks already present")
            out.append(f".mcp.json: {_merge_mcp(root)} (growmos MCP server)")
        elif t == "codex":
            out.append(f"AGENTS.md: {upsert_block(root / 'AGENTS.md', block)}")
        elif t == "gemini":
            out.append(f"GEMINI.md: {upsert_block(root / 'GEMINI.md', block)}")
        elif t == "grok":
            out.append(f"AGENTS.md: {upsert_block(root / 'AGENTS.md', block)}")
            out.append(f".mcp.json: {_merge_mcp(root)} (if your Grok CLI supports MCP servers)")
        elif t == "generic":
            out.append(f"AGENTS.md: {upsert_block(root / 'AGENTS.md', block)}")
        elif t == "cursor":
            p = root / ".cursor" / "rules" / "growmos.mdc"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("---\ndescription: growmos living knowledge graph protocol\nalwaysApply: true\n---\n\n" + block,
                         encoding="utf-8")
            out.append(".cursor/rules/growmos.mdc: written")
            out.append(f".cursor/mcp.json: {_merge_mcp(root, root / '.cursor' / 'mcp.json')} (growmos MCP server)")
        elif t == "mcp":
            out.append(f".mcp.json: {_merge_mcp(root)} (Claude Code / Codex / Grok / any MCP client)")
            if (root / ".cursor").exists():
                out.append(f".cursor/mcp.json: {_merge_mcp(root, root / '.cursor' / 'mcp.json')}")
            out.append('  other clients: add {"mcpServers": {"growmos": {"command": "growmos", "args": ["mcp"]}}} to their MCP config')
        elif t == "file":
            if not file:
                raise ValueError("--file PATH is required for target 'file'")
            out.append(f"{file}: {upsert_block(root / file, block)}")
        elif t == "hooks":
            out.extend(install_git_hooks(root))
        elif t == "ci":
            p = root / ".github" / "workflows" / "growmos.yml"
            if p.exists():
                out.append(".github/workflows/growmos.yml: exists (left untouched)")
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text((TPL / "workflow.yml").read_text(encoding="utf-8"), encoding="utf-8")
                out.append(".github/workflows/growmos.yml: written")
        else:
            raise ValueError(f"unknown target '{t}'. Choose from: {', '.join(TARGETS)}")
    return out


HOOK_MARK = "# growmos-hook"


def install_git_hooks(root: Path) -> List[str]:
    git_dir = root / ".git"
    if not git_dir.exists():
        return ["git hooks: skipped (not a git repository)"]
    # respect core.hooksPath if configured inside the repo
    hooks_dir = git_dir / "hooks"
    cfg = git_dir / "config"
    if cfg.exists():
        m = re.search(r"hooksPath\s*=\s*(.+)", cfg.read_text(encoding="utf-8", errors="ignore"))
        if m:
            cand = (root / m.group(1).strip()).resolve()
            if cand.exists():
                hooks_dir = cand
    hooks_dir.mkdir(parents=True, exist_ok=True)
    out = []
    snippet = (f"\n{HOOK_MARK}: queue changed docs for the living knowledge graph (safe no-op if growmos is absent)\n"
               f"command -v growmos >/dev/null 2>&1 && growmos scan --quiet || true\n")
    for name in ("post-commit", "post-merge", "post-checkout"):
        p = hooks_dir / name
        if p.exists():
            text = p.read_text(encoding="utf-8", errors="ignore")
            if HOOK_MARK in text:
                out.append(f"{name}: already installed")
                continue
            p.write_text(text.rstrip("\n") + "\n" + snippet, encoding="utf-8")
            out.append(f"{name}: appended")
        else:
            p.write_text("#!/bin/sh\n" + snippet, encoding="utf-8")
            out.append(f"{name}: created")
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return [f"git hooks ({hooks_dir.relative_to(root) if hooks_dir.is_relative_to(root) else hooks_dir}): " + ", ".join(out)]


def detect_targets(root: Path) -> List[str]:
    """Guess which agent CLIs are in use from files present."""
    found = []
    if (root / "CLAUDE.md").exists() or (root / ".claude").exists():
        found.append("claude")
    if (root / "AGENTS.md").exists() or (root / ".codex").exists():
        found.append("codex")
    if (root / "GEMINI.md").exists() or (root / ".gemini").exists():
        found.append("gemini")
    if (root / ".cursor").exists():
        found.append("cursor")
    return found
