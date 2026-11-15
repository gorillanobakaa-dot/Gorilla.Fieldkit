//! Native Rust deterministic validators (Windows & Linux, zero external crates).

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use crate::common::{
    is_https_url, parse_frontmatter, parse_json, read_utf8_lossy,
    utc_parts_from_unix, walk_files, CheckResult, JsonVal,
};

const MAX_RULE_BYTES: usize = 24_000;
const DEFAULT_RULES_BUDGET_TOKENS: usize = 20_000;

pub fn check_skill(path: &Path) -> CheckResult {
    check_skill_opts(path, false)
}

pub fn check_skill_opts(path: &Path, strict_names: bool) -> CheckResult {
    let mut dirs = Vec::new();
    if path.join("SKILL.md").is_file() {
        dirs.push(path.to_path_buf());
    } else if path.is_dir() {
        if let Ok(rd) = fs::read_dir(path) {
            for ent in rd.flatten() {
                if ent.path().is_dir() {
                    dirs.push(ent.path());
                }
            }
        }
        dirs.sort();
    } else if path.is_file() && path.file_name().and_then(|s| s.to_str()) == Some("SKILL.md") {
        if let Some(parent) = path.parent() {
            dirs.push(parent.to_path_buf());
        }
    }

    let mut findings = Vec::new();
    if dirs.is_empty() {
        findings.push(format!("no skill directory found at {}", path.display()));
    }
    for dir in &dirs {
        let md = dir.join("SKILL.md");
        if !md.is_file() {
            findings.push(format!("{}: missing SKILL.md", dir.display()));
            continue;
        }
        match read_utf8_lossy(&md) {
            Err(e) => findings.push(e),
            Ok(raw) => {
                let fm = parse_frontmatter(&raw);
                for e in fm.errors {
                    findings.push(format!("{}: {e}", md.display()));
                }
                let name = fm.fields.get("name").map(|s| s.trim()).unwrap_or("");
                if name.is_empty() {
                    findings.push(format!("{}: missing required frontmatter field 'name'", md.display()));
                } else if strict_names {
                    let dir_name = dir.file_name().and_then(|s| s.to_str()).unwrap_or("");
                    let norm_name = name.replace('_', "-");
                    let norm_dir = dir_name.replace('_', "-");
                    if !dir_name.is_empty() && norm_name != norm_dir {
                        findings.push(format!(
                            "{}: skill name '{name}' does not match folder '{dir_name}'",
                            md.display()
                        ));
                    }
                }
                if fm.fields.get("description").map(|s| s.trim().is_empty()).unwrap_or(true) {
                    findings.push(format!("{}: missing required frontmatter field 'description'", md.display()));
                }
                if fm.body.trim().is_empty() {
                    findings.push(format!("{}: body instructions are empty", md.display()));
                }
                let mut in_fence = false;
                for line in fm.body.lines() {
                    if line.trim_start().starts_with("```") {
                        in_fence = !in_fence;
                        continue;
                    }
                    if in_fence {
                        continue;
                    }
                    let mut rest = line;
                    while let Some(pos) = rest.find("](") {
                        let after = &rest[pos + 2..];
                        if let Some(end) = after.find(')') {
                            let raw_target = after[..end].split('#').next().unwrap_or("").trim();
                            if !raw_target.is_empty()
                                && !raw_target.contains("://")
                                && !raw_target.starts_with('/')
                                && !raw_target.starts_with("mailto:")
                                && !raw_target.contains('{')
                                && !raw_target.contains(' ')
                            {
                                if !dir.join(raw_target).exists() {
                                    findings.push(format!(
                                        "{}: broken relative link '{raw_target}'",
                                        md.display()
                                    ));
                                }
                            }
                            rest = &after[end + 1..];
                        } else {
                            break;
                        }
                    }
                }
            }
        }
    }
    let mut sum = BTreeMap::new();
    sum.insert("skills_checked".into(), dirs.len().to_string());
    CheckResult::from_findings(findings, sum)
}

fn extract_rule_includes(raw: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut rest = raw;
    while let Some(pos) = rest.find("@[") {
        let after_at = &rest[pos + 2..];
        if let Some(mid) = after_at.find("](") {
            let after_mid = &after_at[mid + 2..];
            if let Some(end) = after_mid.find(')') {
                let inc = after_mid[..end].trim();
                if !inc.is_empty() {
                    out.push(inc.to_string());
                }
                rest = &after_mid[end + 1..];
                continue;
            }
        }
        rest = after_at;
    }
    out
}

