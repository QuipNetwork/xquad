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

//! `xquad run` subcommand -- runs XQVM bytecode with optional tracing.

use std::fs::File;
use std::io::{BufWriter, Write};
use std::path::PathBuf;

use clap::Parser;
use miette::{IntoDiagnostic, WrapErr};

use xqasm::assemble_source;
use xqvm::Program;
use xqvm::{JsonTracer, RegVal, TextTracer, Vm};

/// Trace output format.
#[derive(Clone, Debug, clap::ValueEnum)]
enum TraceFormat {
    /// Human-readable aligned columns.
    Text,
    /// One JSON object per line (JSONL).
    Json,
}

/// Run XQVM bytecode or assembly.
///
/// Reads a program from FILE and executes it.  Use `--text` to accept
/// assembly source instead of a binary bytecode file.  Add `--trace` for
/// a step-by-step execution log.
#[derive(Debug, Parser)]
pub(crate) struct Args {
    /// Treat FILE as assembly text and assemble before running.
    #[arg(long)]
    text: bool,

    /// Comma-separated calldata integers passed to INPUT instructions.
    #[arg(long, value_delimiter = ',')]
    calldata: Vec<i64>,

    /// Number of output slots available for OUTPUT instructions.
    #[arg(long, default_value = "16")]
    outputs: usize,

    /// Maximum number of instructions to execute.
    ///
    /// Exact: `--step-limit 0` executes nothing. Pass `--unlimited-steps` to
    /// run without a bound.
    #[arg(long, default_value_t = xqvm::DEFAULT_STEP_LIMIT)]
    step_limit: u64,

    /// Run without a step limit.
    ///
    /// Conflicts with `--step-limit`: opting out of the bound has to be said
    /// aloud, and saying it two ways at once is an error rather than a
    /// precedence question. A program that never halts will not return.
    #[arg(long, conflicts_with = "step_limit")]
    unlimited_steps: bool,

    /// Allocation budget in bytes for models, samples and vectors.
    // Restates `xqvm::DEFAULT_MEMORY_LIMIT` rather than naming it, unlike
    // `--step-limit` above. `release:validate` runs `cargo publish
    // --dry-run --workspace`, which builds this crate against the
    // *published* `xqvm` rather than the workspace path, so a constant this
    // crate names has to exist in the last published `xqvm` --
    // `DEFAULT_MEMORY_LIMIT` became public in the same MR as this comment
    // (QUI-1289) and is not published yet. Name it once an `xqvm` carrying
    // it is on crates.io. Not a doc comment: clap renders those as
    // `--help` text, and this is build-system state a user has no use for.
    #[arg(long, default_value = "1073741824")]
    memory_limit: u64,

    /// Enable step-by-step execution tracing.
    #[arg(long)]
    trace: bool,

    /// Trace output format (requires --trace).
    #[arg(long, default_value = "text", requires = "trace")]
    trace_format: TraceFormat,

    /// Write trace output to a file instead of stderr (requires --trace).
    #[arg(long, requires = "trace")]
    trace_file: Option<PathBuf>,

    /// Bytecode (or assembly) file to run.
    file: PathBuf,
}

/// Execute the `run` subcommand.
pub(crate) fn exec(args: Args) -> miette::Result<()> {
    let source = std::fs::read(&args.file)
        .into_diagnostic()
        .wrap_err_with(|| format!("failed to read '{}'", args.file.display()))?;

    let program: Program = if args.text {
        let text = String::from_utf8(source)
            .into_diagnostic()
            .wrap_err("assembly file is not valid UTF-8")?;
        assemble_source(&text)
            .map_err(|e| miette::miette!("{e}"))
            .wrap_err("assembly failed")?
    } else {
        Program::decode(&source)
            .into_diagnostic()
            .wrap_err("failed to decode program")?
    };

    let calldata: Vec<RegVal> = args.calldata.into_iter().map(RegVal::Int).collect();

    let mut vm = Vm::new();
    let _ = vm.set_calldata(calldata).set_output_slots(args.outputs);
    if args.unlimited_steps {
        let _ = vm.set_unlimited_steps();
    } else {
        let _ = vm.set_step_limit(args.step_limit);
    }
    let _ = vm.set_memory_limit(args.memory_limit);

    let file_name = args.file.to_string_lossy();
    if args.trace {
        let writer: Box<dyn Write> = match &args.trace_file {
            Some(path) => {
                let file = File::create(path)
                    .into_diagnostic()
                    .wrap_err_with(|| format!("failed to create '{}'", path.display()))?;
                Box::new(BufWriter::new(file))
            }
            None => Box::new(BufWriter::new(std::io::stderr())),
        };
        match args.trace_format {
            TraceFormat::Text => {
                let mut tracer = TextTracer::new(writer);
                vm.run_trace(&mut tracer, &program)
                    .map_err(|e| e.into_diagnostic(&program, &file_name))?;
            }
            TraceFormat::Json => {
                let mut tracer = JsonTracer::new(writer);
                vm.run_trace(&mut tracer, &program)
                    .map_err(|e| e.into_diagnostic(&program, &file_name))?;
            }
        }
    } else {
        vm.run(&program)
            .map_err(|e| e.into_diagnostic(&program, &file_name))?;
    }

    print_results(&vm);
    Ok(())
}

/// Print VM output slots and remaining stack to stdout.
fn print_results(vm: &Vm) {
    let outputs = vm.outputs();
    let has_outputs = outputs.iter().any(|v| !matches!(v, RegVal::Unset));
    if has_outputs {
        println!("outputs:");
        for (i, v) in outputs.iter().enumerate() {
            if !matches!(v, RegVal::Unset) {
                println!("  [{i}] = {v:?}");
            }
        }
    }

    let stack = vm.stack();
    if !stack.is_empty() {
        println!("stack (bottom to top):");
        for v in stack {
            println!("  {v}");
        }
    }
}
