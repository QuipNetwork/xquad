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

//! The step-cost constants in `src/metering.rs` against the constants table
//! in `spec/xqvm/METERING.md`, name for name and value for value.
//!
//! Reads the spec from the workspace, so the packaged crate excludes this
//! target (see `Cargo.toml`).

#![expect(clippy::expect_used, reason = "test - a missing file should panic")]

use std::collections::BTreeMap;
use std::path::Path;

fn read(relative: &str) -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join(relative);
    std::fs::read_to_string(&path).expect("read metering source")
}

/// Every `pub const NAME: u64 = VALUE;` line.
fn rust_constants(text: &str) -> BTreeMap<String, u64> {
    text.lines()
        .filter_map(|line| {
            let (name, value) = line.strip_prefix("pub const ")?.split_once(": u64 = ")?;
            Some((name.to_owned(), value.strip_suffix(';')?.parse().ok()?))
        })
        .collect()
}

/// Every table row of the form ``| `NAME` | VALUE | ...``.
fn spec_constants(text: &str) -> BTreeMap<String, u64> {
    text.lines()
        .filter_map(|line| {
            let mut cells = line.strip_prefix('|')?.split('|').map(str::trim);
            let name = cells.next()?.strip_prefix('`')?.strip_suffix('`')?;
            let value = cells.next()?.parse().ok()?;
            Some((name.to_owned(), value))
        })
        .collect()
}

#[test]
fn metering_constants_match_the_spec_table() {
    let rust = rust_constants(&read("src/metering.rs"));
    let spec = spec_constants(&read("../spec/xqvm/METERING.md"));
    assert!(!rust.is_empty(), "no constants parsed from src/metering.rs");
    assert_eq!(
        rust, spec,
        "src/metering.rs and spec/xqvm/METERING.md's constants table disagree"
    );
}
