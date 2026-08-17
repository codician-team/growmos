"""Minimal MCP (Model Context Protocol) stdio server — zero dependencies.

Lets any MCP-capable agent CLI (Claude Code, Codex, Cursor, Grok, Gemini, …) call the graph
as tools instead of shelling out. Configure with:

    {"mcpServers": {"growmos": {"command": "growmos", "args": ["mcp"]}}}

Speaks JSON-RPC 2.0, newline-delimited, over stdin/stdout. Implements: initialize,
notifications/initialized, ping, tools/list, tools/call (+ empty resources/prompts lists).
"""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Dict, List

from . import __version__

PROTOCOL = "2024-11-05"


def _tool(name: str, desc: str, props: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    return {"name": name, "description": desc,
            "inputSchema": {"type": "object", "properties": props, "required": required}}


TOOLS = [
    _tool("growmos_context", "Compact brief of the repository knowledge graph: hubs, health, pending work, latest journal. Call at the start of a session.", {}, []),
    _tool("growmos_query", "Ask a multi-hop question; returns the k-hop subgraph (triples with edge ids and provenance) to answer from. Answer only from it and cite edge ids.",
          {"question": {"type": "string"}, "seeds": {"type": "array", "items": {"type": "string"}}, "hops": {"type": "integer"}}, ["question"]),
    _tool("growmos_entity", "Show one entity: description, profile, edges with provenance, sources, aliases.", {"name": {"type": "string"}}, ["name"]),
    _tool("growmos_search", "Find entities by name/alias substring.", {"text": {"type": "string"}}, ["text"]),
    _tool("growmos_remember", "Record a durable node (component, decision, concept, person…) with a grounded one-sentence description.",
          {"name": {"type": "string"}, "type": {"type": "string"}, "description": {"type": "string"}, "source": {"type": "string", "description": "provenance label, e.g. session:2026-08-17 or a file path"}}, ["name", "type", "description"]),
    _tool("growmos_link", "Record a typed relation A --[predicate]--> B (short verb phrase predicates).",
          {"source": {"type": "string"}, "predicate": {"type": "string"}, "target": {"type": "string"}, "provenance": {"type": "string"}}, ["source", "predicate", "target"]),
    _tool("growmos_journal", "Append a note to the shared journal (what changed and why).", {"text": {"type": "string"}, "author": {"type": "string"}}, ["text"]),
    _tool("growmos_check", "Fact-check claims (as 'A --[pred]--> B' lines or free text) against graph edges with provenance.", {"text": {"type": "string"}}, ["text"]),
    _tool("growmos_next", "Get the next task packet (extraction/resolution/profile) to grow the graph. Produce the JSON it asks for, then call growmos_apply.", {}, []),
    _tool("growmos_apply", "Apply a completed packet. kind = extraction|resolution|profile; payload = the JSON object; plus source/chunk, type, or entity as printed in the packet.",
          {"kind": {"type": "string"}, "payload": {"type": "object"}, "source": {"type": "string"}, "chunk": {"type": "integer"}, "partial": {"type": "boolean"}, "type": {"type": "string"}, "entity": {"type": "string"}}, ["kind", "payload"]),
    _tool("growmos_status", "Graph statistics and health signals.", {}, []),
    _tool("growmos_sample", "Random node for human review (the daily comprehension check).", {}, []),
]


def _run_cli(argv: List[str], stdin_text: str = "") -> str:
    """Invoke the CLI in-process and capture stdout."""
    from . import cli
    buf = io.StringIO()
    old_stdin = sys.stdin
    try:
        sys.stdin = io.StringIO(stdin_text)
        with redirect_stdout(buf):
            try:
                cli.main(argv)
            except SystemExit as e:
                if e.code not in (0, None):
                    buf.write(f"\n[exit {e.code}]")
    finally:
        sys.stdin = old_stdin
    return buf.getvalue()


def call_tool(name: str, args: Dict[str, Any]) -> str:
    a = args or {}
    if name == "growmos_context":
        return _run_cli(["context"])
    if name == "growmos_status":
        return _run_cli(["status"])
    if name == "growmos_sample":
        return _run_cli(["sample"])
    if name == "growmos_next":
        return _run_cli(["next"])
    if name == "growmos_query":
        argv = ["query", a["question"], "--hops", str(int(a.get("hops", 2)))]
        for s in a.get("seeds") or []:
            argv += ["--seed", s]
        return _run_cli(argv)
    if name == "growmos_entity":
        return _run_cli(["show", a["name"]])
    if name == "growmos_search":
        return _run_cli(["search", a["text"]])
    if name == "growmos_remember":
        argv = ["remember", a["name"], "--type", a["type"], "--desc", a["description"]]
        if a.get("source"):
            argv += ["--source", a["source"]]
        return _run_cli(argv)
    if name == "growmos_link":
        argv = ["link", a["source"], a["predicate"], a["target"]]
        if a.get("provenance"):
            argv += ["--source", a["provenance"]]
        return _run_cli(argv)
    if name == "growmos_journal":
        argv = ["journal", a["text"]]
        if a.get("author"):
            argv += ["--author", a["author"]]
        return _run_cli(argv)
    if name == "growmos_check":
        return _run_cli(["check", "-"], stdin_text=a["text"])
    if name == "growmos_apply":
        argv = ["apply", a["kind"], "-"]
        if a.get("source"):
            argv += ["--source", a["source"]]
        if a.get("chunk") is not None:
            argv += ["--chunk", str(int(a["chunk"]))]
        if a.get("partial"):
            argv += ["--partial"]
        if a.get("type"):
            argv += ["--type", a["type"]]
        if a.get("entity"):
            argv += ["--entity", a["entity"]]
        return _run_cli(argv, stdin_text=json.dumps(a["payload"]))
    raise KeyError(f"unknown tool {name}")


def serve() -> None:
    inp = sys.stdin
    out = sys.stdout

    def send(obj: Dict[str, Any]) -> None:
        out.write(json.dumps(obj) + "\n")
        out.flush()

    for line in inp:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        mid = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params") or {}
        try:
            if method == "initialize":
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "protocolVersion": params.get("protocolVersion") or PROTOCOL,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "growmos", "version": __version__},
                }})
            elif method == "notifications/initialized" or method.startswith("notifications/"):
                continue
            elif method == "ping":
                send({"jsonrpc": "2.0", "id": mid, "result": {}})
            elif method == "tools/list":
                send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
            elif method == "tools/call":
                text = call_tool(params.get("name", ""), params.get("arguments") or {})
                send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}], "isError": False}})
            elif method in ("resources/list", "prompts/list"):
                key = method.split("/")[0]
                send({"jsonrpc": "2.0", "id": mid, "result": {key: []}})
            else:
                if mid is not None:
                    send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}})
        except Exception as e:  # noqa: BLE001 — never crash the transport
            if mid is not None:
                send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True}})
