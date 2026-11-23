#!/usr/bin/env python3
"""
Testes dos workflows do GitHub Actions e da cadeia de build.
- #39: a suíte inteira roda em PR/push e na release; tarball de fonte via `git archive`.
- #20: actions fixadas por SHA, build sem permissão de escrita, downloads conferidos por SHA-256
  e dependências instaladas de um lockfile com hashes.
- #73: tags de pré-release (com `-`) são marcadas como `prerelease` e não viram `latest`.
- #76: create_release.sh aborta com stage/arquivos de versão sujos e commita só os dois arquivos.
"""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


class WorkflowsTest(unittest.TestCase):
    def test_ci_runs_full_suite_on_pull_request(self):
        ci = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("pull_request:", ci)
        self.assertIn("python -m pytest tests -q", ci)

    def test_release_runs_full_suite(self):
        release = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
        self.assertIn("python -m pytest tests -q", release)
        # Não pode voltar a listar arquivos de teste avulsos
        self.assertNotIn("pytest tests/test_", release)

    def test_release_source_archive_uses_git_archive(self):
        release = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
        self.assertIn("git archive", release)
        self.assertNotIn("tar --exclude", release)

    def test_release_marks_prerelease_tags_and_protects_latest(self):
        """Issue #73: tags com '-' viram pré-release e não substituem /releases/latest."""
        release = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
        _, gh_release_step = release.split("softprops/action-gh-release@", 1)
        self.assertIn("prerelease: ${{ contains(github.ref_name, '-') }}", gh_release_step)
        self.assertIn("make_latest: ${{ !contains(github.ref_name, '-') }}", gh_release_step)


