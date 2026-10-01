"""Build harness: vault, checked steps, checkpoints, Firefox workflow, recorder, audit, preflight, compile gate.

Every git command in this package names its repository with `-C`. If the process was started by a git hook (the
pre-commit hook runs the tests), git has exported GIT_INDEX_FILE / GIT_DIR and friends, which would silently make
every `git -C <other repo>` read the WRONG repository (a vault looked 'damaged', a working copy looked 'dirty').
So they are removed once, here, before anything runs.
"""
import os

for _k in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_PREFIX", "GIT_OBJECT_DIRECTORY",
           "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE",
           "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_AUTHOR_DATE", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL",
           "GIT_COMMITTER_DATE"):
    os.environ.pop(_k, None)
