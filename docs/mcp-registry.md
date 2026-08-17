# Publishing growmos to the official MCP Registry

The registry (https://registry.modelcontextprotocol.io) lists MCP servers for every client to
discover. Ownership of `io.github.codician-team/*` is proven by a GitHub login as the org;
ownership of the PyPI package is proven by the `mcp-name:` marker in the package README (present).

```bash
# 1. publish the PyPI release whose README carries the mcp-name marker (0.1.4+)
rm -rf dist && uv build && uv publish --token pypi-…

# 2. install the publisher CLI
brew install mcp-publisher            # or: curl -L "https://github.com/modelcontextprotocol/registry/releases/latest/download/mcp-publisher_$(uname -s | tr '[:upper:]' '[:lower:]')_$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/').tar.gz" | tar xz mcp-publisher

# 3. log in as the codician-team GitHub org (opens a browser)
mcp-publisher login github

# 4. publish server.json from the repo root
mcp-publisher publish
```
Bump `version` in `server.json` (both places) with each PyPI release.
