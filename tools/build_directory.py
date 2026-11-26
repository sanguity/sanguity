#!/usr/bin/env python3
"""Build the "Find your provider" directory (#93) on the host, from the vendors' published lists.

An admin adds and updates Epic's and Oracle Health's lists from the page: Settings → Install → Provider directory
(#992), where the server downloads them itself. This is the same build from the command line, for a server that
can't reach open.epic.com and raw.githubusercontent.com (download the two files elsewhere and pass them with
--from), and for maintainers. The code is server/directory/build.py's; this only adds the downloads. Standard
library only, so it runs on the host without the app's dependencies.

  python3 tools/build_directory.py                 # fetch every list; write data/directory/providers.json.gz
  python3 tools/build_directory.py --bundled       # rewrite server/directory/providers.json (no network)
  python3 tools/build_directory.py --from epic-brands=brands.json --from oracle-millennium=oracle.json --out x.json.gz

`--out` defaults to $DATA_DIR/directory/providers.json.gz (DATA_DIR as in .env, else ./data), where the app looks
for it (SANGUITY_DATA inside the container). The app picks up a new file on the next search; no restart needed.
Each list's source and terms: server/directory/build.py.
"""
import json, os, sys, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server.directory.build import *   # noqa: E402,F401,F403  (the readers, build and write, for tests too)
from server.directory import build as _build   # noqa: E402


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": _build.UA, "Accept": "application/fhir+json, application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def main(argv=None):
    _build.main(argv, fetch=fetch, prog="tools/build_directory.py")


if __name__ == "__main__":
    main()
