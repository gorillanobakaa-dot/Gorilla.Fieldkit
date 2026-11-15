//! Native Rust ports of the 10 remaining Python deterministic validators (Windows & Linux, pure std).

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use crate::common::{
    expand_user_path, parse_frontmatter, parse_json, read_utf8_lossy,
    which_on_path, CheckResult, JsonVal,
};


fn fail_result(finding: String, mut summary: BTreeMap<String, String>) -> CheckResult {
    summary.insert("findings_count".into(), "1".into());
    CheckResult {
        status: "fail",
        summary,
        findings: vec![finding],
        exit_code: 3,
    }
}

fn error_result(msg: String, mut summary: BTreeMap<String, String>) -> CheckResult {
    summary.insert("error".into(), msg.clone());
    summary.insert("findings_count".into(), "1".into());
    CheckResult {
        status: "error",
        summary,
        findings: vec![msg],
        exit_code: 2,
    }
}

// ---------------------------------------------------------------------------
// 1. agy-hooks-validate
// ---------------------------------------------------------------------------

const FLAT_HOOK_EVENTS: &[&str] = &["SessionStart", "PreInvocation", "PostInvocation", "Stop"];
const GROUPED_HOOK_EVENTS: &[&str] = &["PreToolUse", "PostToolUse"];

fn check_hook_handler(val: &JsonVal, ctx: &str, findings: &mut Vec<String>) {
    let Some(obj) = val.as_obj() else {
        findings.push(format!("{ctx}: handler must be an object"));
        return;
    };
    match obj.get("command").and_then(JsonVal::as_str) {
        Some(cmd) if !cmd.trim().is_empty() => {}
        _ => findings.push(format!("{ctx}: handler missing non-empty 'command'")),
    }
    if let Some(t) = obj.get("type") {
        if t.as_str() != Some("command") {
            let shown = t
                .as_str()
                .map(|s| s.to_string())
                .unwrap_or_else(|| t.to_canonical_json());
            findings.push(format!("{ctx}: unsupported handler type '{shown}'"));
        }
    }
    if let Some(timeout) = obj.get("timeout") {
        let valid = timeout.as_f64().map(|n| n.is_finite() && n > 0.0).unwrap_or(false);
        if !valid {
            findings.push(format!("{ctx}: 'timeout' must be a positive number"));
        }
    }
}

/// Validate Antigravity `hooks.json` files against event and handler schema.
pub fn check_hooks(path: &Path) -> CheckResult {
    let mut summary = BTreeMap::new();
    summary.insert("file".into(), path.display().to_string());
    if !path.is_file() {
        return error_result(format!("file not found: {}", path.display()), summary);
    }
    let raw = match read_utf8_lossy(path) {
        Ok(s) => s,
        Err(e) => return error_result(e, summary),
    };
    let root = match parse_json(&raw) {
        Ok(v) => v,
        Err(e) => {
            return fail_result(
                format!("{}: invalid JSON ({e})", path.display()),
                summary,
            )
        }
    };
    let Some(data) = root.as_obj() else {
        return fail_result(
            format!(
                "{}: root must be a JSON object mapping hook names to specs",
                path.display()
            ),
            summary,
        );
    };

    let mut findings = Vec::new();
    for (hook_name, spec_val) in data {
        let Some(spec) = spec_val.as_obj() else {
            findings.push(format!(
                "{}:{hook_name}: hook spec must be an object",
                path.display()
            ));
            continue;
        };
        if let Some(en) = spec.get("enabled") {
            if en.as_bool().is_none() {
                findings.push(format!(
                    "{}:{hook_name}: 'enabled' must be boolean",
                    path.display()
                ));
            }
        }
        for (key, val) in spec {
            if key == "enabled" {
                continue;
            }
            let is_flat = FLAT_HOOK_EVENTS.contains(&key.as_str());
            let is_grouped = GROUPED_HOOK_EVENTS.contains(&key.as_str());
            if !is_flat && !is_grouped {
                findings.push(format!(
                    "{}:{hook_name}: unknown hook event '{key}'",
                    path.display()
                ));
                continue;
            }
            let Some(arr) = val.as_arr() else {
                findings.push(format!(
                    "{}:{hook_name}.{key}: must be a list",
                    path.display()
                ));
                continue;
            };
            if is_flat {
                for (idx, h) in arr.iter().enumerate() {
                    check_hook_handler(
                        h,
                        &format!("{}:{hook_name}.{key}[{idx}]", path.display()),
                        &mut findings,
                    );
                }
            } else {
                for (idx, grp_val) in arr.iter().enumerate() {
                    let gctx = format!("{}:{hook_name}.{key}[{idx}]", path.display());
                    let Some(grp) = grp_val.as_obj() else {
                        findings.push(format!("{gctx}: grouped event entry must be an object"));
                        continue;
                    };
                    match grp.get("matcher").and_then(JsonVal::as_str) {
                        Some(m) if !m.trim().is_empty() => {}
                        _ => findings.push(format!("{gctx}: missing 'matcher' string")),
                    }
                    match grp.get("hooks").and_then(JsonVal::as_arr) {
                        Some(hooks) if !hooks.is_empty() => {
                            for (j, h) in hooks.iter().enumerate() {
                                check_hook_handler(h, &format!("{gctx}.hooks[{j}]"), &mut findings);
                            }
                        }
                        _ => findings.push(format!("{gctx}: 'hooks' must be a non-empty list")),
                    }
                }
            }
        }
    }
    summary.insert("hooks_checked".into(), data.len().to_string());
    CheckResult::from_findings(findings, summary)
}

