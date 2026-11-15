//! The THREAT MODEL as a check: every MCP server an agent config can start or reach is a door.
//! Reads the Claude (.claude.json, .mcp.json, settings.json), Codex (config.toml), Gemini
//! (settings.json) and Antigravity (mcp_config.json) MCP configs. Pure std, offline, read-only.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use crate::common::{home_dir, parse_json, read_utf8_lossy, which_on_path, CheckResult, JsonVal};

/// One server entry found in one config file.
#[derive(Debug, Clone, PartialEq)]
pub struct Door {
    pub file: String,
    pub name: String,
    /// "stdio" or "remote"
    pub kind: &'static str,
    pub target: String,
    pub flags: Vec<&'static str>,
}

fn classify_stdio(file: &str, name: &str, cmd: &str, check_bins: bool) -> Door {
    let mut flags = Vec::new();
    if check_bins && which_on_path(cmd).is_none() && !Path::new(cmd).is_file() {
        flags.push("MISSING-BIN");
    }
    Door { file: file.into(), name: name.into(), kind: "stdio", target: cmd.into(), flags }
}

fn classify_remote(file: &str, name: &str, url: &str) -> Door {
    let mut flags = vec!["REMOTE"];
    let path = url.split(['?', '#']).next().unwrap_or(url).trim_end_matches('/');
    if path.ends_with("/sse") {
        flags.push("LEGACY-SSE");
    }
    if url.starts_with("http://") {
        flags.push("PLAINTEXT");
    }
    Door { file: file.into(), name: name.into(), kind: "remote", target: strip_userinfo(url), flags }
}

/// Never print a secret that sits in a URL: drop user:pass@ and the query string.
fn strip_userinfo(url: &str) -> String {
    let base = url.split(['?', '#']).next().unwrap_or(url);
    if let Some(i) = base.find("://") {
        let (scheme, rest) = base.split_at(i + 3);
        let (auth, tail) = match rest.find('/') {
            Some(j) => rest.split_at(j),
            None => (rest, ""),
        };
        let host = auth.rsplit('@').next().unwrap_or(auth);
        return format!("{scheme}{host}{tail}");
    }
    base.to_string()
}

fn doors_from_json(file: &str, root: &JsonVal, check_bins: bool) -> Vec<Door> {
    let mut out = Vec::new();
    let Some(servers) = root.get("mcpServers").and_then(JsonVal::as_obj) else { return out };
    for (name, v) in servers {
        let Some(cfg) = v.as_obj() else { continue };
        let url = ["url", "serverUrl", "httpUrl"]
            .iter()
            .find_map(|k| cfg.get(*k).and_then(JsonVal::as_str))
            .filter(|s| !s.is_empty());
        if let Some(u) = url {
            out.push(classify_remote(file, name, u));
        } else if let Some(c) = cfg.get("command").and_then(JsonVal::as_str) {
            out.push(classify_stdio(file, name, c, check_bins));
        }
    }
    out
}

/// Codex keeps servers as `[mcp_servers.NAME]` tables with `command = "..."` or `url = "..."`.
fn doors_from_toml(file: &str, raw: &str, check_bins: bool) -> Vec<Door> {
    let mut tables: BTreeMap<String, BTreeMap<String, String>> = BTreeMap::new();
    let mut cur: Option<String> = None;
    for line in raw.lines() {
        let l = line.trim();
        if l.starts_with('[') && l.ends_with(']') {
            let h = l.trim_matches(|c| c == '[' || c == ']').trim();
            cur = h
                .strip_prefix("mcp_servers.")
                .filter(|n| !n.contains('.'))
                .map(|n| n.trim_matches('"').to_string());
            if let Some(n) = &cur {
                tables.entry(n.clone()).or_default();
            }
        } else if let (Some(n), Some((k, v))) = (&cur, l.split_once('=')) {
            let v = v.trim();
            if v.starts_with('"') {
                let inner = v[1..].split('"').next().unwrap_or("");
                tables.get_mut(n).unwrap().insert(k.trim().to_string(), inner.to_string());
            }
        }
    }
    let mut out = Vec::new();
    for (name, t) in tables {
        if let Some(u) = t.get("url").filter(|s| !s.is_empty()) {
            out.push(classify_remote(file, &name, u));
        } else if let Some(c) = t.get("command") {
            out.push(classify_stdio(file, &name, c, check_bins));
        }
    }
    out
}

