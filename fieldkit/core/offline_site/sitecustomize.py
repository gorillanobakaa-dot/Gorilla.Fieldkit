# Version: 1.0.0 · updated 26-10-19
"""Put on PYTHONPATH by fieldkit.core.offline.child_env: a Python child that never imports fieldkit is offline too."""
import os

if os.environ.get("FIELDKIT_OFFLINE") == "1":
    import importlib.util as _u

    _spec = _u.spec_from_file_location("_fieldkit_offline", os.path.join(os.path.dirname(__file__), "..", "offline.py"))
    _m = _u.module_from_spec(_spec)
    _spec.loader.exec_module(_m)
    _m.install()
