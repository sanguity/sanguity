#!/usr/bin/env python3
"""(#1158) Every environment variable the server, the receiver and docker/ read is either a setting the page owns (the
settings file is its source of truth: the environment's value is taken in once, on the first start of this version)
or a container-level one in server/config.py's CONTAINER, with why it can't be a setting. The code is read for every
os.environ / os.getenv read, so a new one fails here until it's one or the other.

  python3 tests/test_env_audit.py
"""
import os, re, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="sanguity-env-audit-")
os.environ.update(SANGUITY_DATA=TMP, SANGUITY_KEY_FILE=os.path.join(TMP, "data_key"))
sys.path.insert(0, ROOT)

from server import config, notify, oidc, server_settings, settings   # noqa: E402

FOLDERS = ("server", "watch", "docker")
NAME = r"""["']([A-Z][A-Z0-9_]*)["']"""
LITERAL = [re.compile(p) for p in (r"os\.environ\.get\(\s*" + NAME, r"os\.environ\[\s*" + NAME + r"\s*\]",
                                   r"os\.getenv\(\s*" + NAME, r"os\.environ\.setdefault\(\s*" + NAME,
                                   NAME + r"\s+(?:not\s+)?in\s+os\.environ\b")]
# a read whose name isn't written out: each known site, and the names it stands for
DYNAMIC = re.compile(r"os\.(?:environ\.get|environ\[|getenv)\(?\s*([A-Za-z_][\w.]*)")
KNOWN = {
    ("server/config.py", "v.name"): set(config.VARS),   # each of VARS, read by its name
    ("server/server_settings.py", "name"): set(server_settings.DEFS),   # each restart setting, by its name
    ("server/server_settings.py", "n"): set(server_settings.DEFS),
    ("server/settings_import.py", "n"): set(config.VARS) - set(config.CONTAINER) | set(config.SECRETS),   # taken in once
}


def page_owned():
    """The variables whose value is the settings file's: VARS (but the container's), the restart settings, the secrets."""
    return (set(config.VARS) - set(config.CONTAINER)) | set(server_settings.DEFS) | set(config.SECRETS)


def reads():
    """{name: [file:line, ...]} of every variable read, and [(file:line, expression)] of the reads not known."""
    found, unknown = {}, []
    for folder in FOLDERS:
        for top, _, files in os.walk(os.path.join(ROOT, folder)):
            for f in sorted(files):
                if not f.endswith(".py"):
                    continue
                path = os.path.join(top, f)
                rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
                with open(path, encoding="utf-8") as fh:
                    for no, line in enumerate(fh, 1):
                        code = line.split("#", 1)[0] if not line.lstrip().startswith(("'", '"')) else line
                        for p in LITERAL:
                            for m in p.finditer(code):
                                found.setdefault(m.group(1), []).append(f"{rel}:{no}")
                        for m in DYNAMIC.finditer(code):
                            expr = m.group(1)
                            if (rel, expr) in KNOWN:
                                for n in KNOWN[(rel, expr)]:
                                    found.setdefault(n, []).append(f"{rel}:{no}")
                            else:
                                unknown.append((f"{rel}:{no}", expr))
    return found, unknown


class EnvironmentAudit(unittest.TestCase):
    def test_every_variable_read_is_page_owned_or_the_containers(self):
        found, _ = reads()
        owned = page_owned()
        neither = {n: at for n, at in found.items() if n not in owned and n not in config.CONTAINER}
        self.assertEqual(neither, {}, "read in the code, but neither a page setting nor in config.CONTAINER (with why)")

    def test_every_read_names_its_variable(self):
        """A read whose name the code makes up (os.environ.get(x)) is one of the known ones, for a registry's names."""
        _, unknown = reads()
        self.assertEqual(unknown, [], "a variable read by a name that isn't written out: add it to KNOWN with its names")

    def test_the_allowlist_is_read_and_says_why(self):
        found, _ = reads()
        self.assertEqual(sorted(n for n in config.CONTAINER if n not in found), [], "in CONTAINER but read nowhere")
        self.assertEqual(sorted(n for n, why in config.CONTAINER.items() if len(why) < 20), [], "each says why")
        self.assertEqual(sorted(set(config.CONTAINER) & (set(server_settings.DEFS) | set(config.SECRETS))), [],
                         "a page setting can't be the container's too")

    def test_no_setting_is_the_environments_alone(self):
        """(#1158) Each page setting has a home on the page: an install setting that stands for it (applies at once) or
        one of Needs a restart's (server_settings.DEFS); each secret its write-only store."""
        home = {s.env for s in settings.SETTINGS.values() if s.env} | set(server_settings.DEFS)
        self.assertEqual(sorted(n for n in config.VARS if n not in config.CONTAINER and n not in home), [])
        self.assertEqual(sorted(config.RESTART - set(server_settings.DEFS)), [])
        self.assertEqual((oidc.SECRET_ENV in config.SECRETS, notify.TOKEN_ENV in config.SECRETS), (True, True))

    def test_the_receiver_reads_no_setting_of_its_own(self):
        """(#1158) Its time zone and days are the app's copy (receiver-settings.json), never its environment's."""
        found, _ = reads()
        rx = sorted(n for n, at in found.items() if any(a.startswith("watch/") for a in at) and n not in config.CONTAINER)
        self.assertEqual(rx, [])


if __name__ == "__main__":
    unittest.main()
