"""End-to-end and unit tests for growmos (stdlib unittest — run: python -m unittest -v)."""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from growmos import cli, schema as S  # noqa: E402
from growmos.evaluate import Evaluator, doctor  # noqa: E402
from growmos.graph import Graph  # noqa: E402
from growmos.prompts import chunk_text, extraction_packet, query_packet, resolution_blocks  # noqa: E402
from growmos.store import Store  # noqa: E402
from growmos.util import read_json  # noqa: E402

EXAMPLE = ROOT / "examples" / "apollo"


def run_cli(*argv: str, stdin: str = "") -> str:
    buf = io.StringIO()
    old = sys.stdin
    sys.stdin = io.StringIO(stdin)
    try:
        with redirect_stdout(buf):
            code = cli.main(list(argv))
    finally:
        sys.stdin = old
    assert code == 0, f"exit {code}: {buf.getvalue()}"
    return buf.getvalue()


class ApolloFixture(unittest.TestCase):
    """Builds the playbook's Apollo corpus graph in a temp repo, agent-natively."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="growmos-"))
        shutil.copytree(EXAMPLE / "docs", cls.tmp / "docs")
        cls.st = Store.init(cls.tmp, preset_name="general")
        cls.st.scan()
        for f in sorted((EXAMPLE / "extractions").glob("*.json")):
            data = read_json(f)
            sid = next(s for s, r in cls.st.sources.items() if r["ref"] == data["source"])
            cls.st.apply_extraction(sid, data)
        res = read_json(EXAMPLE / "resolution.json")
        for etype, payload in res.items():
            names = [e["name"] for e in cls.st.entities.values() if e["type"] == etype]
            clusters, _ = S.validate_resolution(payload, names)
            cls.st.apply_resolution(etype, clusters)
        gold_dir = cls.st.p("eval", "gold")
        for f in (EXAMPLE / "gold").glob("*.json"):
            shutil.copy(f, gold_dir / f.name)
        shutil.copy(EXAMPLE / "gold-aliases.json", cls.st.p("eval", "aliases.json"))
        cls.st.save()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self) -> None:
        self.st = Store(self.tmp).load()
        self.g = Graph(self.st)

    def test_graph_is_one_component_after_resolution(self):
        d = self.g.diagnostics()
        self.assertEqual(d["components"], 1, d)
        self.assertGreaterEqual(d["nodes"], 15)
        self.assertGreater(d["density"], 1.0)
        self.assertEqual(d["provisional"], 0)

    def test_hard_resolution_cases(self):
        # Edwin Aldrin -> Buzz Aldrin (zero character overlap) and Neil Armstrong -> Neil Alden Armstrong
        self.assertEqual(self.st.resolve_name("Edwin Aldrin"), self.st.resolve_name("Buzz Aldrin"))
        self.assertEqual(self.st.resolve_name("Neil Armstrong"), self.st.resolve_name("Neil Alden Armstrong"))
        # Gemini 12 was NOT folded into anything
        self.assertIsNotNone(self.st.resolve_name("Gemini 12"))
        self.assertNotEqual(self.st.resolve_name("Gemini 12"), self.st.resolve_name("Gemini 8"))

    def test_corroboration_counts_documents(self):
        armstrong = self.st.resolve_name("Neil Armstrong")
        apollo11 = self.st.resolve_name("Apollo 11")
        rel = next(r for r in self.st.relations.values()
                   if r["source"] == armstrong and r["target"] == apollo11 and r["predicate"] == "commanded")
        self.assertEqual(rel["confidence"], 2)  # neil-armstrong.md + apollo-11.md

    def test_check_claim_gives_playbook_feedback(self):
        res = self.g.check_claim("Neil Armstrong", "commanded", "Gemini 12")
        self.assertEqual(res["verdict"], "contradicting_evidence")
        ev = "\n".join(res["evidence"])
        self.assertIn("Buzz Aldrin) --[flew on]--> (Gemini 12", ev)
        self.assertIn("commanded]--> (Apollo 11", ev)
        ok = self.g.check_claim("Armstrong", "walked on", "Moon")  # partial name → not resolvable → absent
        self.assertIn(ok["verdict"], ("absent", "contradicting_evidence"))
        sup = self.g.check_claim("Neil Armstrong", "walked on", "Moon")
        self.assertEqual(sup["verdict"], "supported")

    def test_query_packet_grounds_and_cites(self):
        text, meta = query_packet(self.st, "Which locations are connected to people who flew on Apollo 11?")
        self.assertIn("(Apollo 11)", text)
        self.assertIn("[r_", text)
        self.assertGreaterEqual(meta["edges"], 10)
        self.assertIn("Answer using only the knowledge graph", text)

    def test_eval_precision_perfect_recall_partial(self):
        rep = Evaluator(self.st).evaluate()
        by = {r["doc"]: r for r in rep["docs"]}
        self.assertEqual(by["docs/apollo-11.md"]["raw"]["p"], 1.0)
        self.assertLess(by["docs/neil-armstrong.md"]["raw"]["r"], 1.0)
        self.assertIn("purdue university", by["docs/neil-armstrong.md"]["missed_entities"])

    def test_doctor_runs_and_reports_ten_items(self):
        checks = doctor(self.st)
        self.assertEqual(len(checks), 10)
        names = {c["item"] for c in checks}
        self.assertIn("Provenance tracking", names)
        self.assertEqual(next(c for c in checks if c["item"] == "Provenance tracking")["status"], "ok")

    def test_incremental_scan_flags_only_changed(self):
        rep = self.st.scan()
        self.assertEqual(rep["new"], [])
        self.assertEqual(rep["changed"], [])
        (self.tmp / "docs" / "apollo-11.md").write_text("# Apollo 11\n\nEdited.\n", encoding="utf-8")
        rep = self.st.scan()
        self.assertEqual(rep["changed"], ["docs/apollo-11.md"])
        self.assertEqual(len(self.st.pending_sources()), 1)
        # restore
        shutil.copy(EXAMPLE / "docs" / "apollo-11.md", self.tmp / "docs" / "apollo-11.md")
        self.st.scan()
        pkt, meta = extraction_packet(self.st, self.st.pending_sources()[0]["id"])
        self.assertIn("growmos apply extraction", pkt)
        self.assertEqual(meta["chunks"], 1)

    def test_export_formats(self):
        from growmos.export import EXPORTERS
        for name, fn in EXPORTERS.items():
            out = fn(self.st)
            self.assertTrue(out.strip(), name)
        self.assertIn("MERGE (n:PERSON", EXPORTERS["cypher"](self.st))
        self.assertIn("CREATE TABLE IF NOT EXISTS entities", EXPORTERS["sql"](self.st))

    def test_cli_status_context_show(self):
        out = run_cli("--root", str(self.tmp), "status")
        self.assertIn("nodes", out)
        out = run_cli("--root", str(self.tmp), "context", "--brief")
        self.assertIn("Hubs:", out)
        out = run_cli("--root", str(self.tmp), "show", "Buzz Aldrin")
        self.assertIn("Edwin Aldrin", out)  # alias listed


class UnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="growmos-unit-"))
        (self.tmp / "README.md").write_text("# Demo\n\nThe Scheduler depends on the Store.\n", encoding="utf-8")
        self.st = Store.init(self.tmp, preset_name="software")
        self.st.scan()
        self.st.save()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_validate_extraction_drops_dangling_and_bad_types(self):
        cleaned, problems = S.validate_extraction({
            "entities": [{"name": "A", "type": "PERSON", "description": "x"}, {"name": "B", "type": "NOPE", "description": ""}],
            "relations": [{"source": "A", "predicate": "knows", "target": "B"}, {"source": "A", "predicate": "knows", "target": "Z"}],
        }, S.BASE_ENTITY_TYPES)
        self.assertEqual(len(cleaned["entities"]), 2)
        self.assertEqual(cleaned["entities"][1]["type"], "ARTIFACT")
        self.assertEqual(len(cleaned["relations"]), 1)
        self.assertTrue(any("dropped" in p for p in problems))

    def test_validate_resolution_fallback_and_duplicates(self):
        clusters, problems = S.validate_resolution(
            {"clusters": [{"canonical": "A", "aliases": ["A", "a2"]}, {"canonical": "B", "aliases": ["a2", "B"]}]},
            ["A", "a2", "B", "C"])
        names = sorted(n for c in clusters for n in c["aliases"])
        self.assertEqual(names, ["A", "B", "C", "a2"])
        self.assertTrue(any("missing from every cluster" in p for p in problems))
        self.assertTrue(any("more than one cluster" in p for p in problems))

    def test_remember_link_journal_and_merge(self):
        eid, created = self.st.remember("Scheduler", "COMPONENT", "Schedules jobs.", "session:test")
        self.assertTrue(created)
        rid, _ = self.st.link("Scheduler", "depends on", "Store", "session:test")
        self.assertIn(rid, self.st.relations)
        self.assertEqual(self.st.relations[rid]["sources"], [self.st._source_id("session:test")])
        # merge alias into canonical and ensure edges follow
        other, _ = self.st.get_or_create_entity("The Store", "COMPONENT", "same thing")
        store_id = self.st.resolve_name("Store")
        self.st.link("Scheduler", "reads", "The Store", "session:test")
        self.st.merge(other, store_id)
        self.assertNotIn(other, self.st.entities)
        self.assertEqual(self.st.resolve_name("The Store"), store_id)
        self.assertTrue(all(r["source"] in self.st.entities and r["target"] in self.st.entities
                            for r in self.st.relations.values()))
        self.st.journal("hello", author="test")
        self.assertIn("hello", self.st.journal_tail(1)[0])
        self.st.save()
        st2 = Store(self.tmp).load()
        self.assertEqual(len(st2.relations), len(self.st.relations))

    def test_apply_extraction_creates_provisional_and_resolves_exact(self):
        sid = next(iter(self.st.sources))
        rep = self.st.apply_extraction(sid, {"entities": [
            {"name": "Scheduler", "type": "COMPONENT", "description": "d"},
            {"name": "scheduler", "type": "COMPONENT", "description": "dup by case"}],
            "relations": []})
        self.assertEqual(rep["new_entities"], 1)  # case-insensitive exact match resolves
        self.assertEqual(len(self.st.provisional_entities()), 1)
        blocks = resolution_blocks(self.st, "COMPONENT")
        self.assertEqual(len(blocks), 1)
        self.assertEqual(self.st.sources[sid]["status"], "extracted")

    def test_chunking_keeps_overlap(self):
        text = "\n\n".join(f"## Section {i}\n\n" + ("word " * 400) for i in range(6))
        chunks = chunk_text(text, max_chars=3000)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= 4600 for c in chunks))

    def test_schema_bump_and_type_added_by_remember(self):
        v0 = self.st.schema_version
        out = run_cli("--root", str(self.tmp), "remember", "Kafka", "--type", "QUEUE", "--desc", "message bus")
        self.assertIn("created", out)
        st = Store(self.tmp).load()
        self.assertEqual(st.schema_version, v0 + 1)
        self.assertIn("QUEUE", st.entity_types)

    def test_integrate_is_idempotent(self):
        from growmos.integrate import integrate
        integrate(self.tmp, "codex")
        first = (self.tmp / "AGENTS.md").read_text()
        integrate(self.tmp, "codex")
        self.assertEqual(first, (self.tmp / "AGENTS.md").read_text())
        self.assertEqual(first.count("growmos:start"), 1)
        integrate(self.tmp, "claude")
        settings = read_json(self.tmp / ".claude" / "settings.json")
        self.assertIn("SessionStart", settings["hooks"])
        integrate(self.tmp, "claude")
        settings2 = read_json(self.tmp / ".claude" / "settings.json")
        self.assertEqual(len(settings2["hooks"]["SessionStart"]), 1)
        self.assertIn("growmos", read_json(self.tmp / ".mcp.json")["mcpServers"])

    def test_mcp_tools_call(self):
        from growmos import mcp
        import os
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            out = mcp.call_tool("growmos_remember", {"name": "Store", "type": "COMPONENT", "description": "persists"})
            self.assertIn("component/store", out)
            out = mcp.call_tool("growmos_context", {})
            self.assertIn("growmos", out)
            names = {t["name"] for t in mcp.TOOLS}
            self.assertIn("growmos_query", names)
        finally:
            os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