class CreateReleaseScriptTest(unittest.TestCase):
    """Issue #76: create_release.sh não inclui arquivos estranhos do stage nem sobrescreve arquivos sujos."""

    SCRIPT = ROOT / "scripts" / "create_release.sh"

    @staticmethod
    def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )

    def _init_temp_repo(self, repo: Path) -> None:
        (repo / "src" / "central_nvr").mkdir(parents=True)
        (repo / "src" / "central_nvr" / "__init__.py").write_text(
            '__version__ = "1.3.2"\n',
            encoding="utf-8",
        )
        (repo / "pyproject.toml").write_text(
            '[project]\nname = "central-nvr"\nversion = "1.3.2"\n',
            encoding="utf-8",
        )
        (repo / "README.md").write_text("# Central NVR\n", encoding="utf-8")

        self._git(repo, "init")
        self._git(repo, "config", "user.name", "Teste")
        self._git(repo, "config", "user.email", "teste@example.com")
        self._git(repo, "config", "commit.gpgsign", "false")
        self._git(repo, "config", "tag.gpgSign", "false")
        self._git(repo, "add", "src/central_nvr/__init__.py", "pyproject.toml", "README.md")
        self._git(repo, "commit", "-m", "commit inicial")

    def test_aborts_when_another_file_is_staged(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._init_temp_repo(repo)
            (repo / "extra.txt").write_text("trabalho em andamento\n", encoding="utf-8")
            self._git(repo, "add", "extra.txt")

            proc = subprocess.run(
                ["bash", str(self.SCRIPT), "1.4.0", "teste"],
                cwd=repo,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("stage", (proc.stdout + proc.stderr).lower())
            commits = self._git(repo, "rev-list", "--count", "HEAD").stdout.strip()
            self.assertEqual(commits, "1")
            tags = self._git(repo, "tag", "--list").stdout.strip()
            self.assertEqual(tags, "")
            self.assertIn('version = "1.3.2"', (repo / "pyproject.toml").read_text(encoding="utf-8"))
            self.assertIn('__version__ = "1.3.2"', (repo / "src" / "central_nvr" / "__init__.py").read_text(encoding="utf-8"))

    def test_aborts_when_version_file_has_unstaged_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._init_temp_repo(repo)
            (repo / "pyproject.toml").write_text(
                '[project]\nname = "central-nvr"\nversion = "1.3.2"\n# alteração local\n',
                encoding="utf-8",
            )

            proc = subprocess.run(
                ["bash", str(self.SCRIPT), "1.4.0", "teste"],
                cwd=repo,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(proc.returncode, 0)
            commits = self._git(repo, "rev-list", "--count", "HEAD").stdout.strip()
            self.assertEqual(commits, "1")
            tags = self._git(repo, "tag", "--list").stdout.strip()
            self.assertEqual(tags, "")

    def test_clean_tree_commits_only_version_files_and_creates_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._init_temp_repo(repo)
            # Alteração fora do stage em outro arquivo não deve entrar no commit de release
            (repo / "README.md").write_text("# Central NVR alterado\n", encoding="utf-8")

            proc = subprocess.run(
                ["bash", str(self.SCRIPT), "1.4.0-rc1", "Pré-release de teste"],
                cwd=repo,
                capture_output=True,
                text=True,
            )

            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            commits = self._git(repo, "rev-list", "--count", "HEAD").stdout.strip()
            self.assertEqual(commits, "2")
            changed = {
                line
                for line in self._git(repo, "show", "--pretty=format:", "--name-only", "HEAD").stdout.splitlines()
                if line.strip()
            }
            self.assertEqual(changed, {"pyproject.toml", "src/central_nvr/__init__.py"})
            tags = self._git(repo, "tag", "--list").stdout.splitlines()
            self.assertEqual(tags, ["v1.4.0-rc1"])
            self.assertIn('version = "1.4.0-rc1"', (repo / "pyproject.toml").read_text(encoding="utf-8"))
            self.assertIn(
                '__version__ = "1.4.0-rc1"',
                (repo / "src" / "central_nvr" / "__init__.py").read_text(encoding="utf-8"),
            )


class SupplyChainTest(unittest.TestCase):
    """Issue #20: integridade das dependências baixadas no build de release."""

    def test_actions_pinned_by_commit_sha(self):
        for wf in WORKFLOWS.glob("*.yml"):
            for ref in re.findall(r"uses:\s*(\S+)", wf.read_text(encoding="utf-8")):
                self.assertRegex(ref, r"@[0-9a-f]{40}$", f"{wf.name}: {ref} não está fixada por SHA")

    def test_build_job_has_no_write_permission(self):
        release = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
        top, jobs = release.split("\njobs:", 1)
        self.assertIn("contents: read", top)
        build, publish = jobs.split("\n  publish:", 1)
        self.assertNotIn("contents: write", build)
        self.assertIn("contents: write", publish)
        self.assertIn("needs: build", publish)
        self.assertNotIn("pip install", publish)

    def test_release_installs_with_hashes(self):
        release = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
        for line in release.splitlines():
            if "pip install" in line and "--upgrade pip" not in line:
                self.assertIn("--require-hashes", line, line.strip())
        self.assertIn("requirements.lock", release)
        self.assertIn("requirements-test.lock", release)
        self.assertIn("--hash=sha256:", (ROOT / "requirements-test.lock").read_text(encoding="utf-8"))

    def test_build_script_verifies_downloads(self):
        script = (ROOT / "packaging" / "build_appimage.sh").read_text(encoding="utf-8")
        self.assertRegex(script, r'PYTHON_SHA256="[0-9a-f]{64}"')
        self.assertRegex(script, r'APPIMAGETOOL_SHA256="[0-9a-f]{64}"')
        self.assertIn('verify_sha256 "${CACHE_DIR}/${PYTHON_TARBALL}" "${PYTHON_SHA256}"', script)
        self.assertIn('verify_sha256 "${APPIMAGETOOL}" "${APPIMAGETOOL_SHA256}"', script)
        self.assertNotIn("continuous", script)
        self.assertNotIn("AppImageKit", script)
        self.assertIn("--require-hashes", script)
        self.assertNotIn("rsync", script)  # nada copiado de um venv local fora do lockfile

    def test_lockfile_pins_every_dependency_with_hashes(self):
        lock = (ROOT / "requirements.lock").read_text(encoding="utf-8")
        pinned = dict(re.findall(r"^([A-Za-z0-9_.\-]+)==(\S+)", lock, re.MULTILINE))
        self.assertTrue(pinned)
        # Cada pacote fixado tem ao menos um hash
        for block in re.split(r"\n(?=[A-Za-z0-9])", lock.split("\n", 6)[-1]):
            if "==" in block.splitlines()[0]:
                self.assertIn("--hash=sha256:", block, block.splitlines()[0])
        # Toda dependência declarada no pyproject está no lockfile
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        deps = re.search(r"dependencies = \[(.*?)\]", pyproject, re.DOTALL).group(1)
        names = set()
        for line in deps.splitlines():
            line = line.strip().strip(",")
            if not line or line.startswith("#"):
                continue
            spec = line.strip('"').split(";")[0]
            names.add(re.split(r"[<>=!~ ]", spec)[0].lower())
        lowered = {k.lower() for k in pinned}
        for name in names:
            self.assertIn(name, lowered, f"{name} ausente do requirements.lock")

    def test_dependabot_covers_pip_and_actions(self):
        cfg = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        self.assertIn('package-ecosystem: "pip"', cfg)
        self.assertIn('package-ecosystem: "github-actions"', cfg)
        # Não propor de novo o PySide6 6.12.x, que quebra o app em Python < 3.12 (PR #51)
        self.assertRegex(cfg, r'dependency-name: "pyside6"\s+versions: \[">=6\.12\.0, <6\.13\.0"\]')


if __name__ == "__main__":
    unittest.main()
