//! Shared cross-platform stdlib helpers for agy-tools (Windows & Linux).

use std::collections::BTreeMap;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone)]
pub struct CheckResult {
    pub status: &'static str,
    pub summary: BTreeMap<String, String>,
    pub findings: Vec<String>,
    pub exit_code: i32,
}

impl CheckResult {
    pub fn from_findings(findings: Vec<String>, mut summary: BTreeMap<String, String>) -> Self {
        let ok = findings.is_empty();
        summary.insert("findings_count".into(), findings.len().to_string());
        Self {
            status: if ok { "pass" } else { "fail" },
            summary,
            findings,
            exit_code: if ok { 0 } else { 3 },
        }
    }

    pub fn print(&self, as_json: bool) -> i32 {
        if as_json {
            let mut out = String::from("{\n");
            out.push_str(&format!("  \"status\": \"{}\",\n", json_escape(self.status)));
            for (k, v) in &self.summary {
                out.push_str(&format!("  \"{}\": \"{}\",\n", json_escape(k), json_escape(v)));
            }
            out.push_str("  \"findings\": [");
            for (i, f) in self.findings.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                out.push_str(&format!("\n    \"{}\"", json_escape(f)));
            }
            if !self.findings.is_empty() {
                out.push('\n');
                out.push_str("  ");
            }
            out.push_str("],\n");
            out.push_str(&format!("  \"exit_code\": {}\n}}", self.exit_code));
            println!("{out}");
        } else {
            println!("status: {}", self.status);
            for (k, v) in &self.summary {
                println!("{k}: {v}");
            }
            for f in self.findings.iter().take(40) {
                println!("  - {f}");
            }
            println!("exit_code: {}", self.exit_code);
        }
        self.exit_code
    }
}

pub fn json_escape(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out
}

pub fn read_utf8_lossy(path: &Path) -> Result<String, String> {
    let bytes = fs::read(path).map_err(|e| format!("{}: {e}", path.display()))?;
    Ok(String::from_utf8_lossy(&bytes).into_owned())
}

pub fn platform_name() -> &'static str {
    if cfg!(target_os = "windows") {
        "windows"
    } else if cfg!(target_os = "macos") {
        "darwin"
    } else if cfg!(target_os = "freebsd") {
        "freebsd"
    } else {
        "linux"
    }
}

pub fn home_dir() -> PathBuf {
    if let Ok(h) = env::var("HOME") {
        if !h.is_empty() {
            return PathBuf::from(h);
        }
    }
    if let Ok(h) = env::var("USERPROFILE") {
        if !h.is_empty() {
            return PathBuf::from(h);
        }
    }
    PathBuf::from(".")
}

pub fn which_on_path(bin: &str) -> Option<PathBuf> {
    let path_var = env::var_os("PATH")?;
    let exts: Vec<&str> = if cfg!(target_os = "windows") {
        vec!["", ".exe", ".cmd", ".bat"]
    } else {
        vec![""]
    };
    for dir in env::split_paths(&path_var) {
        for ext in &exts {
            let cand = dir.join(format!("{bin}{ext}"));
            if cand.is_file() {
                return Some(cand);
            }
        }
    }
    None
}

#[derive(Debug, Default)]
pub struct Frontmatter {
    pub fields: BTreeMap<String, String>,
    pub body: String,
    pub errors: Vec<String>,
}

pub fn parse_frontmatter(raw: &str) -> Frontmatter {
    let norm = raw.replace("\r\n", "\n");
    let mut res = Frontmatter::default();
    if !norm.starts_with("---\n") {
        res.errors.push("missing opening YAML frontmatter delimiter '---'".into());
        res.body = norm;
        return res;
    }
    let rest = &norm[4..];
    let Some(end_idx) = rest.find("\n---\n").or_else(|| {
        let t = rest.trim_end();
        if t.ends_with("\n---") {
            Some(t.len() - 4)
        } else {
            None
        }
    }) else {
        res.errors.push("missing closing YAML frontmatter delimiter '---'".into());
        res.body = norm;
        return res;
    };
    let fm_text = &rest[..end_idx];
    let after = &rest[end_idx..];
    res.body = after.strip_prefix("\n---\n").unwrap_or("").to_string();

    let lines: Vec<&str> = fm_text.lines().collect();
    let mut i = 0;
    while i < lines.len() {
        let line = lines[i];
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') || line.starts_with(' ') || line.starts_with('\t') {
            i += 1;
            continue;
        }
        let Some((k, v)) = line.split_once(':') else {
            res.errors.push(format!("invalid frontmatter line: {line}"));
            i += 1;
            continue;
        };
        let key = k.trim().to_string();
        let val = v.trim();
        if matches!(val, ">" | ">-" | "|" | "|-") {
            let mut block = Vec::new();
            i += 1;
            while i < lines.len() && (lines[i].starts_with(' ') || lines[i].starts_with('\t') || lines[i].trim().is_empty()) {
                let s = lines[i].trim();
                if !s.is_empty() {
                    block.push(s);
                }
                i += 1;
            }
            res.fields.insert(key, block.join(if val.starts_with('>') { " " } else { "\n" }));
            continue;
        }
        let clean = val.trim_matches('"').trim_matches('\'').to_string();
        res.fields.insert(key, clean);
        i += 1;
    }
    res
}

