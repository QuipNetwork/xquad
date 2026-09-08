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
//! Two vectors are excluded by name (see [`VERIFIER_STRICT_EXCEPTIONS`])
//! because the verifier is *deliberately* stricter than the runtime for
//! `GETLINE`/`SETLINE`/`ADDLINE`: this is pre-existing, documented
//! behaviour, not a regression this sweep should flag.

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

/// Conformance vectors that run to completion on the VM but are
/// deliberately rejected by the static verifier's stricter typing rules.
///
/// `GETLINE`/`SETLINE`/`ADDLINE` accept either a model or a sample register
/// at runtime -- see their opcode docs in
/// `xqvm/src/bytecode/types/table.rs`, each of which ends with "`xquad
/// verify` requires a model". `RegisterTypePhase`
/// (`xqvm/src/verifier/reg_type.rs`) only ever records `RegType::Model` as
/// the accepted type for these three opcodes, so a register carrying a
/// sample is flagged even though the interpreter runs the program to
/// completion. This is intentional, pre-existing verifier behaviour, so
/// these two vectors are excluded by name rather than weakening the "clean
/// vectors verify clean" assertion for everyone else.
const VERIFIER_STRICT_EXCEPTIONS: &[(&str, &str)] = &[
    ("xqmx-grid", "sample_linear_ops"),
    ("xqmx-grid", "discrete_alloc"),
];

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

/// Collect `conformance/vectors/<category>/<vector>/` directories whose
/// `expected.json` has no top-level `error` key, excluding the documented
/// [`VERIFIER_STRICT_EXCEPTIONS`].
///
/// Returns the selected directories and the total number of vector
/// directories walked. The total is what a broken glob or a moved tree
/// would zero out, and unlike the selected count it does not move when a
/// vector's expected outcome changes -- so it is the number worth
/// asserting a floor on.
///
/// Panics if an exception in [`VERIFIER_STRICT_EXCEPTIONS`] no longer
/// exists in the tree, so a stale exception is caught rather than silently
/// doing nothing.
fn clean_vector_dirs(vectors_root: &Path) -> (Vec<PathBuf>, usize) {
    let mut dirs = Vec::new();
    let mut walked = 0usize;
    let mut exceptions_seen = vec![false; VERIFIER_STRICT_EXCEPTIONS.len()];

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
        let category_name = category.file_name().to_string_lossy().into_owned();

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
            let vector_name = vector.file_name().to_string_lossy().into_owned();
            walked += 1;

            if let Some(idx) = VERIFIER_STRICT_EXCEPTIONS
                .iter()
                .position(|(c, v)| *c == category_name && *v == vector_name)
                && let Some(seen) = exceptions_seen.get_mut(idx)
            {
                *seen = true;
                continue;
            }

            let dir = vector.path();
            let expected_path = dir.join("expected.json");
            if !expects_runtime_error(&expected_path) {
                dirs.push(dir);
            }
        }
    }

    for (seen, (category, vector)) in exceptions_seen.iter().zip(VERIFIER_STRICT_EXCEPTIONS) {
        assert!(
            *seen,
            "VERIFIER_STRICT_EXCEPTIONS entry {category}/{vector} no longer exists under {}; \
             remove the stale exception",
            vectors_root.display()
        );
    }

    (dirs, walked)
}

// ---------------------------------------------------------------------------
// Test
// ---------------------------------------------------------------------------

#[test]
fn verify_vectors() {
    let root = repo_root();
    let vectors_root = root.join("conformance").join("vectors");

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

    // Each exception must still be rejected. Checking only that the
    // directory exists leaves the list able to rot in the other direction:
    // if the verifier is later relaxed to accept a sample register on
    // GETLINE/SETLINE/ADDLINE, a stale exclusion would quietly keep two
    // vectors out of the sweep forever.
    for (category, vector) in VERIFIER_STRICT_EXCEPTIONS {
        let program_path = vectors_root
            .join(category)
            .join(vector)
            .join("program.xqasm");
        let source = fs::read_to_string(&program_path)
            .unwrap_or_else(|err| panic!("cannot read {}: {err}", program_path.display()));
        assert!(
            verify_source(&program_path, &source).is_err(),
            "{category}/{vector} now verifies clean; drop it from \
             VERIFIER_STRICT_EXCEPTIONS so the sweep covers it"
        );
    }

    println!(
        "verify_vectors: {swept} programs verified clean ({} of {walked} conformance \
         vectors, plus {} fixture programs)",
        clean_dirs.len(),
        fixture_paths.len()
    );
}
