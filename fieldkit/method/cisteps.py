"""fieldkit ci-steps: the CI workflow's job for this OS, run in the current tree. The step runner of
`fieldkit ship ci-local` (fieldkit/method/cilocal.py), sent to a Windows device over the cable as a run job."""
from .cilocal import steps_main as main  # noqa: F401
