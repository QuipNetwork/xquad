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

//! `xquad dism` subcommand -- disassembles XQVM bytecode.

use std::io::{self, Read, Write};
use std::path::PathBuf;

use clap::Parser;
use miette::{IntoDiagnostic, WrapErr};

use xqvm::Program;
use xqvm::disasm::Disassembly;

/// Disassemble XQVM bytecode into a human-readable listing.
///
/// Reads raw bytecode from FILE (or stdin when FILE is omitted) and prints
/// a listing to stdout.  Jump targets are labelled `.0`, `.1`, ... from the
/// jump table.
#[derive(Debug, Parser)]
pub(crate) struct Args {
    /// Bytecode file to disassemble.  Reads from stdin when omitted.
    file: Option<PathBuf>,
}

/// Execute the `dism` subcommand.
pub(crate) fn exec(args: &Args) -> miette::Result<()> {
    let bytes = if let Some(path) = &args.file {
        std::fs::read(path)
            .into_diagnostic()
            .wrap_err_with(|| format!("failed to read '{}'", path.display()))?
    } else {
        let mut buf = Vec::new();
        let _n = io::stdin()
            .read_to_end(&mut buf)
            .into_diagnostic()
            .wrap_err("failed to read stdin")?;
        buf
    };

    let mut out = Vec::new();
    let program = Program::decode(&bytes)
        .into_diagnostic()
        .wrap_err("failed to decode program")?;
    Disassembly::from_program(&program)
        .write_to(&mut out)
        .into_diagnostic()
        .wrap_err("failed to write disassembly")?;
    io::stdout().write_all(&out).into_diagnostic()?;

    Ok(())
}
