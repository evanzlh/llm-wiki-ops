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
            self.assertIn("user-supplied exact", text)
            self.assertIn("llmwikiops -C <root> info --json", text)
            self.assertNotIn("@name", text)
        self.assertIn("Never guess a vault, source root, or\nfallback location", llm_wiki)

    def test_core_skill_resolution_uses_nearest_repository_config(self) -> None:
        for name in ("llm-wiki", "wiki-query", "wiki-update", "wiki-capture"):
            text = self.read(f"obsidian_wiki/_data/skills/{name}/SKILL.md")
            with self.subTest(name=name):
                self.assertIn("nearest", text)
                self.assertIn(".llmwikiops/config.toml", text)
                self.assertIn("External adapter context", text)
                self.assertIn("`<wiki-cli>` is `llmwikiops -C <root>`", text)
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
                authority = (
                    "`<root>/AGENTS.md`",
                    "`<root>/.skills/llm-wiki/SKILL.md`",
                    "`<vault>/AGENTS.md` when present",
                    "`<root>/.skills/<selected-task>/SKILL.md`",
                )
                indexes = [text.index(item) for item in authority]
                self.assertEqual(indexes, sorted(indexes))
                self.assertIn("keep `<root>` immutable", text)
                self.assertIn("metadata overrides", text)
                self.assertNotIn("@name", text)

    def test_install_docs_describe_nested_repository_discovery(self) -> None:
        install = self.read("docs/installation.md")

        self.assertIn("work from anywhere inside it", install)
        self.assertIn("discovers `.llmwikiops/config.toml` while walking up", install)
        self.assertIn("repository-local skills and bootstrap files", install.casefold())

    def test_core_skill_descriptions_use_repository_local_authority(self) -> None:
        paths = (
            "obsidian_wiki/_data/skills/wiki-query/SKILL.md",
            "obsidian_wiki/_data/skills/wiki-update/SKILL.md",
            "obsidian_wiki/_data/skills/wiki-capture/SKILL.md",
        )

        for relpath in paths:
            with self.subTest(relpath=relpath):
                text = self.read(relpath)
                self.assertIn("Repository-local context", text)
                self.assertIn("nearest ancestor `.llmwikiops/config.toml`", text)
                self.assertIn("External adapter context", text)
                self.assertIn("validated immutable root", text)
                self.assertIn("<wiki-cli>", text)
                self.assertNotIn("repository root as the command working directory", text)
                self.assertNotIn("@name", text)

    def test_wiki_query_uses_only_owning_repository_config(self) -> None:
        wiki_query = self.read("obsidian_wiki/_data/skills/wiki-query/SKILL.md")

        self.assertIn("nearest ancestor `.llmwikiops/config.toml`", wiki_query)
        self.assertIn("user-supplied exact", wiki_query)
        self.assertIn("already validated retained exact `<root>`", wiki_query)
        self.assertIn("Never infer or switch roots", wiki_query)
        self.assertNotIn("@name", wiki_query)


if __name__ == "__main__":
    unittest.main()