/// Files an agent reads MCP servers from, under `home` and the project `cwd`.
pub fn default_config_files(home: &Path, cwd: &Path) -> Vec<PathBuf> {
    vec![
        home.join(".claude.json"),
        home.join(".claude").join("settings.json"),
        cwd.join(".mcp.json"),
        home.join(".codex").join("config.toml"),
        home.join(".gemini").join("settings.json"),
        home.join(".gemini").join("antigravity").join("mcp_config.json"),
    ]
}

pub fn collect(files: &[PathBuf], check_bins: bool) -> (Vec<Door>, Vec<String>) {
    let mut doors = Vec::new();
    let mut problems = Vec::new();
    for f in files {
        if !f.is_file() {
            continue;
        }
        let label = f.display().to_string();
        let raw = match read_utf8_lossy(f) {
            Ok(s) => s,
            Err(e) => {
                problems.push(format!("{label}: {e}"));
                continue;
            }
        };
        if f.extension().and_then(|e| e.to_str()) == Some("toml") {
            doors.extend(doors_from_toml(&label, &raw, check_bins));
        } else {
            match parse_json(&raw) {
                Ok(v) => doors.extend(doors_from_json(&label, &v, check_bins)),
                Err(e) => problems.push(format!("{label}: invalid JSON ({e})")),
            }
        }
    }
    (doors, problems)
}

/// `doors [FILE...] [--home DIR] [--check-bins]`: FILEs given are read as they are; none given
/// means the default config locations. Remote servers are listed (they are doors); a legacy
/// `/sse` endpoint, plain http, a missing binary or an unreadable file is a finding.
pub fn check_doors(files: &[PathBuf], check_bins: bool) -> CheckResult {
    let (doors, problems) = collect(files, check_bins);
    let mut findings = problems;
    let mut summary = BTreeMap::new();
    let remote = doors.iter().filter(|d| d.kind == "remote").count();
    summary.insert("files_read".into(), files.iter().filter(|f| f.is_file()).count().to_string());
    summary.insert("servers".into(), doors.len().to_string());
    summary.insert("remote_doors".into(), remote.to_string());
    for (i, d) in doors.iter().enumerate() {
        summary.insert(
            format!("door_{:02}", i + 1),
            format!("{} {} {} [{}] {}", d.kind, d.name, d.target, d.flags.join(","), d.file),
        );
        for fl in &d.flags {
            if matches!(*fl, "LEGACY-SSE" | "MISSING-BIN" | "PLAINTEXT") {
                findings.push(format!("{}: {} ({}) {}", d.file, d.name, d.target, fl));
            }
        }
    }
    CheckResult::from_findings(findings, summary)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn json_stdio_and_remote_are_classified() {
        let v = parse_json(
            r#"{"mcpServers":{"a":{"command":"definitely-not-a-binary-xyz"},
            "b":{"url":"https://example.org/sse?token=abc"},
            "c":{"httpUrl":"http://example.org/mcp"}}}"#,
        )
        .unwrap();
        let d = doors_from_json("f", &v, true);
        assert_eq!(d.len(), 3);
        assert!(d[0].flags.contains(&"MISSING-BIN"));
        assert!(d[1].flags.contains(&"LEGACY-SSE"));
        assert_eq!(d[1].target, "https://example.org/sse", "query stripped from the URL");
        assert_eq!(strip_userinfo(&format!("https://{}{}h.org/x", "a:b", "@")), "https://h.org/x");
        assert!(d[2].flags.contains(&"PLAINTEXT"));
    }

    #[test]
    fn codex_toml_tables_are_read() {
        let t = "[mcp_servers.x]\ncommand = \"sh\"\n[mcp_servers.y]\nurl = \"https://e.org/mcp\"\n[other]\nurl = \"https://no\"\n";
        let d = doors_from_toml("c.toml", t, false);
        assert_eq!(d.len(), 2);
        assert_eq!(d[0].kind, "stdio");
        assert_eq!(d[1].kind, "remote");
    }

    #[test]
    fn clean_remote_is_listed_but_not_a_finding() {
        let dir = std::env::temp_dir().join(format!("agy-doors-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let f = dir.join(".mcp.json");
        std::fs::write(&f, r#"{"mcpServers":{"r":{"url":"https://e.org/mcp"}}}"#).unwrap();
        let r = check_doors(&[f.clone()], false);
        assert_eq!(r.exit_code, 0);
        assert_eq!(r.summary["remote_doors"], "1");
        std::fs::write(&f, r#"{"mcpServers":{"r":{"url":"https://e.org/sse"}}}"#).unwrap();
        assert_eq!(check_doors(&[f], false).exit_code, 3);
        let _ = std::fs::remove_dir_all(&dir);
    }
}

#[allow(dead_code)]
pub fn home() -> PathBuf {
    home_dir()
}