// ---------------------------------------------------------------------------
// 2. agy-mcp-config-validate
// ---------------------------------------------------------------------------

/// Validate `mcp_config.json` stdio and Streamable HTTP server definitions.
pub fn check_mcp_config(path: &Path, check_bins: bool) -> CheckResult {
    let mut summary = BTreeMap::new();
    summary.insert("file".into(), path.display().to_string());
    if !path.is_file() {
        return error_result(format!("file not found: {}", path.display()), summary);
    }
    let raw = match read_utf8_lossy(path) {
        Ok(s) => s,
        Err(e) => return error_result(e, summary),
    };
    let root = match parse_json(&raw) {
        Ok(v) => v,
        Err(e) => {
            summary.insert("servers".into(), "0".into());
            return fail_result(
                format!("{}: invalid JSON ({e})", path.display()),
                summary,
            );
        }
    };
    let Some(servers) = root.get("mcpServers").and_then(JsonVal::as_obj) else {
        summary.insert("servers".into(), "0".into());
        return fail_result(
            format!("{}: missing 'mcpServers' object", path.display()),
            summary,
        );
    };

    let mut findings = Vec::new();
    for (name, cfg_val) in servers {
        let ctx = format!("{}:mcpServers.{name}", path.display());
        let Some(cfg) = cfg_val.as_obj() else {
            findings.push(format!("{ctx}: server entry must be an object"));
            continue;
        };
        let has_cmd = cfg.contains_key("command");
        let url_val = cfg.get("url").or_else(|| cfg.get("serverUrl"));
        let has_nonempty_url = match url_val {
            Some(JsonVal::Null) | None => false,
            Some(JsonVal::Str(s)) => !s.is_empty(),
            Some(_) => true,
        };
        if has_cmd && has_nonempty_url {
            findings.push(format!("{ctx}: cannot mix Stdio 'command' and HTTP 'url'"));
            continue;
        }
        if !has_cmd && !has_nonempty_url {
            findings.push(format!(
                "{ctx}: must define either Stdio 'command' or Streamable HTTP 'url'"
            ));
            continue;
        }
        if has_cmd {
            match cfg.get("command").and_then(JsonVal::as_str) {
                Some(cmd) if !cmd.trim().is_empty() => {
                    if check_bins && which_on_path(cmd).is_none() && !Path::new(cmd).is_file() {
                        findings.push(format!("{ctx}: executable '{cmd}' not found on PATH"));
                    }
                }
                _ => findings.push(format!("{ctx}: 'command' must be a non-empty string")),
            }
            if let Some(args) = cfg.get("args") {
                let ok = args
                    .as_arr()
                    .map(|v| v.iter().all(|a| a.as_str().is_some()))
                    .unwrap_or(false);
                if !ok {
                    findings.push(format!("{ctx}: 'args' must be a list of strings"));
                }
            }
            if let Some(env_val) = cfg.get("env") {
                let ok = env_val
                    .as_obj()
                    .map(|m| m.values().all(|v| v.as_str().is_some()))
                    .unwrap_or(false);
                if !ok {
                    findings.push(format!(
                        "{ctx}: 'env' must be an object mapping strings to strings"
                    ));
                }
            }
        } else if let Some(uv) = url_val {
            match uv.as_str() {
                Some(url) if url.starts_with("https://") || url.starts_with("http://") => {
                    if url.trim_end_matches('/').ends_with("/sse") {
                        findings.push(format!(
                            "{ctx}: legacy HTTP+SSE '/sse' endpoint is unsupported; use Streamable HTTP (/mcp)"
                        ));
                    }
                }
                _ => findings.push(format!("{ctx}: 'url' must start with http:// or https://")),
            }
        }
    }
    summary.insert("servers".into(), servers.len().to_string());
    CheckResult::from_findings(findings, summary)
}

