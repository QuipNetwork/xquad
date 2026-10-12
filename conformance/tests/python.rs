// Copyright (C) 2026 Postquant Labs Incorporated
// SPDX-License-Identifier: AGPL-3.0-or-later

//! Generated conformance tests against the Python reference runtime.
//!
//! Each test shells out to `uv run --no-sync python -m xqvm_py run` and
//! compares the JSON output against `expected.json`.
//! Gated behind the `python` feature so CI can disable it on jobs
//! without a Python interpreter available.

#![cfg(feature = "python")]

include!(concat!(env!("OUT_DIR"), "/vector_tests_python.rs"));
