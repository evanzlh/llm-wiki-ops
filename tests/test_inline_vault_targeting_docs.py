from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InlineVaultTargetingDocsTest(unittest.TestCase):
    def read(self, relpath: str) -> str:
        return (ROOT / relpath).read_text()

    def test_central_protocol_documents_inline_override_before_fallbacks(self) -> None:
        llm_wiki = self.read("obsidian_wiki/_data/skills/llm-wiki/SKILL.md")
        agents = self.read("obsidian_wiki/_data/bootstrap/AGENTS.md")

        self.assertIn("0. **Inline vault override (`@name`)", llm_wiki)
        self.assertIn("0. **Inline vault override (`@name`)", agents)
        self.assertIn("resolve `~/.obsidian-wiki/config.<name>` directly", llm_wiki)
        self.assertIn("do **not** silently fall back to the default", agents)

    def test_skill_resolution_summaries_include_inline_override(self) -> None:
        stale = []
        skill_root = ROOT / "obsidian_wiki" / "_data" / "skills"
        for skill_file in sorted(skill_root.glob("*/SKILL.md")):
            text = skill_file.read_text()
            if "follow the Config Resolution Protocol" not in text:
                continue
            if "walk up CWD for `.env`" in text and "inline `@name` override" not in text:
                stale.append(skill_file.relative_to(ROOT).as_posix())

        self.assertEqual(stale, [])

    def test_agent_bootstrap_files_mention_named_vault_routing(self) -> None:
        for relpath in [
            "obsidian_wiki/_data/bootstrap/AGENTS.md",
            "obsidian_wiki/_data/bootstrap/agent/rules/obsidian-wiki.md",
            "obsidian_wiki/_data/bootstrap/cursor/rules/obsidian-wiki.mdc",
            "obsidian_wiki/_data/bootstrap/github/copilot-instructions.md",
            "obsidian_wiki/_data/bootstrap/kiro/steering/obsidian-wiki.md",
            "obsidian_wiki/_data/bootstrap/windsurf/rules/obsidian-wiki.md",
            "docs/installation.md",
        ]:
            with self.subTest(relpath=relpath):
                self.assertIn("@name", self.read(relpath))

    def test_install_docs_say_all_supported_agents_inherit_named_vault_routing(self) -> None:
        install = self.read("docs/installation.md")

        self.assertIn("All supported agents can use this syntax", install)
        self.assertIn("Claude Code, Cursor, Windsurf, Codex, Gemini", install)

    def test_core_skill_descriptions_include_named_vault_examples(self) -> None:
        examples = {
            "obsidian_wiki/_data/skills/wiki-query/SKILL.md": "wiki-query @work",
            "obsidian_wiki/_data/skills/wiki-update/SKILL.md": "@work update wiki",
            "obsidian_wiki/_data/skills/wiki-capture/SKILL.md": "@research save this",
        }

        for relpath, expected in examples.items():
            with self.subTest(relpath=relpath):
                self.assertIn(expected, self.read(relpath))

    def test_wiki_query_does_not_prefer_default_over_inline_override(self) -> None:
        wiki_query = self.read("obsidian_wiki/_data/skills/wiki-query/SKILL.md")

        self.assertIn("For cross-project queries without `@name`", wiki_query)
        self.assertNotIn("Prefer `~/.obsidian-wiki/config` for cross-project queries", wiki_query)


if __name__ == "__main__":
    unittest.main()
