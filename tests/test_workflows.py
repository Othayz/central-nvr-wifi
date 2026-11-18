#!/usr/bin/env python3
"""
Testes dos workflows do GitHub Actions e da cadeia de build.
- #39: a suíte inteira roda em PR/push e na release; tarball de fonte via `git archive`.
- #20: actions fixadas por SHA, build sem permissão de escrita, downloads conferidos por SHA-256
  e dependências instaladas de um lockfile com hashes.
"""
import re
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