// ---------------------------------------------------------------------------
// 3. agy-json-config-validate
// ---------------------------------------------------------------------------

/// Validate balanced groups, character classes, escapes, and basic quantifier syntax in regexes.
pub fn validate_regex_syntax(pattern: &str) -> Result<(), String> {
    let chars: Vec<char> = pattern.chars().collect();
    let mut i = 0;
    let mut paren_depth: usize = 0;
    let mut in_class = false;
    let mut class_first = false;
    let mut prev_can_quantify = false;
    let mut prev_was_quantifier = false;

    while i < chars.len() {
        let ch = chars[i];
        if in_class {
            if ch == '\\' {
                if i + 1 >= chars.len() {
                    return Err("bad escape (end of pattern)".into());
                }
                i += 2;
                class_first = false;
                continue;
            }
            if ch == ']' && !class_first {
                in_class = false;
                prev_can_quantify = true;
                prev_was_quantifier = false;
                i += 1;
                continue;
            }
            if ch == '^' && class_first {
                i += 1;
                // '^' alone at class start keeps the immediately following ']' literal too.
                continue;
            }
            class_first = false;
            i += 1;
            continue;
        }

        match ch {
            '\\' => {
                if i + 1 >= chars.len() {
                    return Err("bad escape (end of pattern)".into());
                }
                i += 2;
                prev_can_quantify = true;
                prev_was_quantifier = false;
            }
            '[' => {
                in_class = true;
                class_first = true;
                prev_can_quantify = false;
                prev_was_quantifier = false;
                i += 1;
            }
            '(' => {
                paren_depth += 1;
                i += 1;
                if i < chars.len() && chars[i] == '?' {
                    i += 1;
                    if i >= chars.len() {
                        return Err("unexpected end of pattern after '(?'".into());
                    }
                    if chars[i] == '#' {
                        // Python regex comment (?#...)
                        i += 1;
                        while i < chars.len() && chars[i] != ')' {
                            if chars[i] == '\\' && i + 1 < chars.len() {
                                i += 2;
                            } else {
                                i += 1;
                            }
                        }
                        if i >= chars.len() {
                            return Err("missing ), unterminated comment".into());
                        }
                        paren_depth -= 1;
                        i += 1;
                        prev_can_quantify = false;
                        prev_was_quantifier = false;
                        continue;
                    }
                }
                prev_can_quantify = false;
                prev_was_quantifier = false;
            }
            ')' => {
                if paren_depth == 0 {
                    return Err("unbalanced parenthesis".into());
                }
                paren_depth -= 1;
                prev_can_quantify = true;
                prev_was_quantifier = false;
                i += 1;
            }
            '|' => {
                prev_can_quantify = false;
                prev_was_quantifier = false;
                i += 1;
            }
            '*' | '+' => {
                if !prev_can_quantify {
                    return Err(format!("nothing to repeat before '{ch}'"));
                }
                prev_can_quantify = false;
                prev_was_quantifier = true;
                i += 1;
            }
            '?' => {
                if prev_was_quantifier {
                    prev_was_quantifier = false;
                    prev_can_quantify = false;
                } else if prev_can_quantify {
                    prev_was_quantifier = true;
                    prev_can_quantify = false;
                } else {
                    return Err("nothing to repeat before '?'".into());
                }
                i += 1;
            }
            '{' => {
                // Check strict {m} / {m,} / {m,n} bounds when syntactically a range quantifier
                let mut j = i + 1;
                let mut n1 = String::new();
                while j < chars.len() && chars[j].is_ascii_digit() {
                    n1.push(chars[j]);
                    j += 1;
                }
                if !n1.is_empty() && j < chars.len() && chars[j] == '}' {
                    if !prev_can_quantify {
                        return Err("nothing to repeat before '{m}'".into());
                    }
                    prev_can_quantify = false;
                    prev_was_quantifier = true;
                    i = j + 1;
                    continue;
                }
                if !n1.is_empty() && j < chars.len() && chars[j] == ',' {
                    j += 1;
                    let mut n2 = String::new();
                    while j < chars.len() && chars[j].is_ascii_digit() {
                        n2.push(chars[j]);
                        j += 1;
                    }
                    if j < chars.len() && chars[j] == '}' {
                        if !prev_can_quantify {
                            return Err("nothing to repeat before '{m,n}'".into());
                        }
                        if !n2.is_empty() {
                            if let (Ok(a), Ok(b)) = (n1.parse::<u64>(), n2.parse::<u64>()) {
                                if a > b {
                                    return Err("min repeat greater than max repeat".into());
                                }
                            }
                        }
                        prev_can_quantify = false;
                        prev_was_quantifier = true;
                        i = j + 1;
                        continue;
                    }
                }
                prev_can_quantify = true;
                prev_was_quantifier = false;
                i += 1;
            }
            '^' | '$' => {
                prev_can_quantify = false;
                prev_was_quantifier = false;
                i += 1;
            }
            _ => {
                prev_can_quantify = true;
                prev_was_quantifier = false;
                i += 1;
            }
        }
    }
    if in_class {
        return Err("unterminated character set".into());
    }
    if paren_depth > 0 {
        return Err("missing ), unterminated subpattern".into());
    }
    Ok(())
}

