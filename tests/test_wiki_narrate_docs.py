from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WikiNarrateDocsTest(unittest.TestCase):
    def read(self, relpath: str) -> str:
        return (ROOT / relpath).read_text(encoding="utf-8")

    def test_skill_declares_the_portable_command_and_voices(self) -> None:
        skill = self.read("obsidian_wiki/_data/skills/wiki-narrate/SKILL.md")

        self.assertIn("/wiki-narrate <topic> [--voice briefing|plain-language|lecturer]", skill)
        self.assertNotIn("--save", skill)
        self.assertIn("default voice is `briefing`", skill)
        self.assertIn("case-sensitive", skill)
        self.assertIn("Unsupported values", skill)

    def test_skill_requires_closed_set_citations(self) -> None:
        skill = self.read("obsidian_wiki/_data/skills/wiki-narrate/SKILL.md")

        self.assertIn("every factual sentence", skill)
        self.assertIn("^[inferred]", skill)
        self.assertIn("^[ambiguous]", skill)
        self.assertIn("web knowledge", skill)
        self.assertIn("model memory", skill)

    def test_skill_returns_narration_without_a_saved_readout(self) -> None:
        skill = self.read("obsidian_wiki/_data/skills/wiki-narrate/SKILL.md")

        self.assertIn("Return the narration in the conversation", skill)
        self.assertIn("wiki-capture", skill)
        self.assertIn("wiki-ingest", skill)
        self.assertNotIn("_readouts", skill)
        self.assertNotIn("transaction begin", skill)
        self.assertNotIn("append to `log.md`", skill)

    def test_voice_reference_has_exactly_the_three_first_release_voices(self) -> None:
        voices = self.read("obsidian_wiki/_data/skills/wiki-narrate/references/voices.md")

        headings = set(re.findall(r"^## `([^`]+)`$", voices, flags=re.MULTILINE))
        self.assertEqual(headings, {"briefing", "plain-language", "lecturer"})


if __name__ == "__main__":
    unittest.main()
