import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fieldkit.core.host import host  # noqa: E402

windows_only = pytest.mark.skipif(not host()["is_windows"], reason="Windows-only behaviour")
linux_only = pytest.mark.skipif(not host()["is_linux"], reason="Linux-only behaviour")


# Tests never write into the real MCP flight recorder (subprocesses inherit this too).
import os as _os
import tempfile as _tempfile

# The pre-commit hook runs the tests with GIT_INDEX_FILE / GIT_DIR set. Any test that runs git in a temporary
# repository would then read and WRITE Fieldkit's own index (2026-10-01: foreign files ended up in it and the next
# commit was refused). Tests must never inherit git's repository-selecting variables.
for _k in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_PREFIX", "GIT_OBJECT_DIRECTORY",
           "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE",
           "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_AUTHOR_DATE", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL",
           "GIT_COMMITTER_DATE"):
    _os.environ.pop(_k, None)
_os.environ["FIELDKIT_RECORDER"] = _os.path.join(_tempfile.mkdtemp(prefix="fieldkit-test-recorder-"), "mcp.jsonl")
_os.environ["FIELDKIT_OFFICE_BACKUPS"] = _tempfile.mkdtemp(prefix="fieldkit-test-office-backups-")
