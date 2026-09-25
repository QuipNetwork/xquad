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

//! Verifier-clean sweep over conformance vectors and example fixtures.
//!
//! A program the VM runs to completion without a runtime error must also
//! verify clean: the verifier's whole purpose is to reject, ahead of time,
//! only programs that would misbehave at runtime. So every conformance
//! vector whose `expected.json` does NOT describe a runtime error, plus a
//! handful of hand-picked fixture programs the example runners and xqcp
//! tests compile against, is asserted here to pass `xqvm::verifier::verify`
//! cleanly. This sweep is the over-tightening signal a verifier change
//! needs: it fails the moment a verifier change starts rejecting programs
//! that are in fact valid, long before that regression would otherwise
//! surface as a mysterious conformance or example-smoke failure.
//!
//! The sweep has no exclusions. It carried two until QUI-1168, both there
//! because `RegisterTypePhase` required a model register for
//! `GETLINE`/`SETLINE`/`ADDLINE` while the runtime and `spec/xqvm/ISA.md`
//! both accepted a sample. That was a defect in the verifier rather than
//! deliberate strictness, and fixing it retired the exclusions with it.

#![expect(
    clippy::expect_used,
    clippy::panic,
    clippy::print_stdout,
    reason = "test harness: panics and stdout output are the expected failure/report channel for #[test]"
)]

use std::fs;
use std::path::{Path, PathBuf};

use xqasm::assemble_source;
use xqvm::verifier::verify;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Locate the repository root from the crate manifest directory rather than
/// the process working directory, which `cargo test` does not guarantee.
fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("xqasm crate directory has a parent (the repository root)")
        .to_path_buf()
}

/// Assemble `source` and run the verifier over it, folding either an
/// assembly failure or a verifier rejection into a single `path`-tagged
/// error string.
fn verify_source(path: &Path, source: &str) -> Result<(), String> {
    let program = assemble_source(source)
        .map_err(|err| format!("{}: failed to assemble: {err}", path.display()))?;
    verify(&program).map_err(|err| format!("{}: {err}", path.display()))
}

/// Does `expected.json` describe a runtime error? A vector's program is
/// only expected to verify clean when it does not.
fn expects_runtime_error(expected_path: &Path) -> bool {
    let text = fs::read_to_string(expected_path)
        .unwrap_or_else(|err| panic!("cannot read {}: {err}", expected_path.display()));
    let value: serde_json::Value = serde_json::from_str(&text)
        .unwrap_or_else(|err| panic!("cannot parse {}: {err}", expected_path.display()));
    value.get("error").is_some()
}

/// Collect `xqvm/tests/vectors/<category>/<vector>/` directories whose
/// `expected.json` has no top-level `error` key.
///
/// Returns the selected directories and the total number of vector
/// directories walked. The total is what a broken glob or a moved tree
/// would zero out, and unlike the selected count it does not move when a
/// vector's expected outcome changes -- so it is the number worth
/// asserting a floor on.
fn clean_vector_dirs(vectors_root: &Path) -> (Vec<PathBuf>, usize) {
    let mut dirs = Vec::new();
    let mut walked = 0usize;

    let categories = fs::read_dir(vectors_root)
        .unwrap_or_else(|err| panic!("cannot read {}: {err}", vectors_root.display()));
    for category in categories {
        let category = category.expect("readdir entry for a vector category");
        let category_type = category
            .file_type()
            .unwrap_or_else(|err| panic!("cannot stat {}: {err}", category.path().display()));
        if !category_type.is_dir() {
            continue;
        }
        let vectors = fs::read_dir(category.path())
            .unwrap_or_else(|err| panic!("cannot read {}: {err}", category.path().display()));
        for vector in vectors {
            let vector = vector.expect("readdir entry for a vector directory");
            let vector_type = vector
                .file_type()
                .unwrap_or_else(|err| panic!("cannot stat {}: {err}", vector.path().display()));
            if !vector_type.is_dir() {
                continue;
            }
            walked += 1;

            let dir = vector.path();
            let expected_path = dir.join("expected.json");
            if !expects_runtime_error(&expected_path) {
                dirs.push(dir);
            }
        }
    }

    (dirs, walked)
}

// ---------------------------------------------------------------------------
// Test
// ---------------------------------------------------------------------------

#[test]
fn verify_vectors() {
    let root = repo_root();
    let vectors_root = root.join("xqvm").join("tests").join("vectors");

    let (clean_dirs, walked) = clean_vector_dirs(&vectors_root);

    // A floor on the directories WALKED, not on the ones selected. The
    // selected count falls whenever a vector is added that expects a
    // runtime error -- a routine change -- and a floor near it would fail
    // with "broken glob" pointing at nothing of the sort. The walked count
    // moves only when vectors are added or removed, so a floor well below
    // the current 122 catches the failure this is actually for: the tree
    // moved and the sweep quietly covered nothing.
    assert!(
        walked >= 50,
        "walked only {walked} vector directories under {} -- broken glob or moved directory?",
        vectors_root.display()
    );
    assert!(
        !clean_dirs.is_empty(),
        "expected to find verify-clean conformance vectors under {}",
        vectors_root.display()
    );

    let mut swept = 0usize;
    let mut failures = Vec::new();
    for dir in &clean_dirs {
        let program_path = dir.join("program.xqasm");
        let source = fs::read_to_string(&program_path)
            .unwrap_or_else(|err| panic!("cannot read {}: {err}", program_path.display()));
        if let Err(err) = verify_source(&program_path, &source) {
            failures.push(err);
        }
        swept += 1;
    }

    let fixture_paths = [
        root.join("xqcp/tests/fixtures/maxcut/decoder.xqasm"),
        root.join("xqcp/tests/fixtures/maxcut/encoder.xqasm"),
        root.join("xqcp/tests/fixtures/maxcut/verifier.xqasm"),
        root.join("xqcp/tests/fixtures/tsp/decoder.xqasm"),
        root.join("xqcp/tests/fixtures/tsp/encoder.xqasm"),
        root.join("xqcp/tests/fixtures/tsp/verifier.xqasm"),
        root.join("xqvm/examples/tsp/decoder.xqasm"),
        root.join("xqvm/examples/tsp/encoder.xqasm"),
        root.join("xqvm/examples/tsp/verifier.xqasm"),
    ];

    for path in &fixture_paths {
        assert!(
            path.exists(),
            "expected fixture program to exist: {}",
            path.display()
        );
        let source = fs::read_to_string(path)
            .unwrap_or_else(|err| panic!("cannot read {}: {err}", path.display()));
        if let Err(err) = verify_source(path, &source) {
            failures.push(err);
        }
        swept += 1;
    }

    assert!(
        failures.is_empty(),
        "{} of {swept} programs failed to verify clean:\n{}",
        failures.len(),
        failures.join("\n")
    );

    println!(
        "verify_vectors: {swept} programs verified clean ({} of {walked} conformance \
         vectors, plus {} fixture programs)",
        clean_dirs.len(),
        fixture_paths.len()
    );
}