pub fn walk_files(root: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(rd) = fs::read_dir(&dir) else { continue };
        let mut entries: Vec<PathBuf> = rd.filter_map(|e| e.ok().map(|x| x.path())).collect();
        entries.sort();
        for p in entries {
            let Ok(meta) = fs::symlink_metadata(&p) else { continue };
            if meta.file_type().is_symlink() {
                continue;
            }
            if meta.is_dir() {
                stack.push(p);
            } else if meta.is_file() {
                out.push(p);
            }
        }
    }
    out.sort();
    out
}

pub fn is_https_url(s: &str) -> bool {
    s.starts_with("https://") && s.len() > "https://".len()
}

pub fn expand_user_path(raw: &str, base_dir: &Path) -> PathBuf {
    let trimmed = raw.trim();
    if trimmed == "~" {
        return home_dir();
    }
    if let Some(rest) = trimmed.strip_prefix("~/").or_else(|| trimmed.strip_prefix("~\\")) {
        return home_dir().join(rest);
    }
    let p = Path::new(trimmed);
    if p.is_absolute() {
        p.to_path_buf()
    } else {
        base_dir.join(p)
    }
}

/// Convert Unix epoch seconds (UTC) into `(year, month 1..=12, day 1..=31, hour 0..=23, min 0..=59, sec 0..=59)`
/// and weekday (`0 = Sunday .. 6 = Saturday`). Uses Howard Hinnant's `civil_from_days` algorithm.
pub fn utc_parts_from_unix(secs: i64) -> (i64, u32, u32, u32, u32, u32, u32) {
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400) as u32;
    let hour = rem / 3_600;
    let min = (rem % 3_600) / 60;
    let sec = rem % 60;
    // 1970-01-01 was Thursday (4)
    let dow = ((days + 4).rem_euclid(7)) as u32;

    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let y = (yoe as i64) + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = y + if m <= 2 { 1 } else { 0 };
    (year, m as u32, d as u32, hour, min, sec, dow)
}

// ---------------------------------------------------------------------------
// Strict minimal JSON parser + canonical encoder
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq)]
pub enum JsonVal {
    Null,
    Bool(bool),
    Int(i64),
    RawNum(String),
    Str(String),
    Arr(Vec<JsonVal>),
    Obj(BTreeMap<String, JsonVal>),
}

impl JsonVal {
    pub fn as_str(&self) -> Option<&str> {
        match self {
            JsonVal::Str(s) => Some(s.as_str()),
            _ => None,
        }
    }

    pub fn as_i64(&self) -> Option<i64> {
        match self {
            JsonVal::Int(n) => Some(*n),
            _ => None,
        }
    }

    pub fn as_f64(&self) -> Option<f64> {
        match self {
            JsonVal::Int(n) => Some(*n as f64),
            JsonVal::RawNum(s) => s.parse::<f64>().ok(),
            _ => None,
        }
    }

    pub fn as_bool(&self) -> Option<bool> {
        match self {
            JsonVal::Bool(b) => Some(*b),
            _ => None,
        }
    }

    pub fn as_arr(&self) -> Option<&Vec<JsonVal>> {
        match self {
            JsonVal::Arr(v) => Some(v),
            _ => None,
        }
    }

    pub fn as_obj(&self) -> Option<&BTreeMap<String, JsonVal>> {
        match self {
            JsonVal::Obj(m) => Some(m),
            _ => None,
        }
    }

    pub fn get(&self, key: &str) -> Option<&JsonVal> {
        self.as_obj().and_then(|m| m.get(key))
    }

