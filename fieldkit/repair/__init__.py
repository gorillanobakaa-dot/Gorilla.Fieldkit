"""Small repair and check tools ported from Kiro's extraction (P18 distill, idea I-19).

One command group: `fieldkit repair ACTION ...`. Each module here has `main(argv) -> int` and no import-time work.
Exit codes: 0 fine, 1 error, 3 findings. Every tool ends with a `NEXT:` line. Tools that change a file do it only
with --apply, write through a temporary file, and keep the file's own line endings.
"""
ACTIONS = {
    "cp1252-stdout-fix": "cp1252_stdout_fix",
    "missing-import-fix": "missing_import_fix",
    "keep-both": "keep_both",
    "brace-parity-check": "brace_parity_check",
    "pwsh-syntax-check": "pwsh_syntax_check",
    "pwsh-parent-chains": "pwsh_parent_chains",
    "pid-guarded-stop": "pid_guarded_stop",
    "worktree-classify-recycle": "worktree_classify_recycle",
    "plan-row-insert": "plan_row_insert",
    "evidence-macho-extract": "evidence_macho_extract",
    "tpfan-config-verify": "tpfan_config_verify",
}


def run(action, argv):
    import importlib
    return importlib.import_module(f"{__name__}.{ACTIONS[action]}").main(list(argv))
