#!/usr/bin/env python3
"""Rebuild common.txt.gz, the list of breached passwords that new passwords are checked against.

  python3 server/passwords/update.py [COMMIT]

Source: SecLists (MIT licence), Passwords/Common-Credentials/Pwdb_top-1000000.txt: the million most common
passwords in public breach compilations. Kept: entries of 8 characters or more (the shortest password Sanguity
ever accepts), lower-cased, sorted and de-duplicated, one per line, gzipped reproducibly. Pass a SecLists commit to
pin the source; the one used is recorded in README.md.
"""
import gzip, io, os, sys, urllib.request

COMMIT = sys.argv[1] if len(sys.argv) > 1 else "180c2d55afa7aab8b18b7552f82dd0c92cc4ede5"
URL = f"https://raw.githubusercontent.com/danielmiessler/SecLists/{COMMIT}/Passwords/Common-Credentials/Pwdb_top-1000000.txt"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "common.txt.gz")

with urllib.request.urlopen(URL, timeout=120) as r:
    words = r.read().decode("utf-8", "replace").splitlines()
keep = sorted({w.strip().lower() for w in words if len(w.strip()) >= 8 and w.strip().isprintable() and "\n" not in w})
buf = io.BytesIO()
with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, compresslevel=9) as g:   # same input, same bytes
    g.write(("\n".join(keep) + "\n").encode("utf-8"))
with open(OUT, "wb") as f:
    f.write(buf.getvalue())
print(f"{OUT}: {len(keep)} passwords from SecLists {COMMIT[:12]}")