    pub fn get_str(&self, key: &str) -> Option<&str> {
        self.get(key).and_then(JsonVal::as_str)
    }

    pub fn to_canonical_json(&self) -> String {
        match self {
            JsonVal::Null => "null".into(),
            JsonVal::Bool(true) => "true".into(),
            JsonVal::Bool(false) => "false".into(),
            JsonVal::Int(n) => n.to_string(),
            JsonVal::RawNum(s) => s.clone(),
            JsonVal::Str(s) => format!("\"{}\"", json_escape(s)),
            JsonVal::Arr(items) => {
                let mut out = String::from("[");
                for (idx, it) in items.iter().enumerate() {
                    if idx > 0 {
                        out.push(',');
                    }
                    out.push_str(&it.to_canonical_json());
                }
                out.push(']');
                out
            }
            JsonVal::Obj(map) => {
                let mut out = String::from("{");
                for (idx, (k, v)) in map.iter().enumerate() {
                    if idx > 0 {
                        out.push(',');
                    }
                    out.push('"');
                    out.push_str(&json_escape(k));
                    out.push_str("\":");
                    out.push_str(&v.to_canonical_json());
                }
                out.push('}');
                out
            }
        }
    }
}

pub fn parse_json(input: &str) -> Result<JsonVal, String> {
    let mut p = JsonParser {
        bytes: input.as_bytes(),
        pos: 0,
    };
    p.skip_ws();
    let val = p.parse_val()?;
    p.skip_ws();
    if p.pos != p.bytes.len() {
        return Err(format!("trailing bytes after JSON at offset {}", p.pos));
    }
    Ok(val)
}

struct JsonParser<'a> {
    bytes: &'a [u8],
    pos: usize,
}

impl<'a> JsonParser<'a> {
    fn skip_ws(&mut self) {
        while self.pos < self.bytes.len() && matches!(self.bytes[self.pos], b' ' | b'\t' | b'\r' | b'\n') {
            self.pos += 1;
        }
    }

    fn peek(&self) -> Option<u8> {
        self.bytes.get(self.pos).copied()
    }

    fn next(&mut self) -> Option<u8> {
        let b = self.peek()?;
        self.pos += 1;
        Some(b)
    }

    fn parse_val(&mut self) -> Result<JsonVal, String> {
        self.skip_ws();
        match self.peek() {
            Some(b'n') => {
                self.expect_bytes(b"null")?;
                Ok(JsonVal::Null)
            }
            Some(b't') => {
                self.expect_bytes(b"true")?;
                Ok(JsonVal::Bool(true))
            }
            Some(b'f') => {
                self.expect_bytes(b"false")?;
                Ok(JsonVal::Bool(false))
            }
            Some(b'"') => Ok(JsonVal::Str(self.parse_string()?)),
            Some(b'[') => self.parse_array(),
            Some(b'{') => self.parse_object(),
            Some(b'-' | b'0'..=b'9') => self.parse_number(),
            Some(ch) => Err(format!("unexpected byte '{}' in JSON at {}", ch as char, self.pos)),
            None => Err("unexpected EOF in JSON".into()),
        }
    }

    fn expect_bytes(&mut self, lit: &[u8]) -> Result<(), String> {
        if self.bytes[self.pos..].starts_with(lit) {
            self.pos += lit.len();
            Ok(())
        } else {
            Err(format!("expected '{}' at offset {}", String::from_utf8_lossy(lit), self.pos))
        }
    }

