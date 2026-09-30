import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fieldkit.core.host import host  # noqa: E402

windows_only = pytest.mark.skipif(not host()["is_windows"], reason="Windows-only behaviour")
linux_only = pytest.mark.skipif(not host()["is_linux"], reason="Linux-only behaviour")
