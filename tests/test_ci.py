"""Release regressions must be caught before CI receives registry write access."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.ci import changed_paths, plan, publication_tags, validate_release

SHA = "a" * 40
OTHER = "b" * 40
MAIN = "refs/heads/main"
ROOT = Path(__file__).resolve().parents[1]


class PlanTests(unittest.TestCase):
    def test_docs_only_keep_checks_but_skip_image(self):
        for event, ref in [("push", MAIN), ("pull_request", "refs/pull/1/merge")]:
            with self.subTest(event=event):
                self.assertEqual(
                    plan(event, ref, False, ["README.md", "docs/deployment.md"]),
                    {"build": False, "publish": False},
                )

    def test_runtime_workflow_and_nginx_changes_build(self):
        for path in [
            "submerge/server.py",
            ".github/workflows/ci.yml",
            "deploy/nginx-submerge.conf",
        ]:
            with self.subTest(path=path):
                self.assertEqual(
                    plan("push", MAIN, False, ["README.md", path]),
                    {"build": True, "publish": True},
                )

    def test_pr_never_publishes(self):
        self.assertEqual(
            plan("pull_request", "refs/pull/1/merge", True, ["Containerfile"]),
            {"build": True, "publish": False},
        )

    def test_manual_always_builds_but_publishing_is_opt_in(self):
        for publish in [False, True]:
            with self.subTest(publish=publish):
                self.assertEqual(
                    plan("workflow_dispatch", MAIN, publish, []),
                    {"build": True, "publish": publish},
                )

    def test_manual_feature_branch_can_only_validate(self):
        ref = "refs/heads/feature"
        self.assertFalse(plan("workflow_dispatch", ref, False, [])["publish"])
        with self.assertRaises(ValueError):
            plan("workflow_dispatch", ref, True, [])

    def test_release_builds_even_after_docs_only_change(self):
        self.assertEqual(
            plan("push", "refs/tags/v1.2.3", False, ["README.md"]),
            {"build": True, "publish": True},
        )

    def test_unknown_changes_build_conservatively(self):
        self.assertTrue(plan("push", MAIN, False, None)["build"])
        with patch("scripts.ci.git", side_effect=subprocess.CalledProcessError(128, "git")):
            self.assertIsNone(changed_paths("push", {"before": OTHER}))
        self.assertIsNone(changed_paths("push", {"before": "0" * 40}))

    def test_docs_rename_cannot_hide_runtime_deletion(self):
        with patch("scripts.ci.git", return_value="submerge/server.py\0docs/old.py\0") as git:
            paths = changed_paths("push", {"before": OTHER})
        self.assertIn("--no-renames", git.call_args.args)
        self.assertTrue(plan("push", MAIN, False, paths)["build"])

    def test_empty_diff(self):
        with patch("scripts.ci.git", return_value=""):
            self.assertEqual(changed_paths("push", {"before": OTHER}), [])


class PublicationTests(unittest.TestCase):
    def test_main_can_only_advance_to_current_head(self):
        self.assertEqual(publication_tags(MAIN, SHA, {MAIN: SHA}), [f"sha-{SHA}", "main"])
        self.assertEqual(publication_tags(MAIN, SHA, {MAIN: OTHER}), [f"sha-{SHA}"])

    def test_latest_release_updates_stable_with_numeric_order(self):
        refs = {"refs/tags/v1.9.0": OTHER, "refs/tags/v1.10.0": SHA}
        self.assertEqual(
            publication_tags("refs/tags/v1.10.0", SHA, refs),
            [f"sha-{SHA}", "v1.10.0", "stable"],
        )

    def test_old_release_cannot_roll_back_stable(self):
        refs = {"refs/tags/v1.9.0": SHA, "refs/tags/v1.10.0": OTHER}
        self.assertEqual(publication_tags("refs/tags/v1.9.0", SHA, refs), [f"sha-{SHA}", "v1.9.0"])

    def test_prerelease_neither_updates_nor_blocks_stable(self):
        refs = {"refs/tags/v1.0.0": SHA, "refs/tags/v2.0.0-rc.1": OTHER}
        self.assertIn("stable", publication_tags("refs/tags/v1.0.0", SHA, refs))
        self.assertNotIn("stable", publication_tags("refs/tags/v2.0.0-rc.1", OTHER, refs))

    def test_annotated_tag_uses_peeled_commit(self):
        refs = {"refs/tags/v1.0.0": OTHER, "refs/tags/v1.0.0^{}": SHA}
        self.assertIn("stable", publication_tags("refs/tags/v1.0.0", SHA, refs))

    def test_removed_or_moved_release_is_rejected(self):
        for refs in [{}, {"refs/tags/v1.0.0": OTHER}]:
            with self.subTest(refs=refs), self.assertRaises(ValueError):
                publication_tags("refs/tags/v1.0.0", SHA, refs)

    def test_malformed_or_registry_incompatible_tags_are_rejected(self):
        for tag in ["v1", "v01.2.3", "v1.2.3-01", "v1.2.3-rc..1", "v1.2.3+build", "v1.2.3/x"]:
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                validate_release(tag)

    def test_valid_prerelease_tags(self):
        for tag in ["v0.0.0", "v1.2.3-rc.1", "v1.2.3-0", "v1.2.3-01alpha"]:
            with self.subTest(tag=tag):
                validate_release(tag)

    def test_feature_branch_cannot_publish(self):
        with self.assertRaises(ValueError):
            publication_tags("refs/heads/feature", SHA, {})


class CommandTests(unittest.TestCase):
    def test_release_command_reads_remote_annotated_tag_and_normalizes_image(self):
        with tempfile.TemporaryDirectory() as directory:

            def git(*args):
                return subprocess.check_output(
                    ["git", "-C", directory, *args], text=True, stderr=subprocess.PIPE
                ).strip()

            git("init", "-q", "--initial-branch=main")
            identity = ["-c", "user.name=CI", "-c", "user.email=ci@example.invalid"]
            git(*identity, "commit", "--allow-empty", "-qm", "fixture")
            git(*identity, "tag", "-a", "v1.2.3", "-m", "fixture")
            git("remote", "add", "origin", directory)
            sha = git("rev-parse", "HEAD")
            output = Path(directory) / "output"
            env = {
                **os.environ,
                "GITHUB_REF": "refs/tags/v1.2.3",
                "GITHUB_SHA": sha,
                "GITHUB_REPOSITORY_OWNER": "ExampleOwner",
                "GITHUB_REPOSITORY": "ExampleOwner/ForkName",
                "GITHUB_OUTPUT": str(output),
            }
            cmd = [sys.executable, str(ROOT / "scripts/ci.py"), "tags"]
            subprocess.run(cmd, env=env, cwd=directory, check=True, capture_output=True)
            self.assertEqual(
                output.read_text(),
                f"tags=sha-{sha} v1.2.3 stable\nowner=exampleowner\nname=forkname\n",
            )
            # A failed remote query must not guess release tags or grant publication.
            output.write_text("")
            git("remote", "set-url", "origin", str(Path(directory) / "missing"))
            result = subprocess.run(cmd, env=env, cwd=directory, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(output.read_text(), "")

    def test_manual_plan_writes_github_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = Path(directory) / "event.json"
            output = Path(directory) / "output"
            payload.write_text(json.dumps({"inputs": {"publish": True}}))
            env = {
                **os.environ,
                "GITHUB_REF": MAIN,
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "GITHUB_EVENT_PATH": str(payload),
                "GITHUB_OUTPUT": str(output),
                "PUBLISH": "true",
            }
            subprocess.run(
                [sys.executable, str(ROOT / "scripts/ci.py"), "plan"],
                env=env,
                check=True,
                capture_output=True,
            )
            self.assertEqual(output.read_text(), "build=true\npublish=true\n")