fn check_json_config_list(
    items_val: &JsonVal,
    label: &str,
    base_dir: &Path,
    check_paths: bool,
    findings: &mut Vec<String>,
) {
    let Some(items) = items_val.as_arr() else {
        findings.push(format!("{label}: must be a list"));
        return;
    };
    for (idx, item_val) in items.iter().enumerate() {
        let ctx = format!("{label}[{idx}]");
        let Some(item) = item_val.as_obj() else {
            findings.push(format!("{ctx}: entry must be an object"));
            continue;
        };
        match item.get("path").and_then(JsonVal::as_str) {
            Some(raw_path) if !raw_path.trim().is_empty() => {
                if check_paths {
                    let resolved = expand_user_path(raw_path, base_dir);
                    if !resolved.exists() {
                        findings.push(format!(
                            "{ctx}: resolved path does not exist: {}",
                            resolved.display()
                        ));
                    }
                }
            }
            _ => findings.push(format!("{ctx}: missing required 'path' string")),
        }
        for key in ["include_only", "exclude"] {
            if let Some(pats_val) = item.get(key) {
                let Some(pats) = pats_val.as_arr() else {
                    findings.push(format!("{ctx}.{key}: must be a list of pattern strings"));
                    continue;
                };
                if pats.iter().any(|p| p.as_str().is_none()) {
                    findings.push(format!("{ctx}.{key}: must be a list of pattern strings"));
                    continue;
                }
                for p in pats {
                    if let Some(pat) = p.as_str() {
                        if let Err(err) = validate_regex_syntax(pat) {
                            findings.push(format!("{ctx}.{key}: invalid regex '{pat}' ({err})"));
                        }
                    }
                }
            }
        }
    }
}

