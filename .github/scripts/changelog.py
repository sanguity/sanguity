#!/usr/bin/env python3
"""CHANGELOG.md (Keep a Changelog) for the release workflow and CI.

  python3 .github/scripts/changelog.py check          the format: [Unreleased] first, then releases newest
                                                      first, each "## [X.Y.Z] - YYYY-MM-DD" with Upgrade notes
  python3 .github/scripts/changelog.py notes 1.2.0    that release's section, for its GitHub release

Standard library only.
"""
import os, re, sys

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir, "CHANGELOG.md")
HEADING = re.compile(r"^## \[(?P<v>[^\]]+)\](?: - (?P<date>\d{4}-\d{2}-\d{2}))?\s*$")
VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")


def sections():
    """[(version, date, body)] in file order; body without the heading or the link references at the end."""
    out, cur = [], None
    for line in open(PATH, encoding="utf-8").read().splitlines():
        m = HEADING.match(line)
        if m:
            cur = [m.group("v"), m.group("date"), []]
            out.append(cur)
        elif cur is not None and not re.match(r"^\[[^\]]+\]: \S+$", line):
            cur[2].append(line)
    return [(v, d, "\n".join(b).strip()) for v, d, b in out]


def key(v):
    major, minor, patch, pre = VERSION.match(v).groups()
    return (int(major), int(minor), int(patch), pre is None, pre or "")


def check():
    errors, s = [], sections()
    if not s or s[0][0] != "Unreleased":
        errors.append("the first section must be ## [Unreleased]")
    released = s[1:] if s and s[0][0] == "Unreleased" else s
    for v, d, body in released:
        if not VERSION.match(v):
            errors.append(f"[{v}] isn't a version (MAJOR.MINOR.PATCH, optionally -PRERELEASE)")
            continue
        if not d:
            errors.append(f"[{v}] has no date: ## [{v}] - YYYY-MM-DD")
        if "### Upgrade notes" not in body:
            errors.append(f"[{v}] has no ### Upgrade notes (write \"Nothing to do.\" when there is nothing)")
    versions = [v for v, _, _ in released if VERSION.match(v)]
    if versions != sorted(versions, key=key, reverse=True):
        errors.append("releases must be newest first")
    if len(set(versions)) != len(versions):
        errors.append("a version appears twice")
    for e in errors:
        print(f"CHANGELOG.md: {e}", file=sys.stderr)
    return 1 if errors else 0


def notes(version):
    for v, _, body in sections():
        if v == version:
            if not body:
                break
            print(body)
            return 0
    print(f"CHANGELOG.md has no notes for {version}: add a \"## [{version}] - YYYY-MM-DD\" section (move what's under "
          "[Unreleased] into it) before tagging", file=sys.stderr)
    return 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["check"]:
        sys.exit(check())
    if sys.argv[1:2] == ["notes"] and len(sys.argv) == 3:
        sys.exit(notes(sys.argv[2]))
    sys.exit(__doc__)
