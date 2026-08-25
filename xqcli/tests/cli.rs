// Copyright (C) 2026 Postquant Labs Incorporated
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.
//
// SPDX-License-Identifier: AGPL-3.0-or-later

#![expect(
    clippy::expect_used,
    reason = "integration test helpers - panics on failure are intentional"
)]

//! Integration tests for the `xq` unified CLI.
//!
//! Each test invokes the `xq` binary through [`assert_cmd`] and inspects
//! exit status, stdout, and stderr with [`predicates`].

use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

use assert_cmd::Command;
use predicates::prelude::*;

// ── utilities ────────────────────────────────────────────────────────────────

/// Per-process counter used to produce unique temp-dir names.
static COUNTER: AtomicU64 = AtomicU64::new(0);

/// RAII temp directory.  Deleted on drop.
struct TempDir(PathBuf);

impl TempDir {
    fn new() -> Self {
        let n = COUNTER.fetch_add(1, Ordering::Relaxed);
        let dir = std::env::temp_dir().join(format!("xq-test-{}-{}", std::process::id(), n));
        std::fs::create_dir_all(&dir).expect("create temp dir");
        Self(dir)
    }

    /// Absolute path to `name` inside this directory.
    fn file(&self, name: &str) -> PathBuf {
        self.0.join(name)
    }

    /// Write `content` to `name` and return its path.
    fn write(&self, name: &str, content: &[u8]) -> PathBuf {
        let path = self.file(name);
        std::fs::write(&path, content).expect("write temp file");
        path
    }