/// Validate `skills.json` and `plugins.json` config structure, paths, and regex filters.
pub fn check_json_config(path: &Path, check_paths: bool) -> CheckResult {
    let mut summary = BTreeMap::new();
    summary.insert("file".into(), path.display().to_string());
    if !path.is_file() {
        return error_result(format!("file not found: {}", path.display()), summary);
    }
    let raw = match read_utf8_lossy(path) {
        Ok(s) => s,
        Err(e) => return error_result(e, summary),
    };
    let root = match parse_json(&raw) {
        Ok(v) => v,
        Err(e) => {
            return fail_result(
                format!("{}: invalid JSON ({e})", path.display()),
                summary,
            )
        }
    };
    let Some(data) = root.as_obj() else {
        return fail_result(
            format!("{}: root must be a JSON object", path.display()),
            summary,
        );
    };

    let mut findings = Vec::new();
    if !data.contains_key("entries") && !data.contains_key("inherits") {
        findings.push(format!(
            "{}: config should define 'entries' and/or 'inherits'",
            path.display()
        ));
    }
    let base_dir = path.parent().unwrap_or_else(|| Path::new("."));
    if let Some(entries) = data.get("entries") {
        check_json_config_list(
            entries,
            &format!("{}:entries", path.display()),
            base_dir,
            check_paths,
            &mut findings,
        );
    }
    if let Some(inherits) = data.get("inherits") {
        check_json_config_list(
            inherits,
            &format!("{}:inherits", path.display()),
            base_dir,
            check_paths,
            &mut findings,
        );
    }
    CheckResult::from_findings(findings, summary)
}

// ---------------------------------------------------------------------------
// 4. agy-workflow-migrate
// ---------------------------------------------------------------------------

fn slugify_workflow(stem: &str) -> String {
    let mut out = String::new();
    let mut prev_dash = false;
    for ch in stem.to_lowercase().chars() {
        if ch.is_ascii_lowercase() || ch.is_ascii_digit() || ch == '-' {
            out.push(ch);
            prev_dash = false;
        } else if !prev_dash && !out.is_empty() {
            out.push('-');
            prev_dash = true;
        }
    }
    let trimmed = out.trim_matches('-').to_string();
    if trimmed.is_empty() {
        "migrated-workflow".into()
    } else {
        trimmed
    }
}

fn move_file_cross_device(src: &Path, dst: &Path) -> Result<(), String> {
    if let Some(parent) = dst.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("{}: {e}", parent.display()))?;
    }
    if fs::rename(src, dst).is_ok() {
        return Ok(());
    }
    fs::copy(src, dst).map_err(|e| format!("copy {} -> {}: {e}", src.display(), dst.display()))?;
    fs::remove_file(src).map_err(|e| format!("remove {}: {e}", src.display()))?;
    Ok(())
}

