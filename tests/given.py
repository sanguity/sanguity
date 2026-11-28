"""(#1158) A server setting as a test wants it, the way the app holds it: an install setting's value as saved in the
app, or a restart setting's as the start-up path read it from data/server-settings.json. The environment sets none of
them any more (the settings file is their one source of truth), so a test that patched os.environ uses this instead.

  with given.settings(SESSION_IDLE_MINUTES="1", UPLOAD_MAX_FILES=3, PUBLIC_ORIGINS=""):   # "" or None: its default
"""
import contextlib
from unittest import mock

from server import config, server_settings


@contextlib.contextmanager
def settings(**values):
    saved, restart = {}, {}
    for name, v in values.items():
        var = config.VARS[name]
        if isinstance(v, str):
            v = var.read(v.strip()) if v.strip() else None
        if var.setting:
            saved[var.setting] = list(v) if isinstance(v, tuple) else v
        else:
            restart[name] = v
    real_saved, real_in_force = config.saved, server_settings.in_force

    def have():
        out = dict(real_saved() if real_saved else {})
        for k, v in saved.items():
            out.pop(k, None) if v is None else out.__setitem__(k, v)
        return out

    def in_force():
        out = dict(real_in_force())
        for k, v in restart.items():
            out.pop(k, None) if v is None else out.__setitem__(k, v)
        return out
    with mock.patch.object(config, "saved", have), mock.patch.object(server_settings, "in_force", in_force):
        yield
