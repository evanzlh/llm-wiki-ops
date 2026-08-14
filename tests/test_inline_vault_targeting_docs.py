from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InlineVaultTargetingDocsTest(unittest.TestCase):
    def read(self, relpath: str) -> str:
        return (ROOT / relpath).read_text()

    def test_central_protocol_uses_nearest_repository_config_without_fallbacks(self) -> None:
        llm_wiki = self.read("obsidian_wiki/_data/skills/llm-wiki/SKILL.md")
        agents = self.read("obsidian_wiki/_data/bootstrap/AGENTS.md")

        for text in (llm_wiki, agents):
            self.assertIn("nearest ancestor", text)
            self.assertIn(".llmwikiops/config.toml", text)
            self.assertNotIn("@name", text)
        self.assertIn("Never guess a vault, source root, or\nfallback location", llm_wiki)

    def test_core_skill_resolution_uses_nearest_repository_config(self) -> None:
        for name in ("llm-wiki", "wiki-query", "wiki-update", "wiki-capture"):
            text = self.read(f"obsidian_wiki/_data/skills/{name}/SKILL.md")
            with self.subTest(name=name):
                self.assertIn("nearest", text)
                self.assertIn(".llmwikiops/config.toml", text)
                self.assertNotIn("@name", text)

    def test_runtime_bootstraps_route_nearest_repository_canonical_then_task(self) -> None:
        for relpath in [
            "obsidian_wiki/_data/bootstrap/AGENTS.md",
            "obsidian_wiki/_data/bootstrap/agent/rules/llmwikiops.md",
            "obsidian_wiki/_data/bootstrap/cursor/rules/llmwikiops.mdc",
            "obsidian_wiki/_data/bootstrap/github/copilot-instructions.md",
            "obsidian_wiki/_data/bootstrap/kiro/steering/llmwikiops.md",
            "obsidian_wiki/_data/bootstrap/windsurf/rules/llmwikiops.md",
        ]:
            with self.subTest(relpath=relpath):
                text = self.read(relpath)
                self.assertIn("nearest", text)
                self.assertIn(".llmwikiops/config.toml", text)
                canonical = text.index("`llm-wiki` skill")
                task = text.index("task skill")
                self.assertLess(canonical, task)
                self.assertNotIn(".skills/", text)
                self.assertNotIn("SKILL.md", text)
                self.assertNotIn("@name", text)
                self.assertNotIn("global", text.casefold())

    def test_install_docs_describe_nested_repository_discovery(self) -> None:
        install = self.read("docs/installation.md")

        self.assertIn("work from anywhere inside it", install)
        self.assertIn("discovers `.llmwikiops/config.toml` while walking up", install)
        self.assertIn("repository-local skills and bootstrap files", install.casefold())

    def test_core_skill_descriptions_use_repository_local_authority(self) -> None:
        expectations = {
            "obsidian_wiki/_data/skills/wiki-query/SKILL.md": "only repository/vault selection authority",
            "obsidian_wiki/_data/skills/wiki-update/SKILL.md": "keep that repository root as the command working directory",
            "obsidian_wiki/_data/skills/wiki-capture/SKILL.md": "keep the repository root as CWD",
        }

        for relpath, expected in expectations.items():
            with self.subTest(relpath=relpath):
                text = self.read(relpath)
                self.assertIn(expected, text)
                self.assertNotIn("@name", text)

    def test_wiki_query_uses_only_owning_repository_config(self) -> None:
        wiki_query = self.read("obsidian_wiki/_data/skills/wiki-query/SKILL.md")

        self.assertIn("It is the only repository/vault selection authority", wiki_query)
        self.assertIn("nearest ancestor `.llmwikiops/config.toml`", wiki_query)
        self.assertNotIn("inline", wiki_query.casefold())
        self.assertNotIn("global", wiki_query.casefold())
        self.assertNotIn("@name", wiki_query)


if __name__ == "__main__":
    unittest.main()
