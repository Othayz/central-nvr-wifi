#!/usr/bin/env python3
"""
Testes dos workflows do GitHub Actions (Issue #39).
Garantem que a suíte inteira roda em PR/push e na release, e que o
tarball de fonte é gerado com `git archive` (sem caches locais).
"""
import unittest
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


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


if __name__ == "__main__":
    unittest.main()
