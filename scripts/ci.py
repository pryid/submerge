"""CI decisions, kept separate from the workflow so release policy can be tested."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

NUMBER = r"(?:0|[1-9][0-9]*)"
VERSION = rf"v({NUMBER})\.({NUMBER})\.({NUMBER})"
PRERELEASE_ID = rf"(?:{NUMBER}|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
RELEASE = re.compile(rf"{VERSION}(?:-{PRERELEASE_ID}(?:\.{PRERELEASE_ID})*)?")


def git(*args):
    return subprocess.check_output(["git", *args], text=True)


def version(tag):
    match = re.fullmatch(VERSION, tag)
    return tuple(map(int, match.groups())) if match else None


def validate_release(tag):
    if len(tag) > 128 or not RELEASE.fullmatch(tag):
        raise ValueError(f"Unsupported release tag: {tag!r}; use vX.Y.Z or vX.Y.Z-rc.N")


def plan(event, ref, requested, changed):
    """None means unknown changes: build conservatively. Manual/tag runs always build."""
    if event == "push" and not ref.startswith("refs/tags/"):
        return {"build": False, "publish": False}
    publish = event == "push" or (event == "workflow_dispatch" and requested)
    if publish:
        if ref.startswith("refs/tags/"):
            validate_release(ref.removeprefix("refs/tags/"))
        elif ref != "refs/heads/main":
            raise ValueError("Publication is only allowed from main or a release tag")
    build = (
        event == "workflow_dispatch"
        or ref.startswith("refs/tags/")
        or changed is None
        or any(path != "README.md" and not path.startswith("docs/") for path in changed)
    )
    return {"build": build, "publish": publish and build}


def publication_tags(ref, sha, refs):
    """Call inside the publication lock using fresh remote refs, never checkout tags."""
    tags = [f"sha-{sha}"]
    if ref == "refs/heads/main":
        if refs.get(ref) == sha:
            tags.append("main")
    elif ref.startswith("refs/tags/"):
        tag = ref.removeprefix("refs/tags/")
        validate_release(tag)
        if refs.get(ref + "^{}", refs.get(ref)) != sha:
            raise ValueError("Release tag was removed or moved after this run started")
        tags.append(tag)
        releases = [
            parsed
            for name in refs
            if name.startswith("refs/tags/")
            and (parsed := version(name.removeprefix("refs/tags/"))) is not None
        ]
        if version(tag) is not None and version(tag) == max(releases):
            tags.append("stable")
    else:
        raise ValueError("Publication is only allowed from main or a release tag")
    return tags


def changed_paths(event, payload):
    if event == "pull_request":
        base = payload["pull_request"]["base"]["sha"]
        comparison = f"{base}...HEAD"
    elif event == "push" and payload.get("before", "0" * 40) != "0" * 40:
        comparison = f"{payload['before']}..HEAD"
    else:
        return None
    try:
        paths = git("diff", "--no-renames", "--name-only", "-z", comparison, "--")
        return [path for path in paths.split("\0") if path]
    except subprocess.CalledProcessError:
        # Initial/force pushes may lack the old commit even with full checkout.
        return None


def main():
    ref = os.environ["GITHUB_REF"]
    if sys.argv[1] == "plan":
        event = os.environ["GITHUB_EVENT_NAME"]
        payload = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        result = plan(
            event, ref, os.environ.get("PUBLISH") == "true", changed_paths(event, payload)
        )
    elif sys.argv[1] == "tags":
        remote = git("ls-remote", "origin", "refs/heads/main", "refs/tags/v*")
        refs = {name: sha for sha, name in (line.split() for line in remote.splitlines())}
        result = {
            "tags": " ".join(publication_tags(ref, os.environ["GITHUB_SHA"], refs)),
            "owner": os.environ["GITHUB_REPOSITORY_OWNER"].lower(),
            "name": os.environ["GITHUB_REPOSITORY"].split("/", 1)[1].lower(),
        }
    else:
        raise ValueError("Expected plan or tags")
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        for key, value in result.items():
            text = str(value).lower() if isinstance(value, bool) else value
            print(f"{key}={text}", file=output)
            print(f"{key}={text}")


if __name__ == "__main__":
    main()
