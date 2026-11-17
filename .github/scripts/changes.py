"""Which of check.yml's jobs a pull request's changes can affect.

  python3 -I .github/scripts/changes.py [--all] < changed-paths    # one path per line, relative to the repo

Prints key=value lines for $GITHUB_OUTPUT:

  app        the tests (the check job): anything but documentation, or only the install templates
  image      the image jobs (docker, release-image, upgrade): what goes into the image or the compose files, so
             not documentation, the templates, tests or tools
  templates  the templates job: the install templates, the compose files and the image's docker/ folder

--all (a push to main, a release, a change to CI itself) says true to everything. A path that matches no rule
counts as code, so anything new is tested until a rule says otherwise.
"""
import fnmatch, sys

DOCS = ["README.md", "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md", "LICENSE", "LICENSE.*",
        "docs/*", ".github/ISSUE_TEMPLATE/*", ".github/pull_request_template.md", ".github/FUNDING.yml"]
CI = [".github/workflows/*", ".github/scripts/*"]   # CI itself: everything runs
TEMPLATES = ["deploy/*"]
TEMPLATE_INPUTS = ["compose.yaml", "compose.release.yaml", "docker/*"]   # what the templates are checked against
NOT_IN_IMAGE = ["tests/*", "tools/*", "deploy/*", ".github/dependabot.yml", ".gitignore", ".gitattributes"]
IMAGE_TESTS = ["tests/docker_smoke.py"]   # what the image jobs run against the image


def matches(path, patterns):
    return any(fnmatch.fnmatch(path, p) for p in patterns)


def classify(paths, everything=False):
    paths = [p.strip() for p in paths if p.strip()]
    if everything or not paths or any(matches(p, CI) for p in paths):
        return {"app": True, "image": True, "templates": True}
    code = [p for p in paths if not matches(p, DOCS)]
    return {
        "app": any(not matches(p, TEMPLATES) for p in code),
        "image": any(not matches(p, NOT_IN_IMAGE) or matches(p, IMAGE_TESTS) for p in code),
        "templates": any(matches(p, TEMPLATES + TEMPLATE_INPUTS) for p in code),
    }


if __name__ == "__main__":
    out = classify(sys.stdin.read().splitlines(), "--all" in sys.argv[1:])
    for k, v in out.items():
        print(f"{k}={'true' if v else 'false'}")
