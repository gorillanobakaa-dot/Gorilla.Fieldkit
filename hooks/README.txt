pre-commit: refuses a commit unless `python -m pytest` passes and
`fieldkit privacy scan . --git` finds nothing. Enable after cloning with:
    git config core.hooksPath hooks