pub fn check_rules_budget(path: &Path) -> CheckResult {
    let mut files = Vec::new();
    if path.is_file() {
        files.push(path.to_path_buf());
    } else {
        for f in walk_files(path) {
            if matches!(
                f.file_name().and_then(|s| s.to_str()),
                Some("GEMINI.md" | "AGENTS.md")
            ) {
                files.push(f);
            }
        }
    }
    let mut findings = Vec::new();
    let mut total_tokens = 0usize;
    for rf in &files {
        match read_utf8_lossy(rf) {
            Err(e) => findings.push(e),
            Ok(raw) => {
                if raw.trim_start().starts_with("---\n") || raw.trim_start().starts_with("---\r\n") {
                    findings.push(format!(
                        "{}: standalone rule file must not start with YAML frontmatter",
                        rf.display()
                    ));
                }
                let bytes = raw.len();
                if bytes > MAX_RULE_BYTES {
                    findings.push(format!(
                        "{}: {bytes} bytes exceeds {MAX_RULE_BYTES}-byte cap",
                        rf.display()
                    ));
                }
                let parent = rf.parent().unwrap_or_else(|| Path::new("."));
                for inc in extract_rule_includes(&raw) {
                    let ip = Path::new(&inc);
                    let target = if ip.is_absolute() { ip.to_path_buf() } else { parent.join(ip) };
                    if !target.exists() {
                        findings.push(format!(
                            "{}: missing included rule file '{inc}'",
                            rf.display()
                        ));
                    }
                }
                total_tokens += if raw.is_empty() { 0 } else { (bytes + 3) / 4 };
            }
        }
    }
    if total_tokens > DEFAULT_RULES_BUDGET_TOKENS {
        findings.push(format!(
            "aggregate estimated tokens ({total_tokens}) exceeds {DEFAULT_RULES_BUDGET_TOKENS}-token budget"
        ));
    }
    let mut sum = BTreeMap::new();
    sum.insert("files_checked".into(), files.len().to_string());
    sum.insert("total_tokens_est".into(), total_tokens.to_string());
    sum.insert("budget_tokens".into(), DEFAULT_RULES_BUDGET_TOKENS.to_string());
    CheckResult::from_findings(findings, sum)
}

fn find_plugin_manifest(dir: &Path) -> Option<PathBuf> {
    [
        dir.join("plugin.json"),
        dir.join(".antigravity-plugin").join("plugin.json"),
        dir.join(".gemini-plugin").join("plugin.json"),
    ]
    .into_iter()
    .find(|p| p.is_file())
}

fn validate_single_plugin_dir(path: &Path, findings: &mut Vec<String>) {
    let Some(mpath) = find_plugin_manifest(path) else {
        findings.push(format!("{}: missing plugin.json", path.display()));
        return;
    };
    match read_utf8_lossy(&mpath) {
        Err(e) => findings.push(e),
        Ok(txt) => match parse_json(&txt) {
            Err(e) => findings.push(format!("{}: invalid JSON ({e})", mpath.display())),
            Ok(val) => {
                let Some(obj) = val.as_obj() else {
                    findings.push(format!("{}: manifest must be a JSON object", mpath.display()));
                    return;
                };
                if let Some(hp) = obj.get("homepage") {
                    if !hp.as_str().map(is_https_url).unwrap_or(false) {
                        findings.push(format!("{}: 'homepage' must be an https:// URL", mpath.display()));
                    }
                }
                if let Some(repo) = obj.get("repository") {
                    if let Some(ro) = repo.as_obj() {
                        if !ro.get("url").and_then(JsonVal::as_str).map(is_https_url).unwrap_or(false) {
                            findings.push(format!("{}: repository.url must be an https:// URL", mpath.display()));
                        }
                    } else if !repo.as_str().map(is_https_url).unwrap_or(false) {
                        findings.push(format!("{}: repository must be an https:// URL or object", mpath.display()));
                    }
                }
                if let Some(dis) = obj.get("disabled") {
                    if dis.as_bool().is_none() {
                        findings.push(format!("{}: 'disabled' must be a boolean", mpath.display()));
                    }
                }
                if let Some(logo) = obj.get("logo").and_then(JsonVal::as_str) {
                    if !path.join(logo).exists() {
                        findings.push(format!("{}: missing logo asset '{logo}'", mpath.display()));
                    }
                }
            }
        },
    }
    if path.join("hooks").join("hooks.json").is_file() && !path.join("hooks.json").is_file() {
        findings.push(format!(
            "{}: native plugin must place hooks.json at plugin root",
            path.display()
        ));
    }
    let skills_dir = path.join("skills");
    if skills_dir.is_dir() {
        if let Ok(rd) = fs::read_dir(&skills_dir) {
            let mut subdirs: Vec<PathBuf> = rd.flatten().map(|e| e.path()).filter(|p| p.is_dir()).collect();
            subdirs.sort();
            for sd in subdirs {
                if !sd.join("SKILL.md").is_file() {
                    findings.push(format!("{}: plugin skill missing SKILL.md", sd.display()));
                }
            }
        }
    }
}

pub fn check_plugin_dir(path: &Path) -> CheckResult {
    let mut findings = Vec::new();
    let dirs: Vec<PathBuf> = if find_plugin_manifest(path).is_some() {
        vec![path.to_path_buf()]
    } else if path.is_dir() {
        let mut subs: Vec<PathBuf> = fs::read_dir(path)
            .into_iter()
            .flatten()
            .flatten()
            .map(|e| e.path())
            .filter(|p| p.is_dir())
            .collect();
        subs.sort();
        subs
    } else {
        findings.push(format!("{}: directory not found", path.display()));
        Vec::new()
    };
    if dirs.is_empty() && findings.is_empty() {
        findings.push(format!("{}: missing plugin.json", path.display()));
    }
    for d in &dirs {
        validate_single_plugin_dir(d, &mut findings);
    }
    let mut sum = BTreeMap::new();
    sum.insert("plugins_checked".into(), dirs.len().to_string());
    sum.insert("plugin_dir".into(), path.display().to_string());
    CheckResult::from_findings(findings, sum)
}

