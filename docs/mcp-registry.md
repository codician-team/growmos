# Publishing growmos to the official MCP Registry

growmos is listed under the Codician domain namespace **`com.codician/growmos`** (DNS-verified),
independent of any GitHub account. Ownership of the PyPI package is proven by the
`mcp-name: com.codician/growmos` marker in the package README.

Release procedure (maintainers):

```bash
# 1. bump version in pyproject.toml, src/growmos/__init__.py and server.json (both places)
# 2. publish to PyPI
rm -rf dist && uv build && uv publish --token pypi-…
# 3. authenticate with the codician.com DNS key (kept outside the repo) and publish
mcp-publisher login dns --domain codician.com --private-key "$(openssl pkey -in ~/.config/mcp-publisher/codician-dns-key.pem -outform DER | tail -c 32 | xxd -p -c 64)"
mcp-publisher publish
```

The apex TXT record `codician.com. IN TXT "v=MCPv1; k=ed25519; p=…"` must stay in place; rotate the
key by replacing that record (never leave the old one — it is tried first).
