#!/usr/bin/env python3
"""验证本地链接检查器对Gitlink和普通缺失目标的区分。"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


GITLINK_PATH = ".github/extensions/tmux"
GITLINK_SHA = "0123456789abcdef0123456789abcdef01234567"


def run_git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


def build_fixture(
    artifacts: Path,
    checker: bytes,
    boundaries: bytes,
    name: str,
    target: str,
    *,
    gitlink: bool = False,
    initialized: bool = False,
) -> tuple[Path, Path]:
    repo = artifacts / name
    script = repo / ".github" / "check-local-links.py"
    script.parent.mkdir(parents=True)
    script.write_bytes(checker)
    script.with_name("source_boundaries.py").write_bytes(boundaries)
    (repo / "README.md").write_text("# Fixture\n", encoding="utf-8")
    extensions = repo / ".github" / "extensions"
    extensions.mkdir(parents=True)
    (extensions / "README.md").write_text(f"[target]({target})\n", encoding="utf-8")

    run_git(repo, "init", "--quiet")
    if gitlink:
        run_git(repo, "update-index", "--add", "--cacheinfo", f"160000,{GITLINK_SHA},{GITLINK_PATH}")
    if initialized:
        submodule = repo / GITLINK_PATH
        submodule.mkdir(parents=True)
        (submodule / ".git").write_text("gitdir: ../.git/modules/tmux\n", encoding="utf-8")
    return repo, script


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path)
    args = parser.parse_args()
    checker_path = Path(__file__).with_name("check-local-links.py")
    checker = checker_path.read_bytes()
    boundaries = checker_path.with_name("source_boundaries.py").read_bytes()
    if args.artifacts:
        artifacts = args.artifacts.expanduser().resolve()
        artifacts.mkdir(parents=True, exist_ok=False)
    else:
        artifacts = Path(tempfile.mkdtemp(prefix="local-links-test-"))

    cases = (
        ("uninitialized_gitlink", "tmux/README", True, False, 0),
        ("ordinary_missing_target", "missing.md", False, False, 1),
        ("initialized_gitlink_missing_target", "tmux/README", True, True, 1),
    )
    records = []

    class LocalLinksTest(unittest.TestCase):
        def test_gitlink_and_missing_target_boundaries(self):
            for name, target, gitlink, initialized, expected_exit in cases:
                with self.subTest(case=name):
                    repo, script = build_fixture(
                        artifacts,
                        checker,
                        boundaries,
                        name,
                        target,
                        gitlink=gitlink,
                        initialized=initialized,
                    )
                    command = [sys.executable, str(script)]
                    completed = subprocess.run(
                        command,
                        cwd=repo,
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    (artifacts / f"{name}.stdout.log").write_text(completed.stdout, encoding="utf-8")
                    (artifacts / f"{name}.stderr.log").write_text(completed.stderr, encoding="utf-8")
                    records.append({
                        "case": name,
                        "command": command,
                        "expected_exit_code": expected_exit,
                        "actual_exit_code": completed.returncode,
                    })
                    self.assertEqual(completed.returncode, expected_exit, completed.stdout + completed.stderr)
                    if name == "uninitialized_gitlink":
                        self.assertIn("SKIPPED_UNINITIALIZED_GITLINK_TARGETS=1", completed.stdout)
                        self.assertNotIn("MISSING_LINKS", completed.stdout)
                    else:
                        self.assertIn("MISSING_LINKS", completed.stdout)
                        self.assertIn("TOTAL_LINKS=1", completed.stdout)

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(LocalLinksTest)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {
        "success": result.wasSuccessful(),
        "cases": records,
        "checker_sha256": hashlib.sha256(checker).hexdigest(),
        "source_boundaries_sha256": hashlib.sha256(boundaries).hexdigest(),
        "test_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    (artifacts / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"success": report["success"], "cases": len(records), "artifacts": str(artifacts)}, ensure_ascii=False))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
