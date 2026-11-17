pre-commit: refuses a commit unless `python -m pytest` passes and
`fieldkit privacy scan . --git` finds nothing. Enable after cloning with:
    git config core.hooksPath hooks

post-merge / post-checkout: after `git pull`, a merge or a branch switch, recompile the changed Python files to
bytecode (`fieldkit compileonce build`), so no tool compiles at start-up. Never fail the git command.