#[allow(dead_code)]
fn cron_named_val(tok: &str, field_idx: usize) -> Option<i32> {
    match (field_idx, tok) {
        (3, "jan") => Some(1),
        (3, "feb") => Some(2),
        (3, "mar") => Some(3),
        (3, "apr") => Some(4),
        (3, "may") => Some(5),
        (3, "jun") => Some(6),
        (3, "jul") => Some(7),
        (3, "aug") => Some(8),
        (3, "sep") => Some(9),
        (3, "oct") => Some(10),
        (3, "nov") => Some(11),
        (3, "dec") => Some(12),
        (4, "sun") => Some(0),
        (4, "mon") => Some(1),
        (4, "tue") => Some(2),
        (4, "wed") => Some(3),
        (4, "thu") => Some(4),
        (4, "fri") => Some(5),
        (4, "sat") => Some(6),
        _ => None,
    }
}

fn parse_cron_field(expr: &str, lo: i32, hi: i32, field_idx: usize) -> Result<BTreeSet<i32>, String> {
    let mut set = BTreeSet::new();
    for raw_part in expr.split(',') {
        let part = raw_part.trim().to_ascii_lowercase();
        if part.is_empty() {
            return Err("empty cron list item".into());
        }
        let (base, step_opt) = match part.split_once('/') {
            Some((b, st)) => {
                let step = st.parse::<i32>().map_err(|_| format!("invalid step '{part}'"))?;
                if step <= 0 {
                    return Err(format!("step must be > 0: '{part}'"));
                }
                (b, Some(step))
            }
            None => (part.as_str(), None),
        };
        let step = step_opt.unwrap_or(1);
        let parse_num = |s: &str| -> Result<i32, String> {
            cron_named_val(s, field_idx)
                .ok_or(())
                .or_else(|_| s.parse::<i32>().map_err(|_| ()))
                .map_err(|_| format!("invalid cron token '{s}' in '{part}'"))
        };
        let (start, end) = if base == "*" {
            (lo, hi)
        } else if let Some((a, b)) = base.split_once('-') {
            (parse_num(a)?, parse_num(b)?)
        } else {
            let v = parse_num(base)?;
            if step_opt.is_some() {
                (v, hi)
            } else {
                (v, v)
            }
        };
        if start < lo || end > hi || start > end {
            return Err(format!("field {field_idx}: value out of [{lo}..{hi}]: '{part}'"));
        }
        let mut x = start;
        while x <= end {
            set.insert(if field_idx == 4 && x == 7 { 0 } else { x });
            x += step;
        }
    }
    Ok(set)
}

pub fn check_cron(expr: &str) -> CheckResult {
    check_cron_with_next(expr, 3)
}

pub fn check_cron_with_next(expr: &str, next_count: usize) -> CheckResult {
    let fields: Vec<&str> = expr.split_whitespace().collect();
    let mut findings = Vec::new();
    let mut runs = Vec::new();
    if fields.len() != 5 {
        findings.push(format!("expected 5 cron fields, got {}", fields.len()));
    } else {
        let bounds = [(0i32, 59i32), (0, 23), (1, 31), (1, 12), (0, 7)];
        let mut sets = Vec::with_capacity(5);
        for (idx, (&f, &(lo, hi))) in fields.iter().zip(bounds.iter()).enumerate() {
            match parse_cron_field(f, lo, hi, idx) {
                Ok(s) => sets.push(s),
                Err(e) => findings.push(e),
            }
        }
        if findings.is_empty() && sets.len() == 5 {
            let want = next_count.clamp(1, 20);
            let now = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map(|d| d.as_secs() as i64)
                .unwrap_or(0);
            let mut t = (now / 60 + 1) * 60;
            let limit = t + 366 * 86_400;
            while t <= limit && runs.len() < want {
                let (yr, mo, dy, hr, mn, _sc, dow) = utc_parts_from_unix(t);
                if sets[0].contains(&(mn as i32))
                    && sets[1].contains(&(hr as i32))
                    && sets[2].contains(&(dy as i32))
                    && sets[3].contains(&(mo as i32))
                    && sets[4].contains(&(dow as i32))
                {
                    runs.push(format!("{yr:04}-{mo:02}-{dy:02}T{hr:02}:{mn:02}Z"));
                }
                t += 60;
            }
        }
    }
    let mut sum = BTreeMap::new();
    sum.insert("expression".into(), expr.to_string());
    if !runs.is_empty() {
        sum.insert("next_runs_utc".into(), runs.join(", "));
    }
    CheckResult::from_findings(findings, sum)
}

