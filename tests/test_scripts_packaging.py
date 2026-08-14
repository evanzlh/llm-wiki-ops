"""Distribution guards for generic scripts and removed scheduler artifacts."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
import zipfile

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
REMOVED_SCHEDULER_ARTIFACTS = (
    "daily-update.sh",
    "com.llmwikiops.daily-update.plist",
    "wiki-notify.sh",
)
GENERIC_SCRIPTS = ("extract-jsonl.py",)
REMOVED_PORTABLE_ONLY_ARTIFACTS = ("manifest.py",)


@unittest.skipIf(tomllib is None, "tomllib requires Python 3.11+")
class ScriptsPackagingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())

    def test_removed_scheduler_artifacts_are_absent_from_source(self) -> None:
        for name in REMOVED_SCHEDULER_ARTIFACTS:
            with self.subTest(script=name):
                self.assertFalse((SCRIPTS_DIR / name).exists(), name)

    def test_generic_scripts_remain_in_source(self) -> None:
        for name in GENERIC_SCRIPTS:
            with self.subTest(script=name):
                self.assertTrue((SCRIPTS_DIR / name).is_file(), name)

    def test_manifest_v1_script_is_absent_from_source(self) -> None:
        for name in REMOVED_PORTABLE_ONLY_ARTIFACTS:
            self.assertFalse((SCRIPTS_DIR / name).exists(), name)

    def test_source_scripts_have_no_removed_scheduler_contract(self) -> None:
        contents = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in sorted(SCRIPTS_DIR.iterdir())
            if path.is_file()
        ).lower()
        self.assertNotIn("launchd", contents)
        self.assertNotIn("memory-bridge", contents)

    def test_wheel_has_no_source_script_or_environment_force_includes(self) -> None:
        wheel = self.pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]
        self.assertNotIn("force-include", wheel)
        self.assertFalse((ROOT / ".env.example").exists())

    def test_scripts_dir_not_excluded_from_sdist(self) -> None:
        # The sdist retains genuinely generic source-maintenance utilities even
        # though they are not runtime package data in the wheel.
        sdist_exclude = self.pyproject["tool"]["hatch"]["build"]["targets"]["sdist"]["exclude"]
        self.assertNotIn("/scripts", sdist_exclude)

    def test_wheel_excludes_pycache_from_force_included_dirs(self) -> None:
        # Package builds must never carry local interpreter caches.
        wheel_exclude = self.pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["exclude"]
        self.assertIn("**/__pycache__", wheel_exclude)

    @unittest.skipUnless(shutil.which("uv"), "uv is required for builds")
    def test_built_wheel_omits_scripts_and_sdist_keeps_only_generic_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            subprocess.run(
                ["uv", "build", "--quiet", "--wheel", "--sdist", "--out-dir", str(output), str(ROOT)],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                timeout=180,
            )
            wheel, = output.glob("*.whl")
            sdist, = output.glob("*.tar.gz")
            with zipfile.ZipFile(wheel) as archive:
                wheel_names = tuple(archive.namelist())
                wheel_scripts = {
                    Path(name).name: archive.read(name)
                    for name in wheel_names
                    if "/_data/scripts/" in name and not name.endswith("/")
                }
            with tarfile.open(sdist) as archive:
                sdist_members = tuple(archive.getmembers())
                sdist_scripts = {
                    Path(member.name).name: archive.extractfile(member).read()
                    for member in sdist_members
                    if member.isfile() and "/scripts/" in member.name
                }

            for name in REMOVED_SCHEDULER_ARTIFACTS:
                self.assertNotIn(name, wheel_scripts)
                self.assertNotIn(name, sdist_scripts)
            for name in REMOVED_PORTABLE_ONLY_ARTIFACTS:
                self.assertNotIn(name, wheel_scripts)
                self.assertNotIn(name, sdist_scripts)
            for name in GENERIC_SCRIPTS:
                self.assertNotIn(name, wheel_scripts)
                self.assertIn(name, sdist_scripts)
            packaged = b"\n".join((*wheel_scripts.values(), *sdist_scripts.values())).lower()
            self.assertNotIn(b"launchd", packaged)
            self.assertNotIn(b"memory-bridge", packaged)


if __name__ == "__main__":
    unittest.main()