    /// Write a UTF-8 string to `name` and return its path.
    fn write_str(&self, name: &str, content: &str) -> PathBuf {
        self.write(name, content.as_bytes())
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// Build a [`Command`] targeting the `xquad` binary.
fn xq() -> Command {
    Command::cargo_bin("xquad").expect("xq binary to exist")
}

/// Assemble an inline source string and return the raw bytecode bytes.
fn assemble_bytes(src: &str) -> Vec<u8> {
    let tmp = TempDir::new();
    let path = tmp.write_str("prog.xqasm", src);
    let output = xq()
        .args(["asm", "--stdout", path.to_str().expect("UTF-8 path")])
        .output()
        .expect("assembler invocation");
    assert!(output.status.success(), "assemble_bytes: assembler failed");
    output.stdout
}

// ── fixtures ─────────────────────────────────────────────────────────────────

/// Push integer 42 and halt.  Leaves `42` on the stack.
const PUSH_42: &str = "PUSH 42\nHALT\n";

/// Read calldata slot 0 into r0, push it, halt.  Stack = [calldata[0]].
const CALLDATA_R0: &str = "PUSH 0\nINPUT r0\nLOAD r0\nHALT\n";

/// Loop 1 000 000 times -- will always exceed a single-digit step limit.
const LOOP_1M: &str = "PUSH 0\nPUSH 1000000\nRANGE\n  LVAL r0\nNEXT\nHALT\n";

/// Source that is not valid XQASM.
const INVALID_SRC: &str = "NOTANOPCODE\n";

// ── xquad asm ────────────────────────────────────────────────────────────────

#[test]
fn asm_writes_default_xqb_file() {
    let tmp = TempDir::new();
    let src = tmp.write_str("prog.xqasm", PUSH_42);
    let _ = xq()
        .args(["asm", src.to_str().expect("UTF-8 path")])
        .assert()
        .success();
    assert!(
        tmp.file("prog.xqb").exists(),
        ".xqb output file not created"
    );
}

#[test]
fn asm_stdout_flag_emits_nonempty_bytes() {
    let tmp = TempDir::new();
    let src = tmp.write_str("prog.xqasm", PUSH_42);
    let output = xq()
        .args(["asm", "--stdout", src.to_str().expect("UTF-8 path")])
        .output()
        .expect("assembler invocation");
    assert!(output.status.success(), "assembler failed");
    assert!(!output.stdout.is_empty(), "stdout should contain bytecode");
}

#[test]
fn asm_custom_output_path() {
    let tmp = TempDir::new();
    let src = tmp.write_str("prog.xqasm", PUSH_42);
    let out = tmp.file("custom.xqb");
    let _ = xq()
        .args([
            "asm",
            "-o",
            out.to_str().expect("UTF-8"),
            src.to_str().expect("UTF-8"),
        ])
        .assert()
        .success();
    assert!(out.exists(), "custom output file not created");
}

#[test]
fn asm_invalid_source_exits_nonzero() {
    let tmp = TempDir::new();
    let src = tmp.write_str("bad.xqasm", INVALID_SRC);
    let _ = xq()
        .args(["asm", src.to_str().expect("UTF-8 path")])
        .assert()
        .failure();
}

// ── xquad dism ───────────────────────────────────────────────────────────────

#[test]
fn dism_file_listing_contains_push() {
    let tmp = TempDir::new();
    let bytes = assemble_bytes(PUSH_42);
    let xqb = tmp.write("prog.xqb", &bytes);
    let _ = xq()
        .args(["dism", xqb.to_str().expect("UTF-8 path")])
        .assert()
        .success()
        .stdout(predicate::str::contains("PUSH"));
}

#[test]
fn dism_reads_stdin_when_no_file() {
    let bytes = assemble_bytes(PUSH_42);
    let _ = xq()
        .arg("dism")
        .write_stdin(bytes)
        .assert()
        .success()
        .stdout(predicate::str::contains("PUSH"));
}

// ── xquad run ────────────────────────────────────────────────────────────────

#[test]
fn run_binary_file_prints_stack() {
    let tmp = TempDir::new();
    let bytes = assemble_bytes(PUSH_42);
    let xqb = tmp.write("prog.xqb", &bytes);
    let _ = xq()
        .args(["run", xqb.to_str().expect("UTF-8 path")])
        .assert()
        .success()
        .stdout(predicate::str::contains("42"));
}

#[test]
fn run_text_flag_assembles_before_running() {
    let tmp = TempDir::new();
    let src = tmp.write_str("prog.xqasm", PUSH_42);
    let _ = xq()
        .args(["run", "--text", src.to_str().expect("UTF-8 path")])
        .assert()
        .success()
        .stdout(predicate::str::contains("42"));
}

#[test]
fn run_calldata_reaches_input_instruction() {
    let tmp = TempDir::new();
    let src = tmp.write_str("prog.xqasm", CALLDATA_R0);
    let _ = xq()
        .args([
            "run",
            "--text",
            "--calldata",
            "99",
            src.to_str().expect("UTF-8 path"),
        ])
        .assert()
        .success()
        .stdout(predicate::str::contains("99"));
}

#[test]
fn run_trace_produces_stderr_output() {
    let tmp = TempDir::new();
    let src = tmp.write_str("prog.xqasm", PUSH_42);
    let _ = xq()
        .args([
            "run",
            "--text",
            "--trace",
            src.to_str().expect("UTF-8 path"),
        ])
        .assert()
        .success()
        .stderr(predicate::str::is_empty().not());
}

#[test]
fn run_trace_json_format_emits_json_objects() {
    let tmp = TempDir::new();
    let src = tmp.write_str("prog.xqasm", PUSH_42);
    let _ = xq()
        .args([
            "run",
            "--text",
            "--trace",
            "--trace-format",
            "json",
            src.to_str().expect("UTF-8 path"),
        ])
        .assert()
        .success()
        .stderr(predicate::str::contains("{"));
}

#[test]
fn run_trace_file_creates_nonempty_file() {
    let tmp = TempDir::new();
    let src = tmp.write_str("prog.xqasm", PUSH_42);
    let trace = tmp.file("trace.txt");
    let _ = xq()
        .args([
            "run",
            "--text",
            "--trace",
            "--trace-file",
            trace.to_str().expect("UTF-8"),
            src.to_str().expect("UTF-8 path"),
        ])
        .assert()
        .success();
    let size = std::fs::metadata(&trace)
        .expect("trace file should exist")
        .len();
    assert!(size > 0, "trace file is empty");
}

#[test]
fn run_step_limit_exceeded_exits_nonzero() {
    let tmp = TempDir::new();
    let src = tmp.write_str("prog.xqasm", LOOP_1M);
    let _ = xq()
        .args([
            "run",
            "--text",
            "--step-limit",
            "10",
            src.to_str().expect("UTF-8 path"),
        ])
        .assert()
        .failure();
}

#[test]
fn run_step_limit_zero_exits_nonzero() {
    // The limit is exact, so 0 is a real budget and not a sentinel: even a
    // two-instruction program is refused at its first instruction. `0 =
    // unlimited` made the safest looking value the most dangerous one.
    let tmp = TempDir::new();
    let src = tmp.write_str("prog.xqasm", PUSH_42);
    let _ = xq()
        .args([
            "run",
            "--text",
            "--step-limit",
            "0",
            src.to_str().expect("UTF-8 path"),
        ])
        .assert()
        .failure();
}

#[test]
fn run_unlimited_steps_removes_the_bound() {
    // A million-iteration loop is well past the default budget, so it
    // completes only if the flag actually removes the bound.
    let tmp = TempDir::new();
    let src = tmp.write_str("prog.xqasm", LOOP_1M);
    let _ = xq()
        .args([
            "run",
            "--text",
            "--unlimited-steps",
            src.to_str().expect("UTF-8 path"),
        ])
        .assert()
        .success();
}

#[test]
fn run_step_limit_and_unlimited_steps_conflict() {
    // clap rejects the pair before the VM is built, so this is exit 2 (a
    // usage error) rather than exit 1 (a program that faulted). The help
    // text used to say `--unlimited-steps` ignored `--step-limit`, which
    // described a precedence the parser does not implement.
    let tmp = TempDir::new();
    let src = tmp.write_str("prog.xqasm", PUSH_42);
    let _ = xq()
        .args([
            "run",
            "--text",
            "--step-limit",
            "10",
            "--unlimited-steps",
            src.to_str().expect("UTF-8 path"),
        ])
        .assert()
        .code(2);
}