/// Migrate legacy `.agent/workflows/*.md` files into `.agents/skills/` (`preview|apply|undo|verify`).
pub fn check_workflow_migrate(root: &Path, mode: &str) -> CheckResult {
    let mut summary = BTreeMap::new();
    summary.insert("mode".into(), mode.into());
    if !root.is_dir() {
        return error_result(format!("directory not found: {}", root.display()), summary);
    }

    let wf_dir = root.join(".agent").join("workflows");
    let skills_dir = root.join(".agents").join("skills");
    let archive_dir = root.join(".agent").join("workflows.migrated");
    let manifest_path = archive_dir.join("migration-manifest.json");

    if !matches!(mode, "preview" | "apply" | "undo" | "verify") {
        return error_result(
            format!("invalid mode '{mode}'; expected preview, apply, undo, or verify"),
            summary,
        );
    }

    if mode == "undo" {
        if !manifest_path.is_file() {
            return fail_result(
                format!("missing manifest: {}", manifest_path.display()),
                summary,
            );
        }
        let raw = match read_utf8_lossy(&manifest_path) {
            Ok(s) => s,
            Err(e) => return error_result(e, summary),
        };
        let manifest = match parse_json(&raw) {
            Ok(v) => v,
            Err(e) => {
                return fail_result(
                    format!("{}: invalid manifest JSON ({e})", manifest_path.display()),
                    summary,
                )
            }
        };
        if let Err(e) = fs::create_dir_all(&wf_dir) {
            return error_result(format!("{}: {e}", wf_dir.display()), summary);
        }
        // Nothing is deleted: what undo takes out of .agents/skills goes to a time-stamped folder.
        let stamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let undone = root.join(".agent").join("workflows.undone").join(stamp.to_string());
        let mut restored = 0usize;
        if let Some(items) = manifest.get("items").and_then(JsonVal::as_arr) {
            for item in items {
                let Some(arch) = item.get_str("archived").map(PathBuf::from) else {
                    continue;
                };
                let Some(orig) = item.get_str("source").map(PathBuf::from) else {
                    continue;
                };
                let Some(dest) = item.get_str("skill_md").map(PathBuf::from) else {
                    continue;
                };
                if arch.is_file() && !orig.exists() {
                    if let Err(e) = move_file_cross_device(&arch, &orig) {
                        return error_result(e, summary);
                    }
                    restored += 1;
                }
                if dest.is_file() {
                    let keep = undone.join(dest.parent().and_then(|p| p.file_name()).unwrap_or_default()).join("SKILL.md");
                    if let Err(e) = move_file_cross_device(&dest, &keep) {
                        return error_result(e, summary);
                    }
                    if let Some(parent) = dest.parent() {
                        if let Ok(mut rd) = fs::read_dir(parent) {
                            if rd.next().is_none() {
                                let _ = fs::remove_dir(parent);
                            }
                        }
                    }
                }
            }
        }
        if let Err(e) = move_file_cross_device(&manifest_path, &undone.join("migration-manifest.json")) {
            return error_result(e, summary);
        }
        summary.insert("undone_to".into(), undone.display().to_string());
        summary.insert("restored".into(), restored.to_string());
        return CheckResult::from_findings(Vec::new(), summary);
    }

    if mode == "verify" {
        if !manifest_path.is_file() {
            return fail_result(
                format!("missing manifest: {}", manifest_path.display()),
                summary,
            );
        }
        let raw = match read_utf8_lossy(&manifest_path) {
            Ok(s) => s,
            Err(e) => return error_result(e, summary),
        };
        let manifest = match parse_json(&raw) {
            Ok(v) => v,
            Err(e) => {
                return fail_result(
                    format!("{}: invalid manifest JSON ({e})", manifest_path.display()),
                    summary,
                )
            }
        };
        let items = manifest.get("items").and_then(JsonVal::as_arr).cloned().unwrap_or_default();
        let mut findings = Vec::new();
        for item in &items {
            if let Some(s) = item.get_str("skill_md") {
                if !Path::new(s).is_file() {
                    findings.push(format!("missing migrated skill: {s}"));
                }
            }
            if let Some(a) = item.get_str("archived") {
                if !Path::new(a).is_file() {
                    findings.push(format!("missing archived workflow: {a}"));
                }
            }
        }
        summary.insert("verified".into(), items.len().to_string());
        return CheckResult::from_findings(findings, summary);
    }

    if !wf_dir.is_dir() {
        summary.insert("planned".into(), "0".into());
        return CheckResult::from_findings(Vec::new(), summary);
    }

    let mut sources: Vec<PathBuf> = match fs::read_dir(&wf_dir) {
        Ok(rd) => rd
            .flatten()
            .map(|e| e.path())
            .filter(|p| p.is_file() && p.extension().and_then(|s| s.to_str()) == Some("md"))
            .collect(),
        Err(e) => return error_result(format!("{}: {e}", wf_dir.display()), summary),
    };
    sources.sort();

    struct Planned {
        source: PathBuf,
        skill_md: PathBuf,
        archived: PathBuf,
        name: String,
        description: String,
        body: String,
    }

    let mut planned = Vec::new();
    let mut findings = Vec::new();
    for src in sources {
        let raw = match read_utf8_lossy(&src) {
            Ok(s) => s,
            Err(e) => {
                findings.push(e);
                continue;
            }
        };
        let fm = parse_frontmatter(&raw);
        let stem = src
            .file_stem()
            .and_then(|s| s.to_str())
            .unwrap_or("workflow");
        let fname = src
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("workflow.md");
        let name = slugify_workflow(stem);
        let desc = fm
            .fields
            .get("description")
            .cloned()
            .filter(|d| !d.is_empty())
            .unwrap_or_else(|| format!("Workflow migrated from {fname}."));
        let dest = skills_dir.join(&name).join("SKILL.md");
        let arch = archive_dir.join(fname);
        if dest.exists() {
            findings.push(format!("target already exists: {}", dest.display()));
        }
        planned.push(Planned {
            source: src,
            skill_md: dest,
            archived: arch,
            name,
            description: desc,
            body: fm.body.trim_start_matches('\n').to_string(),
        });
    }

    if mode == "preview" {
        let targets_json: Vec<JsonVal> = planned
            .iter()
            .map(|it| JsonVal::Str(it.skill_md.display().to_string()))
            .collect();
        summary.insert("planned".into(), planned.len().to_string());
        summary.insert("targets".into(), JsonVal::Arr(targets_json).to_canonical_json());
        return CheckResult::from_findings(findings, summary);
    }

    if !findings.is_empty() {
        return CheckResult::from_findings(findings, summary);
    }

    if let Err(e) = fs::create_dir_all(&archive_dir) {
        return error_result(format!("{}: {e}", archive_dir.display()), summary);
    }
    let mut manifest_items = Vec::with_capacity(planned.len());
    for it in &planned {
        let desc_json = JsonVal::Str(it.description.clone()).to_canonical_json();
        let skill_text = format!(
            "---\nname: {}\ndescription: {}\n---\n\n{}",
            it.name, desc_json, it.body
        );
        if let Some(parent) = it.skill_md.parent() {
            if let Err(e) = fs::create_dir_all(parent) {
                return error_result(format!("{}: {e}", parent.display()), summary);
            }
        }
        if let Err(e) = fs::write(&it.skill_md, skill_text) {
            return error_result(format!("{}: {e}", it.skill_md.display()), summary);
        }
        if let Err(e) = move_file_cross_device(&it.source, &it.archived) {
            return error_result(e, summary);
        }
        let mut obj = BTreeMap::new();
        obj.insert("source".into(), JsonVal::Str(it.source.display().to_string()));
        obj.insert(
            "skill_md".into(),
            JsonVal::Str(it.skill_md.display().to_string()),
        );
        obj.insert(
            "archived".into(),
            JsonVal::Str(it.archived.display().to_string()),
        );
        manifest_items.push(JsonVal::Obj(obj));
    }

    let mut root_obj = BTreeMap::new();
    root_obj.insert("items".into(), JsonVal::Arr(manifest_items));
    if let Err(e) = fs::write(&manifest_path, JsonVal::Obj(root_obj).to_canonical_json()) {
        return error_result(format!("{}: {e}", manifest_path.display()), summary);
    }
    summary.insert("migrated".into(), planned.len().to_string());
    CheckResult::from_findings(Vec::new(), summary)
}

// ---------------------------------------------------------------------------
// 5. agy-agent-settings-audit
// ---------------------------------------------------------------------------