    fn parse_string(&mut self) -> Result<String, String> {
        if self.next() != Some(b'"') {
            return Err("expected '\"'".into());
        }
        let mut out = String::new();
        while let Some(b) = self.next() {
            match b {
                b'"' => return Ok(out),
                b'\\' => {
                    let esc = self.next().ok_or("unterminated escape in JSON string")?;
                    match esc {
                        b'"' => out.push('"'),
                        b'\\' => out.push('\\'),
                        b'/' => out.push('/'),
                        b'b' => out.push('\x08'),
                        b'f' => out.push('\x0c'),
                        b'n' => out.push('\n'),
                        b'r' => out.push('\r'),
                        b't' => out.push('\t'),
                        b'u' => {
                            let code = self.parse_hex4()?;
                            if (0xD800..=0xDBFF).contains(&code) {
                                if !self.bytes[self.pos..].starts_with(b"\\u") {
                                    return Err("unpaired high surrogate".into());
                                }
                                self.pos += 2;
                                let low = self.parse_hex4()?;
                                if !(0xDC00..=0xDFFF).contains(&low) {
                                    return Err("invalid low surrogate".into());
                                }
                                let cp = 0x10000 + (((code as u32 - 0xD800) << 10) | (low as u32 - 0xDC00));
                                let ch = char::from_u32(cp).ok_or("invalid Unicode scalar")?;
                                out.push(ch);
                            } else {
                                let ch = char::from_u32(code as u32).ok_or("invalid \\uXXXX code point")?;
                                out.push(ch);
                            }
                        }
                        other => return Err(format!("invalid JSON escape '\\{}'", other as char)),
                    }
                }
                c if c < 0x20 => return Err("unescaped control byte in JSON string".into()),
                first => {
                    if first < 0x80 {
                        out.push(first as char);
                    } else {
                        self.pos -= 1;
                        let rest = std::str::from_utf8(&self.bytes[self.pos..])
                            .map_err(|_| "invalid UTF-8 in JSON string".to_string())?;
                        let ch = rest.chars().next().ok_or("EOF in UTF-8 string")?;
                        out.push(ch);
                        self.pos += ch.len_utf8();
                    }
                }
            }
        }
        Err("unterminated JSON string".into())
    }

    fn parse_hex4(&mut self) -> Result<u16, String> {
        if self.pos + 4 > self.bytes.len() {
            return Err("truncated \\uXXXX escape".into());
        }
        let s = std::str::from_utf8(&self.bytes[self.pos..self.pos + 4]).map_err(|_| "invalid hex")?;
        self.pos += 4;
        u16::from_str_radix(s, 16).map_err(|_| format!("invalid hex escape: {s}"))
    }

    fn parse_number(&mut self) -> Result<JsonVal, String> {
        let start = self.pos;
        if self.peek() == Some(b'-') {
            self.pos += 1;
        }
        let mut has_digits = false;
        while matches!(self.peek(), Some(b'0'..=b'9')) {
            has_digits = true;
            self.pos += 1;
        }
        if !has_digits {
            return Err(format!("invalid number at offset {start}"));
        }
        let mut is_float = false;
        if self.peek() == Some(b'.') {
            is_float = true;
            self.pos += 1;
            while matches!(self.peek(), Some(b'0'..=b'9')) {
                self.pos += 1;
            }
        }
        if matches!(self.peek(), Some(b'e' | b'E')) {
            is_float = true;
            self.pos += 1;
            if matches!(self.peek(), Some(b'+' | b'-')) {
                self.pos += 1;
            }
            while matches!(self.peek(), Some(b'0'..=b'9')) {
                self.pos += 1;
            }
        }
        let slice = std::str::from_utf8(&self.bytes[start..self.pos]).map_err(|_| "utf8")?;
        if !is_float {
            if let Ok(n) = slice.parse::<i64>() {
                return Ok(JsonVal::Int(n));
            }
        }
        Ok(JsonVal::RawNum(slice.to_string()))
    }

    fn parse_array(&mut self) -> Result<JsonVal, String> {
        self.pos += 1;
        let mut items = Vec::new();
        self.skip_ws();
        if self.peek() == Some(b']') {
            self.pos += 1;
            return Ok(JsonVal::Arr(items));
        }
        loop {
            items.push(self.parse_val()?);
            self.skip_ws();
            match self.next() {
                Some(b',') => continue,
                Some(b']') => return Ok(JsonVal::Arr(items)),
                _ => return Err("expected ',' or ']' in JSON array".into()),
            }
        }
    }

    fn parse_object(&mut self) -> Result<JsonVal, String> {
        self.pos += 1;
        let mut map = BTreeMap::new();
        self.skip_ws();
        if self.peek() == Some(b'}') {
            self.pos += 1;
            return Ok(JsonVal::Obj(map));
        }
        loop {
            self.skip_ws();
            let k = self.parse_string()?;
            self.skip_ws();
            if self.next() != Some(b':') {
                return Err("expected ':' after object key".into());
            }
            let v = self.parse_val()?;
            map.insert(k, v);
            self.skip_ws();
            match self.next() {
                Some(b',') => continue,
                Some(b'}') => return Ok(JsonVal::Obj(map)),
                _ => return Err("expected ',' or '}' in JSON object".into()),
            }
        }
    }
}
