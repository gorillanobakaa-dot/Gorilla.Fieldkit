//! agy-tools: small deterministic validators for agent config files (Linux and Windows, std only).
//! Imported from SOURCE; no listener, no network, no catalog, nothing is executed from the files read.

#![allow(dead_code)] // shared helpers: later workers add tools that use them

mod common;
mod doors;
mod validators;
mod validators_port;

use std::env;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

use validators::{check_cron, check_cron_with_next, check_plugin_dir, check_rules_budget, check_skill_opts};
use validators_port::{check_hooks, check_json_config, check_mcp_config, check_workflow_migrate};

fn usage() {
    eprintln!(
        "agy-tools - deterministic validators for agent config files\n\n\
         run TOOL TARGET [FLAGS] [--json]\n\
           skill [--strict-names] | rules | plugin | cron [--next N] | hooks | mcp [--check-bins] |\n\
           json-config [--check-paths] | workflow-migrate [--mode preview|apply|verify|undo] |\n\
           doors [FILE...] [--home DIR] [--check-bins]"
    );
}

#[derive(Default)]
struct RunFlags {
    strict_names: bool,
    check_bins: bool,
    check_paths: bool,
    next_count: usize,
    mode: String,
    home: Option<PathBuf>,
    positional: Vec<String>,
}

fn parse_run_flags(args: &[&str]) -> Result<RunFlags, String> {
    let mut f = RunFlags { mode: "preview".into(), ..Default::default() };
    let mut i = 0;
    while i < args.len() {
        match args[i] {
            "--strict-names" => f.strict_names = true,
            "--check-bins" => f.check_bins = true,
            "--check-paths" => f.check_paths = true,
            "--next" => {
                i += 1;
                let s = args.get(i).ok_or("--next needs N")?;
                f.next_count = s.parse::<usize>().map_err(|_| format!("invalid --next value '{s}'"))?;
            }
            "--mode" => {
                i += 1;
                f.mode = (*args.get(i).ok_or("--mode needs VALUE")?).to_string();
            }
            "--home" => {
                i += 1;
                f.home = Some(PathBuf::from(args.get(i).ok_or("--home needs PATH")?));
            }
            other => f.positional.push(other.to_string()),
        }
        i += 1;
    }
    Ok(f)
}

fn run_validator_cli(args: &[&str], as_json: bool) -> i32 {
    if args.is_empty() {
        usage();
        return 2;
    }
    let tool = args[0];
    let flags = match parse_run_flags(&args[1..]) {
        Ok(f) => f,
        Err(e) => {
            eprintln!("error: {e}");
            return 2;
        }
    };
    let target = flags.positional.first().map(String::as_str).unwrap_or(".");
    match tool {
        "skill" => check_skill_opts(Path::new(target), flags.strict_names).print(as_json),
        "rules" => check_rules_budget(Path::new(target)).print(as_json),
        "plugin" => check_plugin_dir(Path::new(target)).print(as_json),
        "cron" => {
            if flags.next_count == 0 {
                check_cron(target).print(as_json)
            } else {
                check_cron_with_next(target, flags.next_count).print(as_json)
            }
        }
        "hooks" => check_hooks(Path::new(target)).print(as_json),
        "mcp" => check_mcp_config(Path::new(target), flags.check_bins).print(as_json),
        "json-config" => check_json_config(Path::new(target), flags.check_paths).print(as_json),
        "workflow-migrate" => check_workflow_migrate(Path::new(target), &flags.mode).print(as_json),
        "doors" => {
            let files: Vec<PathBuf> = if flags.positional.is_empty() {
                let home = flags.home.clone().unwrap_or_else(common::home_dir);
                let cwd = env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
                doors::default_config_files(&home, &cwd)
            } else {
                flags.positional.iter().map(PathBuf::from).collect()
            };
            doors::check_doors(&files, flags.check_bins).print(as_json)
        }
        _ => {
            eprintln!("unknown tool: {tool}");
            2
        }
    }
}

fn main() -> ExitCode {
    let raw_args: Vec<String> = env::args().skip(1).collect();
    let as_json = raw_args.iter().any(|a| a == "--json");
    let args: Vec<&str> = raw_args.iter().map(String::as_str).filter(|a| *a != "--json").collect();
    if args.is_empty() || matches!(args[0], "-h" | "--help") {
        usage();
        return ExitCode::from(0);
    }
    let code = match args[0] {
        "run" => run_validator_cli(&args[1..], as_json),
        other => {
            eprintln!("unknown command: {other}");
            usage();
            2
        }
    };
    ExitCode::from(code as u8)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cron_validator_catches_bad_input() {
        assert_eq!(check_cron("*/15 * * * 1-5").exit_code, 0);
        assert_eq!(check_cron("99 * * * *").exit_code, 3);
    }

    #[test]
    fn flags_parse() {
        let f = parse_run_flags(&["x", "--next", "3", "--check-bins"]).unwrap();
        assert_eq!(f.next_count, 3);
        assert!(f.check_bins);
        assert!(parse_run_flags(&["--next", "z"]).is_err());
    }
}
