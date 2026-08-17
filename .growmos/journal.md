# growmos journal

Shared memo between humans and agents. Append-only; newest at the bottom.


### 2026-08-17T11:51:11Z · claude

Initial build of growmos from the Anthropic Knowledge Graph Playbook PDF: package (store, graph, prompts, schema, evaluate, export, providers, integrate, mcp, cli), Apollo example corpus, 18 tests, docs. Design decisions: agent-native task packets (no API key needed), zero dependencies, JSONL store committed with code, MCP server for any CLI. Verified end-to-end on the Apollo corpus (1 component, density 1.58, precision 1.0).

### 2026-08-17T12:27:04Z · agent

Added growmos view: interactive HTML explorer + README demo screenshot; bumped to 0.1.1.

### 2026-08-17T17:05:55Z · claude

Session wrap (2026-08-17): published growmos 0.1.5 to PyPI, public GitHub repo with release, Pages site + live demos, MCP Registry listing as com.codician/growmos (DNS-verified), awesome-mcp-servers PR, launch kit. Graph caught up on all doc edits; profiles refreshed.
