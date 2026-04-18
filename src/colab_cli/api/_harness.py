"""Remote harness for the @remote decorator.

Loads a pickled callable + args on the Colab runtime, executes, and
writes a pickled result envelope back to disk. The caller pulls the
envelope via the Jupyter contents API and unpickles it locally.
"""

from __future__ import annotations

DONE_MARKER = "__COLAB_CLI_DONE__"

REMOTE_HARNESS_TEMPLATE = r"""
import os, traceback, pickle
try:
    import cloudpickle as _pickle
except ImportError:
    _pickle = pickle

_SLUG_DIR = {slug_dir!r}
_FN_PATH  = os.path.join(_SLUG_DIR, "fn.pkl")
_ARG_PATH = os.path.join(_SLUG_DIR, "args.pkl")
_OUT_PATH = os.path.join(_SLUG_DIR, "result.pkl")
os.makedirs(_SLUG_DIR, exist_ok=True)

def _colab_cli_run():
    with open(_FN_PATH, "rb") as f:
        fn = _pickle.load(f)
    with open(_ARG_PATH, "rb") as f:
        args, kwargs = _pickle.load(f)
    try:
        value = fn(*args, **kwargs)
    except BaseException as exc:
        tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
        payload = ("err", exc, tb)
    else:
        payload = ("ok", value)
    with open(_OUT_PATH, "wb") as f:
        _pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    print({done_marker!r} + ":" + _OUT_PATH)

_colab_cli_run()
"""


def render_harness(slug_dir: str) -> str:
    return REMOTE_HARNESS_TEMPLATE.format(slug_dir=slug_dir, done_marker=DONE_MARKER)
