# Quantum Gate ISA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a gate-based quantum ISA as a separate `.xqg` binary format with CPU, IBM Quantum, and IonQ backends, enabling XQuad to target real quantum hardware alongside the existing QUBO optimization path.

**Architecture:** A new Rust crate `xqgbc` defines the gate opcode table (X-macro), `.xqg` binary format (15-byte header + instruction stream), codec, and static verifier. A new Python package `xqgb` provides a pure-Python decoder for `.xqg` files, a `GateSolver` ABC mirroring `xqsa`'s `Solver`, a NumPy statevector CPU simulator, and backend adapters for IBM Quantum (Qiskit) and IonQ (ionq SDK), all tied together by a registry factory.

**Tech Stack:** Rust 2024 / `no_std+alloc` (core types), `crc32fast` (CRC), `thiserror`; Python 3.13+ / `numpy>=2.0`, optional `qiskit>=1.0` + `qiskit-aer>=0.14` + `qiskit-ibm-runtime>=0.20`, optional `ionq>=0.3`

## Consolidated work items (W1–W7)

The Linear epic (QUI-724) groups these tasks into seven work items. The granular tasks below are the implementation sub-steps; this table maps each to its W-item.

| Work item | Composed of | Linear ticket |
|---|---|---|
| **W1** — `xqgbc` Rust crate | W1 Parts 1–3 (former Tasks 1–3) | QUI-725 |
| **W2** — `xqgb` Python core | W2 Parts 1–2 (former Tasks 4–5) | QUI-728 |
| **W3** — `xqgb` hardware backends | W3 Parts 1–2 (former Tasks 6–7) | QUI-730 |
| **W4** — Conformance harness | former Task 8 | QUI-732 |
| **W5** — Gate primitives | former Task 10 | QUI-739 |
| **W6** — Classical control flow + register file | W6 Parts 1–3 (former Tasks 9, 11, 13) | QUI-738 |
| **W7** — Parameterized circuits / header v2 | former Task 12 | QUI-741 |

> Section order in this document predates the consolidation: W6's three parts appear below as the former Task 9 (Part 1), Task 11 (Part 2), and Task 13 (Part 3), interleaved with W5 and W7. Follow the W-numbering, not the document order.

> W4 should assert that the Rust opcode set, the Python opcode set, and `gate_opcodes.yaml` are all equal, rather than checking a hardcoded opcode count — this lets W5, W6, and W7 proceed in parallel once W4 lands.

## Global Constraints

- `xqgbc` must be `no_std + alloc` with a `std` feature gate, matching `xqvm`'s portability posture
- All Rust code must pass `cargo clippy --all-features -- -D warnings` with the workspace `clippy.toml`
- Python must pass `ruff check` and `ruff format --check` at the repo root
- Google-style docstrings on all public Python APIs
- Gate opcode bytes live in 0x80–0xC2 (the range the XQVM spec reserves as illegal for `.xqb`)
- Gate angles are stored as `u32` binary-angular units (full turn = 2^32); conversion to radians happens only at the simulator/backend boundary, never on-chain — this eliminates NaN/−0 hazards and halves angle storage cost vs f64
- No modifications to `xqvm`, `xqasm`, or `xqvm_py` — gate ISA is purely additive in new crates/packages
- Add `xqgbc` to root `Cargo.toml` `members`; add `xqgb` to root `pyproject.toml` `[tool.uv.workspace] members`

---

## File Map

### New: `xqgbc/` (Rust crate)

| File | Purpose |
|---|---|
| `xqgbc/Cargo.toml` | Crate manifest with `crc32fast`, `thiserror`, `pastey` deps |
| `xqgbc/src/lib.rs` | Public facade re-exporting all public types |
| `xqgbc/src/table.rs` | `gate_opcodes!` X-macro (26 gate instructions) |
| `xqgbc/src/types/mod.rs` | Re-exports from sub-modules |
| `xqgbc/src/types/operand.rs` | `Qubit(u8)`, `Cbit(u8)`, `GateAngle(u32)` newtypes (full turn = 2^32 units) |
| `xqgbc/src/types/opcode.rs` | `GateOpcode` `#[repr(u8)]` enum derived from X-macro |
| `xqgbc/src/types/instruction.rs` | `GateInstruction` enum derived from X-macro |
| `xqgbc/src/codec.rs` | `EncodeOperand`/`DecodeOperand` traits + `impl_gate_codec!` macro |
| `xqgbc/src/program.rs` | `GateCircuit` struct + `.xqg` encode/decode with 15-byte header |
| `xqgbc/src/verifier.rs` | `static_verify()` single-pass qubit bounds + MEAS presence check |
| `xqgbc/tests/roundtrip.rs` | Integration: encode → decode roundtrips for every opcode |

### New: `xqgb/` (Python package)

| File | Purpose |
|---|---|
| `xqgb/pyproject.toml` | Package manifest; extras `[ibm]`, `[ionq]` |
| `xqgb/__init__.py` | Re-exports public API |
| `xqgb/decoder.py` | Pure-Python `.xqg` header + instruction decoder |
| `xqgb/solver.py` | `GateSolverResult`, `GateSolver` ABC |
| `xqgb/transpiler.py` | `GateCircuitTranspiler[T]` ABC; `QiskitTranspiler`; `IonQTranspiler` |
| `xqgb/cpu_sim.py` | `StatevectorSimulator` + `SolverCPUSim(GateSolver)` |
| `xqgb/ibm_quantum.py` | `SolverIBMQuantum(GateSolver)` |
| `xqgb/ionq.py` | `SolverIonQ(GateSolver)` |
| `xqgb/registry.py` | `GATE_SOLVERS` dict + `build_gate_solver(name, **kwargs)` |
| `xqgb/tests/test_decoder.py` | Decoder unit tests (no hardware) |
| `xqgb/tests/test_cpu_sim.py` | Statevector sim: Bell state, GHZ, Pauli correctness |
| `xqgb/tests/test_registry.py` | Registry smoke tests |

### New: conformance additions

| File | Purpose |
|---|---|
| `conformance/gate_opcodes.yaml` | Canonical source of truth for all 25 gate opcodes: code, mnemonic, operand types and wire widths |
| `conformance/vectors/gate/<name>/circuit.xqg` | Pre-encoded `.xqg` binary blobs (one per operand shape); committed ground truth |
| `conformance/vectors/gate/<name>/expected_parse.json` | Decoded instruction list each blob must parse to |
| `conformance/vectors/gate/bell_state/circuit.xqg` | 2-qubit Bell circuit; simulation conformance vector |
| `conformance/vectors/gate/bell_state/expected_counts.json` | Measurement distribution bounds (min fraction per outcome) |
| `scripts/check-gate-opcode-parity.py` | CI script: validates Rust `gate_opcodes!` table and Python `_OPCODE_TABLE` both match `gate_opcodes.yaml` |
| `scripts/gen-gate-vectors.py` | Generator: reads `gate_opcodes.yaml`, emits codec vectors under `conformance/vectors/gate/` |

---

## W1 · Part 1 — `xqgbc` Rust Crate: Scaffold + Opcode Table

**Files:**
- Create: `xqgbc/Cargo.toml`
- Create: `xqgbc/src/lib.rs`
- Create: `xqgbc/src/table.rs`
- Create: `xqgbc/src/types/mod.rs`
- Create: `xqgbc/src/types/operand.rs`
- Modify: `Cargo.toml` (root) — add `xqgbc` to `members`

**Interfaces:**
- Produces: `gate_opcodes!` X-macro usable by later tasks to generate `GateOpcode`, `GateInstruction`, and codec

- [ ] **Step 1: Add `xqgbc` to root workspace**

In `Cargo.toml` (root), add `"xqgbc"` to the `members` array:
```toml
members = [
    "xqvm",
    "xqasm",
    "xqcli",
    "conformance",
    "xqffi",
    "xqgbc",             # <-- add this
    "fixtures/xqvm-wasm",
]
```

- [ ] **Step 2: Write `xqgbc/Cargo.toml`**

```toml
[package]
name        = "xqgbc"
version     = "0.1.0"
edition     = "2021"
description = "Gate-based quantum bytecode (XQG) format, codec, and verifier."
repository.workspace = true
license.workspace    = true

[features]
default = ["std"]
std     = ["crc32fast/std", "thiserror/std"]

[dependencies]
crc32fast = { version = "1.5", default-features = false }
thiserror  = { version = "2", default-features = false }
pastey     = { workspace = true }

[dev-dependencies]
std::f64::consts = []   # accessed in tests directly; no extra dep needed
```

- [ ] **Step 3: Write `xqgbc/src/types/operand.rs`**

```rust
// Copyright (C) 2026 Postquant Labs Incorporated
// SPDX-License-Identifier: AGPL-3.0-or-later

/// A qubit index operand (max 255 qubits in v1).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Qubit(pub u8);

/// A classical bit index operand.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Cbit(pub u8);

/// A rotation angle stored as a u32 binary-angular value.
///
/// Full turn (2π radians) = 2^32 units. This representation is
/// deterministic on all platforms (no NaN, no −0.0, no f64 edge cases)
/// and half the wire size of an f64. Resolution is ~1.46 × 10⁻⁹ rad,
/// well below the noise floor of any real quantum hardware.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct GateAngle(pub u32);

impl GateAngle {
    /// Convert to radians for gate matrix computation.
    ///
    /// Only call this at the simulator/backend boundary — not on-chain.
    pub fn to_radians(self) -> f64 {
        self.0 as f64 * (core::f64::consts::TAU / 4_294_967_296.0)
    }

    /// Encode the nearest representable u32 for a radian value.
    ///
    /// Only used in tests and off-chain tooling (assemblers, compilers).
    pub fn from_radians(r: f64) -> Self {
        let normalised = r.rem_euclid(core::f64::consts::TAU);
        Self((normalised / core::f64::consts::TAU * 4_294_967_296.0).round() as u32)
    }

    /// Encode as 4 big-endian bytes.
    pub fn to_be_bytes(self) -> [u8; 4] { self.0.to_be_bytes() }

    /// Decode from 4 big-endian bytes.
    pub fn from_be_bytes(b: [u8; 4]) -> Self { Self(u32::from_be_bytes(b)) }
}
```

- [ ] **Step 4: Write `xqgbc/src/table.rs`** (the X-macro — single source of truth)

```rust
// Copyright (C) 2026 Postquant Labs Incorporated
// SPDX-License-Identifier: AGPL-3.0-or-later

/// Invoke `$mac!` with the complete XQG gate opcode table.
///
/// Each entry is an enum variant with a `#[gate(...)]` attribute and an
/// optional named-field body, mirroring Rust enum variant syntax:
///
/// ```text
/// /// Doc comment.
/// #[gate(code, "MNEMONIC", qubit_arity)]
/// VariantName { field: Type, ... },
/// ```
///
/// | Attribute position | Type | Description |
/// |---|---|---|
/// | `code` | `u8` literal | Wire-encoding byte (0x80–0xC2) |
/// | `"MNEMONIC"` | `str` literal | Uppercase assembly mnemonic |
/// | `qubit_arity` | `u8` literal | Number of qubit operands; used by the static verifier |
///
/// `GateAngle` fields are not counted toward `qubit_arity`.
/// All opcodes live in 0x80–0xC2, the range `.xqb` reserves as illegal.
///
/// Consumer macros match the pattern:
/// ```rust,ignore
/// macro_rules! my_consumer {
///     (
///         $(
///             $(#[doc = $doc:literal])*
///             #[gate($code:literal, $mnem:literal, $arity:literal)]
///             $var:ident $({ $($fields:tt)* })?
///         ),* $(,)?
///     ) => { /* ... */ }
/// }
/// ```
#[macro_export]
macro_rules! gate_opcodes {
    ($mac:ident) => {
        $mac! {
            // --- Circuit control ---
            /// No operation.
            #[gate(0x80, "QNOP", 0)]
            QNop,

            /// End of circuit.
            #[gate(0x81, "QHALT", 0)]
            QHalt,

            /// Optimization barrier; no physical effect.
            #[gate(0x82, "BARRIER", 0)]
            Barrier,

            /// Measure qubit q into classical bit c.
            #[gate(0x83, "MEAS", 1)]
            Meas { q: $crate::Qubit, c: $crate::Cbit },

            // --- Fixed single-qubit gates ---
            /// Hadamard gate.
            #[gate(0x90, "H", 1)]
            H { q: $crate::Qubit },

            /// Pauli-X (NOT) gate.
            #[gate(0x91, "X", 1)]
            X { q: $crate::Qubit },

            /// Pauli-Y gate.
            #[gate(0x92, "Y", 1)]
            Y { q: $crate::Qubit },

            /// Pauli-Z gate.
            #[gate(0x93, "Z", 1)]
            Z { q: $crate::Qubit },

            /// S gate (Z^(1/2)).
            #[gate(0x94, "S", 1)]
            S { q: $crate::Qubit },

            /// S-dagger gate.
            #[gate(0x95, "SDG", 1)]
            Sdg { q: $crate::Qubit },

            /// T gate (Z^(1/4)).
            #[gate(0x96, "T", 1)]
            T { q: $crate::Qubit },

            /// T-dagger gate.
            #[gate(0x97, "TDG", 1)]
            Tdg { q: $crate::Qubit },

            /// Square-root-of-X gate.
            #[gate(0x98, "SX", 1)]
            Sx { q: $crate::Qubit },

            // --- Parametric single-qubit gates ---
            /// X-axis rotation by angle theta.
            #[gate(0xA0, "RX", 1)]
            Rx { q: $crate::Qubit, theta: $crate::GateAngle },

            /// Y-axis rotation by angle theta.
            #[gate(0xA1, "RY", 1)]
            Ry { q: $crate::Qubit, theta: $crate::GateAngle },

            /// Z-axis rotation by angle theta.
            #[gate(0xA2, "RZ", 1)]
            Rz { q: $crate::Qubit, theta: $crate::GateAngle },

            /// Phase gate P(lambda).
            #[gate(0xA3, "P", 1)]
            P { q: $crate::Qubit, lambda: $crate::GateAngle },

            /// General single-qubit unitary U(theta, phi, lambda).
            #[gate(0xA4, "U", 1)]
            U { q: $crate::Qubit, theta: $crate::GateAngle, phi: $crate::GateAngle, lambda: $crate::GateAngle },

            // --- Fixed two-qubit gates ---
            /// Controlled-NOT (CX) gate.
            #[gate(0xB0, "CNOT", 2)]
            Cnot { ctrl: $crate::Qubit, tgt: $crate::Qubit },

            /// Controlled-Z gate.
            #[gate(0xB1, "CZ", 2)]
            Cz { ctrl: $crate::Qubit, tgt: $crate::Qubit },

            /// Swap two qubits.
            #[gate(0xB2, "SWAP", 2)]
            Swap { q0: $crate::Qubit, q1: $crate::Qubit },

            /// iSWAP gate.
            #[gate(0xB3, "ISWAP", 2)]
            ISwap { q0: $crate::Qubit, q1: $crate::Qubit },

            // --- Parametric two-qubit gates ---
            /// Controlled-phase gate.
            #[gate(0xC0, "CP", 2)]
            Cp { ctrl: $crate::Qubit, tgt: $crate::Qubit, lambda: $crate::GateAngle },

            /// ZZ-rotation entangling gate.
            #[gate(0xC1, "RZZ", 2)]
            Rzz { q0: $crate::Qubit, q1: $crate::Qubit, theta: $crate::GateAngle },

            /// Controlled-RX gate.
            #[gate(0xC2, "CRX", 2)]
            Crx { ctrl: $crate::Qubit, tgt: $crate::Qubit, theta: $crate::GateAngle },
        }
    };
}
```

- [ ] **Step 5: Write `xqgbc/src/types/mod.rs`** (placeholder for W1 Part 2 types)

```rust
pub mod operand;
pub use operand::{Cbit, GateAngle, Qubit};
// opcode and instruction modules added in W1 Part 2
```

- [ ] **Step 6: Write `xqgbc/src/lib.rs`**

```rust
// Copyright (C) 2026 Postquant Labs Incorporated
// SPDX-License-Identifier: AGPL-3.0-or-later

#![cfg_attr(not(feature = "std"), no_std)]
#[cfg(not(feature = "std"))]
extern crate alloc;

#[macro_use]
mod table;

mod types;
pub use types::{Cbit, GateAngle, Qubit};
pub use gate_opcodes;
```

- [ ] **Step 7: Verify it compiles**

```bash
cargo check -p xqgbc
```

Expected: 0 errors, 0 warnings.

- [ ] **Step 8: Commit**

```bash
git add xqgbc/ Cargo.toml Cargo.lock
git commit -m "feat(xqgbc): scaffold crate with gate_opcodes! X-macro and operand types"
```

---

## W1 · Part 2 — `xqgbc`: Opcode Enum, Instruction Enum, and Codec

**Files:**
- Create: `xqgbc/src/types/opcode.rs`
- Create: `xqgbc/src/types/instruction.rs`
- Create: `xqgbc/src/codec.rs`
- Modify: `xqgbc/src/types/mod.rs`
- Modify: `xqgbc/src/lib.rs`

**Interfaces:**
- Consumes: `gate_opcodes!` from W1 Part 1; `Qubit`, `Cbit`, `GateAngle` from W1 Part 1
- Produces: `GateOpcode`, `GateInstruction`, `codec::encode(&GateInstruction) -> Vec<u8>`, `codec::decode(&[u8]) -> Result<(GateInstruction, usize), DecodeError>`

- [ ] **Step 1: Write failing roundtrip tests** in `xqgbc/tests/roundtrip.rs`

```rust
use xqgbc::{GateInstruction, Qubit, Cbit, GateAngle};
use xqgbc::codec;

#[test]
fn roundtrip_h() {
    let instr = GateInstruction::H { q: Qubit(0) };
    let bytes = codec::encode(&instr);
    assert_eq!(bytes, [0x90, 0x00]);
    let (decoded, consumed) = codec::decode(&bytes).unwrap();
    assert_eq!(decoded, instr);
    assert_eq!(consumed, 2);
}

#[test]
fn roundtrip_cnot() {
    let instr = GateInstruction::Cnot { ctrl: Qubit(0), tgt: Qubit(1) };
    let bytes = codec::encode(&instr);
    assert_eq!(bytes, [0xB0, 0x00, 0x01]);
    let (decoded, consumed) = codec::decode(&bytes).unwrap();
    assert_eq!(decoded, instr);
    assert_eq!(consumed, 3);
}

#[test]
fn roundtrip_rx() {
    use std::f64::consts::PI;
    let instr = GateInstruction::Rx { q: Qubit(2), theta: GateAngle::from_radians(PI / 2.0) };
    let bytes = codec::encode(&instr);
    assert_eq!(bytes.len(), 6); // 1 opcode + 1 qubit + 4 angle (u32, not f64)
    let (decoded, consumed) = codec::decode(&bytes).unwrap();
    assert_eq!(decoded, instr);
    assert_eq!(consumed, 6);
}

#[test]
fn decode_unknown_opcode() {
    let err = codec::decode(&[0xDE]).unwrap_err();
    assert!(matches!(err, xqgbc::codec::DecodeError::UnknownOpcode { byte: 0xDE }));
}
```

Run: `cargo test -p xqgbc` → expected: fails to compile (types not yet defined).

- [ ] **Step 2: Write `xqgbc/src/types/opcode.rs`**

Use the `pastey` crate (already in workspace) for paste-based identifier construction:

```rust
// Copyright (C) 2026 Postquant Labs Incorporated
// SPDX-License-Identifier: AGPL-3.0-or-later

/// Discriminant for a [`GateInstruction`] variant.
///
/// Derives from [`gate_opcodes!`]; each entry's `Variant` becomes a member.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum GateOpcode {
    QNop    = 0x80,
    QHalt   = 0x81,
    Barrier = 0x82,
    Meas    = 0x83,
    H       = 0x90,
    X       = 0x91,
    Y       = 0x92,
    Z       = 0x93,
    S       = 0x94,
    Sdg     = 0x95,
    T       = 0x96,
    Tdg     = 0x97,
    SX      = 0x98,
    Rx      = 0xA0,
    Ry      = 0xA1,
    Rz      = 0xA2,
    P       = 0xA3,
    U       = 0xA4,
    Cnot    = 0xB0,
    Cz      = 0xB1,
    Swap    = 0xB2,
    ISwap   = 0xB3,
    Cp      = 0xC0,
    Rzz     = 0xC1,
    Crx     = 0xC2,
}

impl TryFrom<u8> for GateOpcode {
    type Error = ();
    fn try_from(byte: u8) -> Result<Self, ()> {
        // Generated mechanically; prefer a match so the compiler enforces exhaustiveness.
        match byte {
            0x80 => Ok(Self::QNop),
            0x81 => Ok(Self::QHalt),
            0x82 => Ok(Self::Barrier),
            0x83 => Ok(Self::Meas),
            0x90 => Ok(Self::H),
            0x91 => Ok(Self::X),
            0x92 => Ok(Self::Y),
            0x93 => Ok(Self::Z),
            0x94 => Ok(Self::S),
            0x95 => Ok(Self::Sdg),
            0x96 => Ok(Self::T),
            0x97 => Ok(Self::Tdg),
            0x98 => Ok(Self::SX),
            0xA0 => Ok(Self::Rx),
            0xA1 => Ok(Self::Ry),
            0xA2 => Ok(Self::Rz),
            0xA3 => Ok(Self::P),
            0xA4 => Ok(Self::U),
            0xB0 => Ok(Self::Cnot),
            0xB1 => Ok(Self::Cz),
            0xB2 => Ok(Self::Swap),
            0xB3 => Ok(Self::ISwap),
            0xC0 => Ok(Self::Cp),
            0xC1 => Ok(Self::Rzz),
            0xC2 => Ok(Self::Crx),
            _    => Err(()),
        }
    }
}
```

Note: `GateOpcode` is written by hand rather than macro-generated to avoid needing `pastey` for a simple enum. The X-macro is the source of truth for `GateInstruction` and the codec.

- [ ] **Step 3: Write `xqgbc/src/types/instruction.rs`**

```rust
// Copyright (C) 2026 Postquant Labs Incorporated
// SPDX-License-Identifier: AGPL-3.0-or-later

#[cfg(not(feature = "std"))]
use alloc::vec::Vec;

use crate::{Cbit, GateAngle, Qubit};
use super::opcode::GateOpcode;

/// A fully decoded gate instruction.
///
/// Derived from [`gate_opcodes!`]; each entry's `(Variant, {fields})` becomes
/// an enum variant with named fields.
#[derive(Debug, Clone, PartialEq)]
pub enum GateInstruction {
    QNop,
    QHalt,
    Barrier,
    Meas    { q: Qubit, c: Cbit },
    H       { q: Qubit },
    X       { q: Qubit },
    Y       { q: Qubit },
    Z       { q: Qubit },
    S       { q: Qubit },
    Sdg     { q: Qubit },
    T       { q: Qubit },
    Tdg     { q: Qubit },
    SX      { q: Qubit },
    Rx      { q: Qubit, theta: GateAngle },
    Ry      { q: Qubit, theta: GateAngle },
    Rz      { q: Qubit, theta: GateAngle },
    P       { q: Qubit, lambda: GateAngle },
    U       { q: Qubit, theta: GateAngle, phi: GateAngle, lambda: GateAngle },
    Cnot    { ctrl: Qubit, tgt: Qubit },
    Cz      { ctrl: Qubit, tgt: Qubit },
    Swap    { q0: Qubit, q1: Qubit },
    ISwap   { q0: Qubit, q1: Qubit },
    Cp      { ctrl: Qubit, tgt: Qubit, lambda: GateAngle },
    Rzz     { q0: Qubit, q1: Qubit, theta: GateAngle },
    Crx     { ctrl: Qubit, tgt: Qubit, theta: GateAngle },
}

impl GateInstruction {
    /// The wire opcode byte for this instruction.
    pub fn opcode(&self) -> GateOpcode {
        match self {
            Self::QNop      => GateOpcode::QNop,
            Self::QHalt     => GateOpcode::QHalt,
            Self::Barrier   => GateOpcode::Barrier,
            Self::Meas { .. }   => GateOpcode::Meas,
            Self::H { .. }      => GateOpcode::H,
            Self::X { .. }      => GateOpcode::X,
            Self::Y { .. }      => GateOpcode::Y,
            Self::Z { .. }      => GateOpcode::Z,
            Self::S { .. }      => GateOpcode::S,
            Self::Sdg { .. }    => GateOpcode::Sdg,
            Self::T { .. }      => GateOpcode::T,
            Self::Tdg { .. }    => GateOpcode::Tdg,
            Self::SX { .. }     => GateOpcode::SX,
            Self::Rx { .. }     => GateOpcode::Rx,
            Self::Ry { .. }     => GateOpcode::Ry,
            Self::Rz { .. }     => GateOpcode::Rz,
            Self::P { .. }      => GateOpcode::P,
            Self::U { .. }      => GateOpcode::U,
            Self::Cnot { .. }   => GateOpcode::Cnot,
            Self::Cz { .. }     => GateOpcode::Cz,
            Self::Swap { .. }   => GateOpcode::Swap,
            Self::ISwap { .. }  => GateOpcode::ISwap,
            Self::Cp { .. }     => GateOpcode::Cp,
            Self::Rzz { .. }    => GateOpcode::Rzz,
            Self::Crx { .. }    => GateOpcode::Crx,
        }
    }

    /// The assembly mnemonic string for this instruction.
    pub fn mnemonic(&self) -> &'static str {
        match self.opcode() {
            GateOpcode::QNop    => "QNOP",
            GateOpcode::QHalt   => "QHALT",
            GateOpcode::Barrier => "BARRIER",
            GateOpcode::Meas    => "MEAS",
            GateOpcode::H       => "H",
            GateOpcode::X       => "X",
            GateOpcode::Y       => "Y",
            GateOpcode::Z       => "Z",
            GateOpcode::S       => "S",
            GateOpcode::Sdg     => "SDG",
            GateOpcode::T       => "T",
            GateOpcode::Tdg     => "TDG",
            GateOpcode::SX      => "SX",
            GateOpcode::Rx      => "RX",
            GateOpcode::Ry      => "RY",
            GateOpcode::Rz      => "RZ",
            GateOpcode::P       => "P",
            GateOpcode::U       => "U",
            GateOpcode::Cnot    => "CNOT",
            GateOpcode::Cz      => "CZ",
            GateOpcode::Swap    => "SWAP",
            GateOpcode::ISwap   => "ISWAP",
            GateOpcode::Cp      => "CP",
            GateOpcode::Rzz     => "RZZ",
            GateOpcode::Crx     => "CRX",
        }
    }
}
```

- [ ] **Step 4: Write `xqgbc/src/codec.rs`**

```rust
// Copyright (C) 2026 Postquant Labs Incorporated
// SPDX-License-Identifier: AGPL-3.0-or-later

//! Binary codec for XQG gate instructions.
//!
//! Wire layout: `[opcode: u8, operands...]` big-endian.
//!
//! | Field type  | Bytes |
//! |---|---|
//! | `Qubit`     | 1 (u8) |
//! | `Cbit`      | 1 (u8) |
//! | `GateAngle` | 4 (u32 big-endian, binary-angular units; full turn = 2^32) |

#[cfg(not(feature = "std"))]
use alloc::vec::Vec;

use thiserror::Error;

use crate::types::instruction::GateInstruction;
use crate::types::opcode::GateOpcode;
use crate::{Cbit, GateAngle, Qubit};

/// Errors returned by [`decode`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Error)]
pub enum DecodeError {
    #[error("instruction stream truncated: empty input")]
    EmptyInput,

    #[error("unknown gate opcode 0x{byte:02X}")]
    UnknownOpcode { byte: u8 },

    #[error("opcode 0x{opcode:02X} needs {needed} more byte(s), got {available}")]
    TruncatedOperand { opcode: u8, needed: usize, available: usize },
}

// ---------------------------------------------------------------------------
// EncodeOperand / DecodeOperand
// ---------------------------------------------------------------------------

trait EncodeOperand {
    fn encode_into(&self, buf: &mut Vec<u8>);
}

trait DecodeOperand: Sized {
    fn decode_from(bytes: &[u8], opcode: u8) -> Result<(Self, usize), DecodeError>;
}

impl EncodeOperand for Qubit {
    fn encode_into(&self, buf: &mut Vec<u8>) { buf.push(self.0); }
}
impl DecodeOperand for Qubit {
    fn decode_from(bytes: &[u8], opcode: u8) -> Result<(Self, usize), DecodeError> {
        bytes.first().copied().map(|v| (Qubit(v), 1)).ok_or(
            DecodeError::TruncatedOperand { opcode, needed: 1, available: 0 },
        )
    }
}

impl EncodeOperand for Cbit {
    fn encode_into(&self, buf: &mut Vec<u8>) { buf.push(self.0); }
}
impl DecodeOperand for Cbit {
    fn decode_from(bytes: &[u8], opcode: u8) -> Result<(Self, usize), DecodeError> {
        bytes.first().copied().map(|v| (Cbit(v), 1)).ok_or(
            DecodeError::TruncatedOperand { opcode, needed: 1, available: 0 },
        )
    }
}

impl EncodeOperand for GateAngle {
    fn encode_into(&self, buf: &mut Vec<u8>) { buf.extend_from_slice(&self.to_be_bytes()); }
}
impl DecodeOperand for GateAngle {
    fn decode_from(bytes: &[u8], opcode: u8) -> Result<(Self, usize), DecodeError> {
        if bytes.len() < 4 {
            return Err(DecodeError::TruncatedOperand {
                opcode,
                needed: 4 - bytes.len(),
                available: bytes.len(),
            });
        }
        let arr: [u8; 4] = bytes[..4].try_into().unwrap();
        Ok((GateAngle::from_be_bytes(arr), 4))
    }
}

// ---------------------------------------------------------------------------
// encode / decode
// ---------------------------------------------------------------------------

/// Encode a gate instruction to its wire representation.
pub fn encode(instr: &GateInstruction) -> Vec<u8> {
    let mut buf = Vec::with_capacity(10);
    buf.push(instr.opcode() as u8);
    match instr {
        GateInstruction::QNop | GateInstruction::QHalt | GateInstruction::Barrier => {}
        GateInstruction::Meas { q, c } => { q.encode_into(&mut buf); c.encode_into(&mut buf); }
        GateInstruction::H  { q } | GateInstruction::X  { q } | GateInstruction::Y  { q }
        | GateInstruction::Z  { q } | GateInstruction::S  { q } | GateInstruction::Sdg { q }
        | GateInstruction::T  { q } | GateInstruction::Tdg { q } | GateInstruction::SX  { q } => {
            q.encode_into(&mut buf);
        }
        GateInstruction::Rx { q, theta } | GateInstruction::Ry { q, theta }
        | GateInstruction::Rz { q, theta } => {
            q.encode_into(&mut buf); theta.encode_into(&mut buf);
        }
        GateInstruction::P { q, lambda } => { q.encode_into(&mut buf); lambda.encode_into(&mut buf); }
        GateInstruction::U { q, theta, phi, lambda } => {
            q.encode_into(&mut buf);
            theta.encode_into(&mut buf);
            phi.encode_into(&mut buf);
            lambda.encode_into(&mut buf);
        }
        GateInstruction::Cnot { ctrl, tgt } | GateInstruction::Cz { ctrl, tgt } => {
            ctrl.encode_into(&mut buf); tgt.encode_into(&mut buf);
        }
        GateInstruction::Swap { q0, q1 } | GateInstruction::ISwap { q0, q1 } => {
            q0.encode_into(&mut buf); q1.encode_into(&mut buf);
        }
        GateInstruction::Cp { ctrl, tgt, lambda } => {
            ctrl.encode_into(&mut buf); tgt.encode_into(&mut buf); lambda.encode_into(&mut buf);
        }
        GateInstruction::Rzz { q0, q1, theta } => {
            q0.encode_into(&mut buf); q1.encode_into(&mut buf); theta.encode_into(&mut buf);
        }
        GateInstruction::Crx { ctrl, tgt, theta } => {
            ctrl.encode_into(&mut buf); tgt.encode_into(&mut buf); theta.encode_into(&mut buf);
        }
    }
    buf
}

/// Decode one gate instruction from the start of `bytes`.
///
/// Returns `(instruction, bytes_consumed)`.
pub fn decode(bytes: &[u8]) -> Result<(GateInstruction, usize), DecodeError> {
    let (&opcode_byte, rest) = bytes.split_first().ok_or(DecodeError::EmptyInput)?;
    let opcode = GateOpcode::try_from(opcode_byte)
        .map_err(|_| DecodeError::UnknownOpcode { byte: opcode_byte })?;

    let mut pos = 0usize;
    macro_rules! read {
        ($T:ty) => {{
            let (val, n) = <$T>::decode_from(&rest[pos..], opcode_byte)?;
            pos += n;
            val
        }};
    }

    let instr = match opcode {
        GateOpcode::QNop    => GateInstruction::QNop,
        GateOpcode::QHalt   => GateInstruction::QHalt,
        GateOpcode::Barrier => GateInstruction::Barrier,
        GateOpcode::Meas    => GateInstruction::Meas    { q: read!(Qubit), c: read!(Cbit) },
        GateOpcode::H       => GateInstruction::H       { q: read!(Qubit) },
        GateOpcode::X       => GateInstruction::X       { q: read!(Qubit) },
        GateOpcode::Y       => GateInstruction::Y       { q: read!(Qubit) },
        GateOpcode::Z       => GateInstruction::Z       { q: read!(Qubit) },
        GateOpcode::S       => GateInstruction::S       { q: read!(Qubit) },
        GateOpcode::Sdg     => GateInstruction::Sdg     { q: read!(Qubit) },
        GateOpcode::T       => GateInstruction::T       { q: read!(Qubit) },
        GateOpcode::Tdg     => GateInstruction::Tdg     { q: read!(Qubit) },
        GateOpcode::SX      => GateInstruction::SX      { q: read!(Qubit) },
        GateOpcode::Rx      => GateInstruction::Rx      { q: read!(Qubit), theta:  read!(GateAngle) },
        GateOpcode::Ry      => GateInstruction::Ry      { q: read!(Qubit), theta:  read!(GateAngle) },
        GateOpcode::Rz      => GateInstruction::Rz      { q: read!(Qubit), theta:  read!(GateAngle) },
        GateOpcode::P       => GateInstruction::P       { q: read!(Qubit), lambda: read!(GateAngle) },
        GateOpcode::U       => GateInstruction::U       { q: read!(Qubit), theta: read!(GateAngle), phi: read!(GateAngle), lambda: read!(GateAngle) },
        GateOpcode::Cnot    => GateInstruction::Cnot    { ctrl: read!(Qubit), tgt: read!(Qubit) },
        GateOpcode::Cz      => GateInstruction::Cz      { ctrl: read!(Qubit), tgt: read!(Qubit) },
        GateOpcode::Swap    => GateInstruction::Swap    { q0: read!(Qubit), q1: read!(Qubit) },
        GateOpcode::ISwap   => GateInstruction::ISwap   { q0: read!(Qubit), q1: read!(Qubit) },
        GateOpcode::Cp      => GateInstruction::Cp      { ctrl: read!(Qubit), tgt: read!(Qubit), lambda: read!(GateAngle) },
        GateOpcode::Rzz     => GateInstruction::Rzz     { q0: read!(Qubit), q1: read!(Qubit), theta: read!(GateAngle) },
        GateOpcode::Crx     => GateInstruction::Crx     { ctrl: read!(Qubit), tgt: read!(Qubit), theta: read!(GateAngle) },
    };
    Ok((instr, 1 + pos))
}
```

- [ ] **Step 5: Update `xqgbc/src/types/mod.rs` and `lib.rs`**

`types/mod.rs`:
```rust
pub mod operand;
pub mod opcode;
pub mod instruction;
pub use operand::{Cbit, GateAngle, Qubit};
pub use opcode::GateOpcode;
pub use instruction::GateInstruction;
```

`lib.rs`:
```rust
#![cfg_attr(not(feature = "std"), no_std)]
#[cfg(not(feature = "std"))]
extern crate alloc;

#[macro_use]
mod table;

mod types;
pub use types::{Cbit, GateAngle, GateInstruction, GateOpcode, Qubit};
pub use gate_opcodes;

pub mod codec;
```

- [ ] **Step 6: Run the roundtrip tests**

```bash
cargo test -p xqgbc
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add xqgbc/
git commit -m "feat(xqgbc): gate instruction enum, opcode enum, and binary codec"
```

---

## W1 · Part 3 — `xqgbc`: `.xqg` Program Format and Static Verifier

**Files:**
- Create: `xqgbc/src/program.rs`
- Create: `xqgbc/src/verifier.rs`
- Modify: `xqgbc/src/lib.rs`

**Interfaces:**
- Consumes: `GateInstruction`, `codec::encode`/`decode` from W1 Part 2
- Produces: `GateCircuit::encode() -> Vec<u8>`, `GateCircuit::decode(&[u8]) -> Result<GateCircuit, _>`, `static_verify(&GateCircuit) -> Result<(), GateVerifyError>`

### `.xqg` Binary Header (15 bytes)

The gate header is structurally identical to the QUBO (`.xqb`) header — same size, same
field positions, same CRC scope. The format is distinguished by the magic bytes.

**XQ bytecode family discriminant:** Both formats share the two-byte prefix `XQ`. Bytes
2–3 identify the specific format: `BC` → QUBO circuit; `GB` → gate circuit. A
generic XQ reader can dispatch on bytes `[2:4]` without parsing the rest of the header.

```
Offset  Width  Field             (.xqb / QUBO)        (.xqg / Gate)
------  -----  -----             ------------         -------------
 0..4     4    Magic             b"XQBC"              b"XQGB"
    4     1    version: u8       = 1                  = 1
    5     1    domain_a: u8      input_slots           n_qubits
    6     1    domain_b: u8      output_slots          n_cbits
 7..11    4    domain_c: u32 BE  code_len              n_shots
11..15    4    crc32: u32 BE     CRC-32 over stream   CRC-32 over stream
15+       *    instruction stream
```

`domain_a`/`domain_b`/`domain_c` are format-specific interpretations of the same
structural slots. CRC is stream-only in both formats: header fields are independently
validated by the verifier (qubit/cbit bounds checks; length match), so a corrupt header
byte that disagrees with the stream fails structurally rather than silently.

- [ ] **Step 1: Write failing tests** in `xqgbc/tests/roundtrip.rs` (append):

```rust
use xqgbc::program::GateCircuit;
use xqgbc::verifier::static_verify;

#[test]
fn encode_decode_bell_circuit() {
    use xqgbc::GateInstruction::{H, Cnot, Meas, QHalt};
    let instructions = vec![
        H { q: Qubit(0) },
        Cnot { ctrl: Qubit(0), tgt: Qubit(1) },
        Meas { q: Qubit(0), c: Cbit(0) },
        Meas { q: Qubit(1), c: Cbit(1) },
        QHalt,
    ];
    let circuit = GateCircuit::new(2, 2, 1024, instructions);
    let bytes = circuit.encode();
    let decoded = GateCircuit::decode(&bytes).unwrap();
    assert_eq!(decoded.n_qubits(), 2);
    assert_eq!(decoded.n_cbits(), 2);
    assert_eq!(decoded.n_shots(), 1024);
    assert_eq!(decoded.instructions(), circuit.instructions());
    static_verify(&decoded).unwrap();
}

#[test]
fn verify_rejects_missing_meas() {
    use xqgbc::GateInstruction::{H, QHalt};
    let circuit = GateCircuit::new(1, 0, 100, vec![H { q: Qubit(0) }, QHalt]);
    assert!(static_verify(&circuit).is_err());
}

#[test]
fn verify_rejects_qubit_out_of_bounds() {
    use xqgbc::GateInstruction::{Meas, QHalt};
    // Circuit declares 1 qubit but instruction references qubit 5
    let circuit = GateCircuit::new(1, 1, 100, vec![
        Meas { q: Qubit(5), c: Cbit(0) },
        QHalt,
    ]);
    assert!(static_verify(&circuit).is_err());
}
```

Run: `cargo test -p xqgbc` → expected: compile error (module not found).

- [ ] **Step 2: Write `xqgbc/src/program.rs`**

```rust
// Copyright (C) 2026 Postquant Labs Incorporated
// SPDX-License-Identifier: AGPL-3.0-or-later

//! `.xqg` binary format: 15-byte header + gate instruction stream.

#[cfg(not(feature = "std"))]
use alloc::{vec, vec::Vec};

use thiserror::Error;

use crate::codec::{self, DecodeError};
use crate::types::instruction::GateInstruction;

const MAGIC: &[u8; 4] = b"XQGB";
const VERSION: u8 = 1;
const HEADER_LEN: usize = 15;

/// A decoded `.xqg` gate circuit.
#[derive(Debug, Clone, PartialEq)]
pub struct GateCircuit {
    n_qubits: u8,
    n_cbits: u8,
    n_shots: u32,
    instructions: Vec<GateInstruction>,
}

impl GateCircuit {
    /// Construct a new circuit from decoded instructions.
    pub fn new(
        n_qubits: u8,
        n_cbits: u8,
        n_shots: u32,
        instructions: Vec<GateInstruction>,
    ) -> Self {
        Self { n_qubits, n_cbits, n_shots, instructions }
    }

    pub fn n_qubits(&self) -> u8 { self.n_qubits }
    pub fn n_cbits(&self) -> u8 { self.n_cbits }
    pub fn n_shots(&self) -> u32 { self.n_shots }
    pub fn instructions(&self) -> &[GateInstruction] { &self.instructions }

    /// Encode to `.xqg` wire format.
    pub fn encode(&self) -> Vec<u8> {
        let mut stream: Vec<u8> = Vec::new();
        for instr in &self.instructions {
            stream.extend_from_slice(&codec::encode(instr));
        }
        let crc = crc32fast::hash(&stream);
        let mut out = Vec::with_capacity(HEADER_LEN + stream.len());
        out.extend_from_slice(MAGIC);
        out.push(VERSION);
        out.push(self.n_qubits);
        out.push(self.n_cbits);
        out.extend_from_slice(&self.n_shots.to_be_bytes());
        out.extend_from_slice(&crc.to_be_bytes());
        out.extend_from_slice(&stream);
        out
    }

    /// Decode a `.xqg` file from raw bytes.
    pub fn decode(bytes: &[u8]) -> Result<Self, GateCircuitDecodeError> {
        if bytes.len() < HEADER_LEN {
            return Err(GateCircuitDecodeError::TruncatedHeader {
                got: bytes.len(),
            });
        }
        if &bytes[0..4] != MAGIC {
            return Err(GateCircuitDecodeError::BadMagic);
        }
        if bytes[4] != VERSION {
            return Err(GateCircuitDecodeError::UnsupportedVersion { version: bytes[4] });
        }
        let n_qubits = bytes[5];
        let n_cbits  = bytes[6];
        let n_shots  = u32::from_be_bytes(bytes[7..11].try_into().unwrap());
        let stored_crc = u32::from_be_bytes(bytes[11..15].try_into().unwrap());

        let stream = &bytes[HEADER_LEN..];
        let actual_crc = crc32fast::hash(stream);
        if actual_crc != stored_crc {
            return Err(GateCircuitDecodeError::CrcMismatch {
                stored: stored_crc,
                computed: actual_crc,
            });
        }

        let mut instructions = Vec::new();
        let mut pos = 0;
        while pos < stream.len() {
            let (instr, consumed) = codec::decode(&stream[pos..])
                .map_err(GateCircuitDecodeError::Codec)?;
            pos += consumed;
            instructions.push(instr);
        }

        Ok(Self { n_qubits, n_cbits, n_shots, instructions })
    }
}

/// Errors returned by [`GateCircuit::decode`].
#[derive(Debug, Error)]
pub enum GateCircuitDecodeError {
    #[error("file too short to contain a valid header: got {got} bytes")]
    TruncatedHeader { got: usize },
    #[error("not a .xqg file (bad magic bytes)")]
    BadMagic,
    #[error("unsupported .xqg version {version} (this build supports version 1)")]
    UnsupportedVersion { version: u8 },
    #[error("CRC-32 mismatch: stored 0x{stored:08X}, computed 0x{computed:08X}")]
    CrcMismatch { stored: u32, computed: u32 },
    #[error("instruction stream decode error: {0}")]
    Codec(#[from] DecodeError),
}
```

- [ ] **Step 3: Write `xqgbc/src/verifier.rs`**

```rust
// Copyright (C) 2026 Postquant Labs Incorporated
// SPDX-License-Identifier: AGPL-3.0-or-later

use thiserror::Error;

use crate::program::GateCircuit;
use crate::types::instruction::GateInstruction;

/// Errors from [`static_verify`].
#[derive(Debug, Error)]
pub enum GateVerifyError {
    #[error("qubit index {index} is out of bounds (circuit declares {n_qubits} qubits)")]
    QubitOutOfBounds { index: u8, n_qubits: u8 },
    #[error("classical bit index {index} is out of bounds (circuit declares {n_cbits} cbits)")]
    CbitOutOfBounds { index: u8, n_cbits: u8 },
    #[error("circuit has no MEAS instruction; every circuit must measure at least one qubit")]
    NoMeasurement,
    #[error("QHALT is not the last instruction in the circuit")]
    HaltNotLast,
}

/// Statically verify a decoded gate circuit.
///
/// Checks:
/// 1. All qubit indices are in `[0, n_qubits)`.
/// 2. All cbit indices are in `[0, n_cbits)`.
/// 3. At least one `MEAS` instruction is present.
/// 4. `QHALT` is the last instruction.
pub fn static_verify(circuit: &GateCircuit) -> Result<(), GateVerifyError> {
    let n_qubits = circuit.n_qubits();
    let n_cbits  = circuit.n_cbits();
    let instrs   = circuit.instructions();

    // Check QHALT is last.
    match instrs.last() {
        Some(GateInstruction::QHalt) => {}
        _ => return Err(GateVerifyError::HaltNotLast),
    }

    let mut has_meas = false;

    for instr in instrs {
        check_qubits(instr, n_qubits)?;
        check_cbits(instr, n_cbits)?;
        if matches!(instr, GateInstruction::Meas { .. }) {
            has_meas = true;
        }
    }

    if !has_meas {
        return Err(GateVerifyError::NoMeasurement);
    }

    Ok(())
}

fn check_qubits(instr: &GateInstruction, n: u8) -> Result<(), GateVerifyError> {
    let qubits: &[u8] = match instr {
        GateInstruction::Meas { q, .. } => &[q.0],
        GateInstruction::H { q } | GateInstruction::X { q } | GateInstruction::Y { q }
        | GateInstruction::Z { q } | GateInstruction::S { q } | GateInstruction::Sdg { q }
        | GateInstruction::T { q } | GateInstruction::Tdg { q } | GateInstruction::SX { q }
        | GateInstruction::Rx { q, .. } | GateInstruction::Ry { q, .. }
        | GateInstruction::Rz { q, .. } | GateInstruction::P { q, .. }
        | GateInstruction::U { q, .. } => &[q.0],
        GateInstruction::Cnot { ctrl, tgt } | GateInstruction::Cz { ctrl, tgt }
        | GateInstruction::Cp { ctrl, tgt, .. } | GateInstruction::Crx { ctrl, tgt, .. } => {
            &[ctrl.0, tgt.0]
        }
        GateInstruction::Swap { q0, q1 } | GateInstruction::ISwap { q0, q1 }
        | GateInstruction::Rzz { q0, q1, .. } => &[q0.0, q1.0],
        _ => &[],
    };
    for &idx in qubits {
        if idx >= n {
            return Err(GateVerifyError::QubitOutOfBounds { index: idx, n_qubits: n });
        }
    }
    Ok(())
}

fn check_cbits(instr: &GateInstruction, n: u8) -> Result<(), GateVerifyError> {
    if let GateInstruction::Meas { c, .. } = instr {
        if c.0 >= n {
            return Err(GateVerifyError::CbitOutOfBounds { index: c.0, n_cbits: n });
        }
    }
    Ok(())
}
```

- [ ] **Step 4: Update `xqgbc/src/lib.rs`**

```rust
#![cfg_attr(not(feature = "std"), no_std)]
#[cfg(not(feature = "std"))]
extern crate alloc;

#[macro_use]
mod table;

mod types;
pub use types::{Cbit, GateAngle, GateInstruction, GateOpcode, Qubit};
pub use gate_opcodes;

pub mod codec;
pub mod program;
pub mod verifier;
pub use program::{GateCircuit, GateCircuitDecodeError};
pub use verifier::{static_verify, GateVerifyError};
```

- [ ] **Step 5: Run all tests**

```bash
cargo test -p xqgbc
```

Expected: all pass including the 3 new program/verifier tests.

- [ ] **Step 6: Commit**

```bash
git add xqgbc/
git commit -m "feat(xqgbc): .xqg binary program format and static verifier"
```

---

## W2 · Part 1 — `xqgb` Python Package: Decoder and `GateSolver` Base

**Files:**
- Create: `xqgb/pyproject.toml`
- Create: `xqgb/__init__.py`
- Create: `xqgb/decoder.py`
- Create: `xqgb/solver.py`
- Create: `xqgb/tests/__init__.py`
- Create: `xqgb/tests/test_decoder.py`
- Modify: root `pyproject.toml` — add `xqgb` to `[tool.uv.workspace] members`

**Interfaces:**
- Produces: `GateCircuit` (Python dataclass), `decode_xqg(data: bytes) -> GateCircuit`, `GateSolverResult`, `GateSolver` ABC

- [ ] **Step 1: Write `xqgb/tests/test_decoder.py`** (failing tests first)

```python
"""Tests for the .xqg binary decoder."""
import struct
import zlib

import pytest

from xqgb.decoder import GateCircuit, decode_xqg

_MAGIC = b"XQGB"
_VERSION = 1


def _build_xqg(n_qubits: int, n_cbits: int, n_shots: int, stream: bytes) -> bytes:
    """Build a minimal valid .xqg file for testing."""
    crc = zlib.crc32(stream) & 0xFFFFFFFF
    header = _MAGIC + bytes([_VERSION, n_qubits, n_cbits]) + struct.pack(">I", n_shots) + struct.pack(">I", crc)
    assert len(header) == 15
    return header + stream


def test_decode_empty_halt_circuit():
    # Stream: QHALT (0x81), MEAS (0x83 q=0 c=0) — but MEAS must come before QHALT
    stream = bytes([0x83, 0x00, 0x00, 0x81])   # MEAS q=0 c=0, QHALT
    data = _build_xqg(1, 1, 100, stream)
    circuit = decode_xqg(data)
    assert circuit.header.n_qubits == 1
    assert circuit.header.n_cbits == 1
    assert circuit.header.n_shots == 100
    assert len(circuit.instructions) == 2
    assert circuit.instructions[0].mnemonic == "MEAS"
    assert circuit.instructions[1].mnemonic == "QHALT"


def test_decode_bad_magic():
    data = b"BADS" + bytes(11)
    with pytest.raises(ValueError, match="magic"):
        decode_xqg(data)


def test_decode_bad_crc():
    stream = bytes([0x83, 0x00, 0x00, 0x81])
    data = _build_xqg(1, 1, 100, stream)
    # Corrupt a stream byte
    corrupted = data[:15] + bytes([data[15] ^ 0xFF]) + data[16:]
    with pytest.raises(ValueError, match="CRC"):
        decode_xqg(corrupted)


def test_decode_h_gate():
    stream = bytes([0x90, 0x00,       # H q=0
                    0x83, 0x00, 0x00, # MEAS q=0 c=0
                    0x81])            # QHALT
    data = _build_xqg(1, 1, 1000, stream)
    circuit = decode_xqg(data)
    h_instr = circuit.instructions[0]
    assert h_instr.mnemonic == "H"
    assert h_instr.operands == (0,)   # qubit=0
```

Run: `cd /home/konrad/Quip/xquad && uv run pytest xqgb/tests/test_decoder.py` → expected: import error.

- [ ] **Step 2: Write `xqgb/pyproject.toml`**

```toml
[build-system]
requires      = ["hatchling"]
build-backend = "hatchling.build"

[project]
name            = "xqgb"
version         = "0.1.0"
description     = "Gate-circuit backends for the XQuad toolchain (CPU sim, IBM Quantum, IonQ)."
requires-python = ">=3.13"
license         = { text = "AGPL-3.0-or-later" }
dependencies    = ["numpy>=2.0"]

[project.optional-dependencies]
ibm  = ["qiskit>=1.0", "qiskit-aer>=0.14", "qiskit-ibm-runtime>=0.20"]
ionq = ["ionq>=0.3"]

[tool.hatch.build.targets.wheel]
packages = ["."]
exclude  = ["tests/**", "pyproject.toml"]
```

- [ ] **Step 3: Add `xqgb` to root `pyproject.toml` workspace**

Locate `[tool.uv.workspace]` in root `pyproject.toml` and add `"xqgb"`:

```toml
[tool.uv.workspace]
members = ["xqffi", "xqvm_py", "xqcp", "xqsa", "xquad", "xqgb"]
```

Also add `"xqgb/tests"` to `[tool.pytest.ini_options] testpaths` if that list exists.

- [ ] **Step 4: Write `xqgb/decoder.py`**

```python
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Pure-Python decoder for the .xqg gate bytecode format."""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

_MAGIC = b"XQGB"
_VERSION = 1
_HEADER_SIZE = 15

# Opcode table: byte -> (mnemonic, operand_format_string)
# Format chars:
#   'q' = u8 qubit index
#   'c' = u8 classical bit index
#   'a' = u32 angle in binary-angular units (4 bytes BE; full turn = 2^32)
#         Decoded as a plain int; callers convert to radians with: val * 2π / 2^32
_OPCODE_TABLE: dict[int, tuple[str, str]] = {
    0x80: ("QNOP",    ""),
    0x81: ("QHALT",   ""),
    0x82: ("BARRIER", ""),
    0x83: ("MEAS",    "qc"),
    0x90: ("H",       "q"),
    0x91: ("X",       "q"),
    0x92: ("Y",       "q"),
    0x93: ("Z",       "q"),
    0x94: ("S",       "q"),
    0x95: ("SDG",     "q"),
    0x96: ("T",       "q"),
    0x97: ("TDG",     "q"),
    0x98: ("SX",      "q"),
    0xA0: ("RX",      "qa"),
    0xA1: ("RY",      "qa"),
    0xA2: ("RZ",      "qa"),
    0xA3: ("P",       "qa"),
    0xA4: ("U",       "qaaa"),
    0xB0: ("CNOT",    "qq"),
    0xB1: ("CZ",      "qq"),
    0xB2: ("SWAP",    "qq"),
    0xB3: ("ISWAP",   "qq"),
    0xC0: ("CP",      "qqa"),
    0xC1: ("RZZ",     "qqa"),
    0xC2: ("CRX",     "qqa"),
}


@dataclass(frozen=True)
class XqgHeader:
    """Decoded .xqg file header."""

    n_qubits: int
    n_cbits: int
    n_shots: int


@dataclass(frozen=True)
class RawGateInstruction:
    """A single decoded gate instruction."""

    mnemonic: str
    operands: tuple  # ints for qubits/cbits and u32 angle values (binary-angular units)


@dataclass(frozen=True)
class GateCircuit:
    """A decoded .xqg gate circuit ready for backend execution."""

    header: XqgHeader
    instructions: list[RawGateInstruction]


def decode_xqg(data: bytes) -> GateCircuit:
    """Decode .xqg binary data into a GateCircuit.

    Args:
        data: Raw bytes of a .xqg file.

    Returns:
        Decoded GateCircuit.

    Raises:
        ValueError: If magic bytes, version, header length, or CRC-32 are invalid.
        ValueError: If the instruction stream contains an unknown opcode.
    """
    if len(data) < _HEADER_SIZE:
        raise ValueError(f"File too short for .xqg header: got {len(data)} bytes")
    if data[:4] != _MAGIC:
        raise ValueError(f"Invalid .xqg magic bytes: expected {_MAGIC!r}, got {data[:4]!r}")
    if data[4] != _VERSION:
        raise ValueError(f"Unsupported .xqg version {data[4]} (this decoder supports version 1)")

    n_qubits = data[5]
    n_cbits  = data[6]
    (n_shots,) = struct.unpack_from(">I", data, 7)
    (stored_crc,) = struct.unpack_from(">I", data, 11)

    stream = data[_HEADER_SIZE:]
    actual_crc = zlib.crc32(stream) & 0xFFFFFFFF
    if actual_crc != stored_crc:
        raise ValueError(
            f"CRC-32 mismatch: stored 0x{stored_crc:08X}, computed 0x{actual_crc:08X}"
        )

    instructions = _decode_stream(stream)
    header = XqgHeader(n_qubits=n_qubits, n_cbits=n_cbits, n_shots=n_shots)
    return GateCircuit(header=header, instructions=instructions)


def _decode_stream(stream: bytes) -> list[RawGateInstruction]:
    """Decode a sequence of gate instructions from the stream bytes."""
    instructions: list[RawGateInstruction] = []
    pos = 0
    while pos < len(stream):
        opcode_byte = stream[pos]
        pos += 1
        if opcode_byte not in _OPCODE_TABLE:
            raise ValueError(f"Unknown gate opcode 0x{opcode_byte:02X} at stream offset {pos - 1}")
        mnemonic, fmt = _OPCODE_TABLE[opcode_byte]
        operands, consumed = _decode_operands(stream, pos, fmt, opcode_byte)
        pos += consumed
        instructions.append(RawGateInstruction(mnemonic=mnemonic, operands=tuple(operands)))
    return instructions


def _decode_operands(
    stream: bytes, pos: int, fmt: str, opcode: int
) -> tuple[list, int]:
    """Decode operands described by format string starting at pos."""
    values: list = []
    consumed = 0
    for char in fmt:
        if char == "q" or char == "c":
            if pos + consumed >= len(stream):
                raise ValueError(
                    f"Truncated operand for opcode 0x{opcode:02X}: expected u8 at offset {pos + consumed}"
                )
            values.append(stream[pos + consumed])
            consumed += 1
        elif char == "a":
            end = pos + consumed + 4
            if end > len(stream):
                raise ValueError(
                    f"Truncated angle operand for opcode 0x{opcode:02X} at offset {pos + consumed}"
                )
            (angle_u32,) = struct.unpack_from(">I", stream, pos + consumed)
            values.append(angle_u32)  # raw u32; callers convert: val * 2π / 2^32
            consumed += 4
    return values, consumed
```

- [ ] **Step 5: Write `xqgb/solver.py`**

```python
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Abstract GateSolver base class and result type."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from xqgb.decoder import GateCircuit


@dataclass(frozen=True)
class GateSolverResult:
    """Result from executing a gate circuit.

    Attributes:
        counts: Measurement histogram mapping zero-padded binary bitstring
            to integer shot count. E.g. ``{"00": 512, "11": 512}``.
        total_shots: Sum of all count values; equals the requested shot count.
        timing: Wall-clock seconds elapsed during backend execution.
        metadata: Backend-specific extra fields (e.g. job ID, backend name).
    """

    counts: dict[str, int]
    total_shots: int
    timing: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def probabilities(self) -> dict[str, float]:
        """Return normalized shot probabilities for each bitstring.

        Returns:
            Dict mapping bitstring to probability in [0, 1].
        """
        if self.total_shots == 0:
            return {}
        return {k: v / self.total_shots for k, v in self.counts.items()}


class GateSolver(ABC):
    """Abstract base class for quantum gate-circuit execution backends."""

    @abstractmethod
    def run(self, circuit: GateCircuit, **kwargs: Any) -> GateSolverResult:
        """Execute a gate circuit and return measurement statistics.

        Args:
            circuit: A decoded .xqg GateCircuit.
            **kwargs: Backend-specific parameters (e.g. ``n_shots`` override).

        Returns:
            GateSolverResult with measurement histogram and timing.
        """
        ...

    def _validate_circuit(self, circuit: GateCircuit) -> None:
        """Check that the circuit has at least one MEAS instruction.

        Args:
            circuit: Circuit to validate.

        Raises:
            ValueError: If no MEAS instruction is present.
        """
        has_meas = any(i.mnemonic == "MEAS" for i in circuit.instructions)
        if not has_meas:
            raise ValueError("GateCircuit has no MEAS instruction")
```

- [ ] **Step 6: Write `xqgb/__init__.py`**

```python
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later

"""XQuad gate-circuit backend package."""

from xqgb.decoder import GateCircuit, XqgHeader, RawGateInstruction, decode_xqg
from xqgb.solver import GateSolver, GateSolverResult

__all__ = [
    "GateCircuit",
    "XqgHeader",
    "RawGateInstruction",
    "decode_xqg",
    "GateSolver",
    "GateSolverResult",
]
```

- [ ] **Step 7: Run decoder tests**

```bash
cd /home/konrad/Quip/xquad && uv run pytest xqgb/tests/test_decoder.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 8: Commit**

```bash
git add xqgb/ pyproject.toml
git commit -m "feat(xqgb): Python package scaffold with .xqg decoder and GateSolver ABC"
```

---

## W2 · Part 2 — `xqgb`: NumPy Statevector CPU Simulator

**Files:**
- Create: `xqgb/cpu_sim.py`
- Create: `xqgb/tests/test_cpu_sim.py`
- Modify: `xqgb/__init__.py`

**Interfaces:**
- Consumes: `GateCircuit`, `GateSolver`, `GateSolverResult` from W2 Part 1
- Produces: `SolverCPUSim(GateSolver)` with `run(circuit) -> GateSolverResult`

- [ ] **Step 1: Write failing tests** in `xqgb/tests/test_cpu_sim.py`

```python
"""CPU statevector simulator tests — no hardware required."""

import math
import struct
import zlib

import numpy as np
import pytest

from xqgb.cpu_sim import SolverCPUSim, StatevectorSimulator
from xqgb.decoder import GateCircuit, XqgHeader, RawGateInstruction


_TAU = 2 * math.pi


def _angle(radians: float) -> int:
    """Convert radians to u32 binary-angular units (full turn = 2^32)."""
    return round(radians % _TAU / _TAU * 4_294_967_296) & 0xFFFFFFFF


def _make_circuit(n_qubits: int, n_cbits: int, n_shots: int, instrs: list) -> GateCircuit:
    return GateCircuit(
        header=XqgHeader(n_qubits=n_qubits, n_cbits=n_cbits, n_shots=n_shots),
        instructions=instrs,
    )


def _I(mnemonic, *operands):
    return RawGateInstruction(mnemonic=mnemonic, operands=operands)


def test_x_gate_flips_qubit():
    """X on |0> should give |1> with probability 1."""
    circuit = _make_circuit(1, 1, 1000, [
        _I("X", 0),
        _I("MEAS", 0, 0),
        _I("QHALT"),
    ])
    sim = SolverCPUSim(seed=42)
    result = sim.run(circuit)
    assert result.counts == {"1": 1000}


def test_h_gate_gives_uniform():
    """H on |0> should give ~50% |0> and ~50% |1>."""
    circuit = _make_circuit(1, 1, 4000, [
        _I("H", 0),
        _I("MEAS", 0, 0),
        _I("QHALT"),
    ])
    sim = SolverCPUSim(seed=0)
    result = sim.run(circuit)
    prob_0 = result.counts.get("0", 0) / result.total_shots
    assert 0.45 <= prob_0 <= 0.55, f"expected ~0.50, got {prob_0:.3f}"


def test_bell_state_entanglement():
    """H+CNOT on |00> should give Bell state: only |00> and |11> outcomes."""
    circuit = _make_circuit(2, 2, 2000, [
        _I("H", 0),
        _I("CNOT", 0, 1),
        _I("MEAS", 0, 0),
        _I("MEAS", 1, 1),
        _I("QHALT"),
    ])
    sim = SolverCPUSim(seed=7)
    result = sim.run(circuit)
    assert set(result.counts.keys()) <= {"00", "11"}
    prob_00 = result.counts.get("00", 0) / result.total_shots
    assert 0.45 <= prob_00 <= 0.55


def test_rx_pi_equals_x():
    """RX(π) should be equivalent to Pauli-X (flips qubit)."""
    circuit = _make_circuit(1, 1, 1000, [
        _I("RX", 0, _angle(math.pi)),  # π as u32 binary-angular ≈ 2_147_483_648
        _I("MEAS", 0, 0),
        _I("QHALT"),
    ])
    sim = SolverCPUSim(seed=42)
    result = sim.run(circuit)
    # RX(π)|0⟩ = -i|1⟩, probability still 1
    assert result.counts.get("1", 0) == 1000


def test_total_shots_matches_request():
    circuit = _make_circuit(1, 1, 500, [
        _I("H", 0),
        _I("MEAS", 0, 0),
        _I("QHALT"),
    ])
    result = SolverCPUSim(seed=1).run(circuit)
    assert result.total_shots == 500
    assert sum(result.counts.values()) == 500
```

Run: `uv run pytest xqgb/tests/test_cpu_sim.py` → expected: import error.

- [ ] **Step 2: Write `xqgb/cpu_sim.py`**

```python
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later

"""NumPy statevector CPU simulator for .xqg gate circuits."""

from __future__ import annotations

import math
import time
from typing import Any

import numpy as np
from numpy.typing import NDArray

from xqgb.decoder import GateCircuit, RawGateInstruction
from xqgb.solver import GateSolver, GateSolverResult

# ---------------------------------------------------------------------------
# Gate unitary matrices (2x2 and 4x4, dtype=complex128)
# ---------------------------------------------------------------------------

_INV_SQRT2 = 1.0 / math.sqrt(2)
_I = complex(0, 1)
_TAU = 2 * math.pi
_ANGLE_SCALE = _TAU / 4_294_967_296  # radians per u32 binary-angular unit


def _u32_to_rad(a: int) -> float:
    """Convert a u32 binary-angular angle to radians."""
    return a * _ANGLE_SCALE

_FIXED_GATES: dict[str, NDArray[np.complexfloating]] = {
    "H":   np.array([[_INV_SQRT2, _INV_SQRT2], [_INV_SQRT2, -_INV_SQRT2]], dtype=np.complex128),
    "X":   np.array([[0, 1], [1, 0]], dtype=np.complex128),
    "Y":   np.array([[0, -_I], [_I, 0]], dtype=np.complex128),
    "Z":   np.array([[1, 0], [0, -1]], dtype=np.complex128),
    "S":   np.array([[1, 0], [0, _I]], dtype=np.complex128),
    "SDG": np.array([[1, 0], [0, -_I]], dtype=np.complex128),
    "T":   np.array([[1, 0], [0, np.exp(_I * math.pi / 4)]], dtype=np.complex128),
    "TDG": np.array([[1, 0], [0, np.exp(-_I * math.pi / 4)]], dtype=np.complex128),
    "SX":  np.array([[1 + _I, 1 - _I], [1 - _I, 1 + _I]], dtype=np.complex128) * 0.5,
}


def _rx(theta: float) -> NDArray[np.complexfloating]:
    c, s = math.cos(theta / 2), math.sin(theta / 2)
    return np.array([[c, -_I * s], [-_I * s, c]], dtype=np.complex128)


def _ry(theta: float) -> NDArray[np.complexfloating]:
    c, s = math.cos(theta / 2), math.sin(theta / 2)
    return np.array([[c, -s], [s, c]], dtype=np.complex128)


def _rz(theta: float) -> NDArray[np.complexfloating]:
    return np.array(
        [[np.exp(-_I * theta / 2), 0], [0, np.exp(_I * theta / 2)]], dtype=np.complex128
    )


def _phase(lam: float) -> NDArray[np.complexfloating]:
    return np.array([[1, 0], [0, np.exp(_I * lam)]], dtype=np.complex128)


def _u(theta: float, phi: float, lam: float) -> NDArray[np.complexfloating]:
    c, s = math.cos(theta / 2), math.sin(theta / 2)
    return np.array(
        [
            [c, -np.exp(_I * lam) * s],
            [np.exp(_I * phi) * s, np.exp(_I * (phi + lam)) * c],
        ],
        dtype=np.complex128,
    )


def _controlled(u2x2: NDArray) -> NDArray[np.complexfloating]:
    """Lift a 2x2 single-qubit unitary to a 4x4 controlled-U."""
    cu = np.eye(4, dtype=np.complex128)
    cu[2:4, 2:4] = u2x2
    return cu


_SWAP_MAT = np.array(
    [[1, 0, 0, 0], [0, 0, 1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], dtype=np.complex128
)
_ISWAP_MAT = np.array(
    [[1, 0, 0, 0], [0, 0, _I, 0], [0, _I, 0, 0], [0, 0, 0, 1]], dtype=np.complex128
)


# ---------------------------------------------------------------------------
# Statevector engine
# ---------------------------------------------------------------------------


class StatevectorSimulator:
    """Dense complex128 statevector simulation on CPU.

    Gate application uses axis-reshape + einsum — avoids building the full
    2^n x 2^n Kronecker product. For single-qubit gates on qubit q of an
    n-qubit system, reshape sv as (2^q, 2, 2^(n-q-1)), apply the 2x2 unitary
    along axis 1, then reshape back.
    """

    def __init__(self, seed: int | None = None) -> None:
        self._rng = np.random.default_rng(seed)

    def simulate(
        self, n_qubits: int, n_cbits: int, n_shots: int, instructions: list[RawGateInstruction]
    ) -> dict[str, int]:
        """Run instructions on |0...0⟩ and return a shot-count histogram.

        Args:
            n_qubits: Number of qubits.
            n_cbits: Number of classical bits.
            n_shots: Desired number of measurement shots.
            instructions: Decoded gate instruction list.

        Returns:
            Dict mapping zero-padded binary bitstring (length n_cbits) to count.
        """
        sv: NDArray[np.complexfloating] = np.zeros(2**n_qubits, dtype=np.complex128)
        sv[0] = 1.0  # |0...0⟩

        # Collect (qubit_idx, cbit_idx) measurement pairs in order
        meas_map: list[tuple[int, int]] = []

        for instr in instructions:
            m = instr.mnemonic
            if m in ("QNOP", "BARRIER", "QHALT"):
                continue
            if m == "MEAS":
                meas_map.append((instr.operands[0], instr.operands[1]))
                continue
            sv = self._apply(sv, n_qubits, instr)

        probs = (sv * sv.conj()).real
        probs = np.maximum(probs, 0.0)
        probs /= probs.sum()  # renormalize for floating-point drift

        sampled = self._rng.choice(2**n_qubits, size=n_shots, p=probs)
        return self._build_histogram(sampled, meas_map, n_cbits)

    def _apply(
        self, sv: NDArray, n_qubits: int, instr: RawGateInstruction
    ) -> NDArray[np.complexfloating]:
        """Apply one gate instruction to the statevector."""
        m = instr.mnemonic
        ops = instr.operands

        if m in _FIXED_GATES:
            return _apply_single(sv, n_qubits, int(ops[0]), _FIXED_GATES[m])
        if m == "RX":
            return _apply_single(sv, n_qubits, int(ops[0]), _rx(_u32_to_rad(ops[1])))
        if m == "RY":
            return _apply_single(sv, n_qubits, int(ops[0]), _ry(_u32_to_rad(ops[1])))
        if m == "RZ":
            return _apply_single(sv, n_qubits, int(ops[0]), _rz(_u32_to_rad(ops[1])))
        if m == "P":
            return _apply_single(sv, n_qubits, int(ops[0]), _phase(_u32_to_rad(ops[1])))
        if m == "U":
            return _apply_single(
                sv, n_qubits, int(ops[0]),
                _u(_u32_to_rad(ops[1]), _u32_to_rad(ops[2]), _u32_to_rad(ops[3])),
            )
        if m == "CNOT":
            return _apply_two(sv, n_qubits, int(ops[0]), int(ops[1]), _controlled(_FIXED_GATES["X"]))
        if m == "CZ":
            return _apply_two(sv, n_qubits, int(ops[0]), int(ops[1]), _controlled(_FIXED_GATES["Z"]))
        if m == "SWAP":
            return _apply_two(sv, n_qubits, int(ops[0]), int(ops[1]), _SWAP_MAT)
        if m == "ISWAP":
            return _apply_two(sv, n_qubits, int(ops[0]), int(ops[1]), _ISWAP_MAT)
        if m == "CP":
            return _apply_two(sv, n_qubits, int(ops[0]), int(ops[1]), _controlled(_phase(_u32_to_rad(ops[2]))))
        if m == "RZZ":
            # RZZ(θ) = exp(-iθ/2 Z⊗Z) = diag(e^{-iθ/2}, e^{iθ/2}, e^{iθ/2}, e^{-iθ/2})
            theta = _u32_to_rad(ops[2])
            rzz_mat = np.diag([
                np.exp(-_I * theta / 2),
                np.exp(_I * theta / 2),
                np.exp(_I * theta / 2),
                np.exp(-_I * theta / 2),
            ]).astype(np.complex128)
            return _apply_two(sv, n_qubits, int(ops[0]), int(ops[1]), rzz_mat)
        if m == "CRX":
            return _apply_two(sv, n_qubits, int(ops[0]), int(ops[1]), _controlled(_rx(_u32_to_rad(ops[2]))))

        raise ValueError(f"Unhandled gate mnemonic in CPU sim: {m!r}")

    @staticmethod
    def _build_histogram(
        sampled: NDArray, meas_map: list[tuple[int, int]], n_cbits: int
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        for raw_state in sampled:
            cval = 0
            for qubit_idx, cbit_idx in meas_map:
                bit = (int(raw_state) >> qubit_idx) & 1
                cval |= bit << cbit_idx
            key = format(cval, f"0{n_cbits}b")
            counts[key] = counts.get(key, 0) + 1
        return counts


def _apply_single(
    sv: NDArray, n: int, q: int, u: NDArray
) -> NDArray[np.complexfloating]:
    """Apply 2x2 unitary to qubit q of an n-qubit statevector."""
    sv = sv.reshape([2] * n)
    sv = np.tensordot(u, sv, axes=[[1], [q]])
    # tensordot moves the contracted axis to front; restore qubit q to position q
    sv = np.moveaxis(sv, 0, q)
    return sv.reshape(-1)


def _apply_two(
    sv: NDArray, n: int, q0: int, q1: int, u: NDArray
) -> NDArray[np.complexfloating]:
    """Apply 4x4 unitary to (ctrl, tgt) pair in an n-qubit statevector."""
    sv = sv.reshape([2] * n)
    # Bring the two target qubits to front, flatten to 4-element axis
    axes = [q0, q1] + [i for i in range(n) if i not in (q0, q1)]
    sv = sv.transpose(axes).reshape(4, -1)
    sv = u @ sv
    sv = sv.reshape([2, 2] + [2] * (n - 2))
    # Invert the transpose
    inv_axes = [0] * n
    for new, old in enumerate(axes):
        inv_axes[old] = new
    sv = sv.transpose(inv_axes)
    return sv.reshape(-1)


# ---------------------------------------------------------------------------
# GateSolver wrapper
# ---------------------------------------------------------------------------


class SolverCPUSim(GateSolver):
    """NumPy statevector CPU backend for gate circuits.

    Supports all 25 gate types in the .xqg v1 ISA. Seeded for reproducibility.
    """

    def __init__(self, seed: int | None = None) -> None:
        self._sim = StatevectorSimulator(seed=seed)

    def run(self, circuit: GateCircuit, **kwargs: Any) -> GateSolverResult:
        """Simulate a gate circuit and return shot-count statistics.

        Args:
            circuit: Decoded .xqg GateCircuit.
            **kwargs: Accepts ``n_shots`` to override the circuit header value.

        Returns:
            GateSolverResult with CPU simulation results.
        """
        self._validate_circuit(circuit)
        n_shots = int(kwargs.get("n_shots", circuit.header.n_shots))
        t0 = time.perf_counter()
        counts = self._sim.simulate(
            n_qubits=circuit.header.n_qubits,
            n_cbits=circuit.header.n_cbits,
            n_shots=n_shots,
            instructions=list(circuit.instructions),
        )
        elapsed = time.perf_counter() - t0
        return GateSolverResult(
            counts=counts,
            total_shots=sum(counts.values()),
            timing=elapsed,
        )
```

- [ ] **Step 3: Run the CPU sim tests**

```bash
uv run pytest xqgb/tests/test_cpu_sim.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 4: Commit**

```bash
git add xqgb/cpu_sim.py xqgb/tests/test_cpu_sim.py
git commit -m "feat(xqgb): NumPy statevector CPU simulator with full gate set"
```

---

## W3 · Part 1 — `xqgb`: Transpilers and IBM Quantum Backend

**Files:**
- Create: `xqgb/transpiler.py`
- Create: `xqgb/ibm_quantum.py`

**Interfaces:**
- Consumes: `GateCircuit`, `GateSolver`, `GateSolverResult` from W2 Part 1
- Produces: `QiskitTranspiler`, `IonQTranspiler`, `SolverIBMQuantum(GateSolver)`

- [ ] **Step 1: Write `xqgb/transpiler.py`**

```python
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Circuit transpilers: .xqg GateCircuit → backend-specific circuit objects."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from xqgb.decoder import GateCircuit, RawGateInstruction

T = TypeVar("T")

# Angle conversion: u32 binary-angular units → radians
_ANGLE_SCALE = 2 * math.pi / 4_294_967_296

# Qiskit gate name mapping: xqg mnemonic -> (qiskit_method, positional_arg_extractor)
# Extractor receives operands tuple; returns args to pass before qubit(s).
_QISKIT_MAP: dict[str, tuple[str, list[int]]] = {
    "H":      ("h",    []),
    "X":      ("x",    []),
    "Y":      ("y",    []),
    "Z":      ("z",    []),
    "S":      ("s",    []),
    "SDG":    ("sdg",  []),
    "T":      ("t",    []),
    "TDG":    ("tdg",  []),
    "SX":     ("sx",   []),
    "RX":     ("rx",   [1]),   # operands[1] = theta
    "RY":     ("ry",   [1]),
    "RZ":     ("rz",   [1]),
    "P":      ("p",    [1]),   # operands[1] = lambda
    "CNOT":   ("cx",   []),
    "CZ":     ("cz",   []),
    "SWAP":   ("swap", []),
    "ISWAP":  ("iswap",[]),
    "CP":     ("cp",   [2]),   # operands[2] = lambda
    "RZZ":    ("rzz",  [2]),   # operands[2] = theta
    "CRX":    ("crx",  [2]),   # operands[2] = theta
}

# IonQ native gate mapping.
_IONQ_MAP: dict[str, str] = {
    "H": "h", "X": "x", "Y": "y", "Z": "z",
    "S": "s", "SDG": "si", "T": "t", "TDG": "ti",
    "SX": "v", "RX": "rx", "RY": "ry", "RZ": "rz",
    "CNOT": "cnot", "SWAP": "swap",
}


class GateCircuitTranspiler(ABC, Generic[T]):
    """Translate a GateCircuit to a backend-specific circuit representation.

    Args:
        T: The target circuit type (e.g. ``qiskit.QuantumCircuit``, ``dict``).
    """

    @abstractmethod
    def transpile(self, circuit: GateCircuit) -> T:
        """Convert a decoded GateCircuit to the backend circuit format.

        Args:
            circuit: Decoded .xqg circuit.

        Returns:
            Backend-specific circuit object.

        Raises:
            ValueError: If the circuit contains a gate unsupported by the backend.
        """
        ...


class QiskitTranspiler(GateCircuitTranspiler["QuantumCircuit"]):
    """Translate a GateCircuit to a Qiskit QuantumCircuit."""

    def transpile(self, circuit: GateCircuit) -> "QuantumCircuit":
        """Build a Qiskit QuantumCircuit from a decoded .xqg circuit.

        Args:
            circuit: Decoded .xqg circuit.

        Returns:
            Equivalent Qiskit QuantumCircuit with classical measurement register.

        Raises:
            ImportError: If qiskit is not installed.
            ValueError: If the circuit contains a gate not supported by Qiskit.
        """
        from qiskit import QuantumCircuit  # deferred import

        qc = QuantumCircuit(circuit.header.n_qubits, circuit.header.n_cbits)
        for instr in circuit.instructions:
            if instr.mnemonic in ("QNOP", "QHALT"):
                continue
            if instr.mnemonic == "BARRIER":
                qc.barrier()
                continue
            if instr.mnemonic == "MEAS":
                qc.measure(instr.operands[0], instr.operands[1])
                continue
            if instr.mnemonic == "U":
                qc.u(
                    instr.operands[1] * _ANGLE_SCALE,
                    instr.operands[2] * _ANGLE_SCALE,
                    instr.operands[3] * _ANGLE_SCALE,
                    instr.operands[0],
                )
                continue
            if instr.mnemonic not in _QISKIT_MAP:
                raise ValueError(
                    f"Gate {instr.mnemonic!r} has no Qiskit mapping; add it to _QISKIT_MAP"
                )
            method_name, angle_indices = _QISKIT_MAP[instr.mnemonic]
            # Convert u32 binary-angular operands to radians before passing to Qiskit
            angles = [instr.operands[i] * _ANGLE_SCALE for i in angle_indices]
            # Qubit operands are the non-angle leading entries
            n_angles = len(angle_indices)
            n_qubits_in_gate = len(instr.operands) - n_angles
            qubits = [instr.operands[i] for i in range(n_qubits_in_gate)]
            getattr(qc, method_name)(*angles, *qubits)
        return qc


class IonQTranspiler(GateCircuitTranspiler[dict]):
    """Translate a GateCircuit to IonQ's JSON circuit dict."""

    def transpile(self, circuit: GateCircuit) -> dict:
        """Build an IonQ JSON circuit from a decoded .xqg circuit.

        IonQ measures all qubits at the end implicitly; MEAS instructions
        are collected and used only to map qubit-to-cbit ordering.

        Args:
            circuit: Decoded .xqg circuit.

        Returns:
            IonQ-format dict with keys ``qubits`` and ``circuit``.

        Raises:
            ValueError: If the circuit contains a gate unsupported by IonQ.
        """
        gates: list[dict] = []
        for instr in circuit.instructions:
            m = instr.mnemonic
            if m in ("QNOP", "BARRIER", "QHALT", "MEAS"):
                continue
            if m not in _IONQ_MAP:
                raise ValueError(
                    f"Gate {m!r} is not natively supported by IonQ; decompose first."
                )
            gate_entry: dict = {"gate": _IONQ_MAP[m]}
            ops = instr.operands
            if m in ("RX", "RY", "RZ"):
                gate_entry["target"] = int(ops[0])
                gate_entry["rotation"] = ops[1] * _ANGLE_SCALE  # u32 → radians
            elif m == "CNOT":
                gate_entry["control"] = int(ops[0])
                gate_entry["target"] = int(ops[1])
            elif m == "SWAP":
                gate_entry["targets"] = [int(ops[0]), int(ops[1])]
            else:
                gate_entry["target"] = int(ops[0])
            gates.append(gate_entry)
        return {"qubits": circuit.header.n_qubits, "circuit": gates}
```

- [ ] **Step 2: Write `xqgb/ibm_quantum.py`**

```python
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later

"""IBM Quantum backend via qiskit-ibm-runtime."""

from __future__ import annotations

import os
import time
from typing import Any

from xqgb.decoder import GateCircuit
from xqgb.solver import GateSolver, GateSolverResult
from xqgb.transpiler import QiskitTranspiler


class SolverIBMQuantum(GateSolver):
    """IBM Quantum gate-circuit backend.

    Supports both Qiskit Aer simulation (``backend="aer_simulator"``, default,
    no credentials needed) and real IBM hardware (requires ``IBM_QUANTUM_TOKEN``
    environment variable or ``token`` constructor argument).

    Args:
        backend: Backend name. ``"aer_simulator"`` runs locally via Qiskit Aer;
            any other value is treated as a real IBM Quantum backend name.
        token: IBM Quantum API token. Falls back to the ``IBM_QUANTUM_TOKEN``
            environment variable. Required for real hardware.
        instance: IBM Quantum instance string (e.g. ``"ibm-q/open/main"``).
            Falls back to ``IBM_QUANTUM_INSTANCE`` env var.
    """

    def __init__(
        self,
        backend: str = "aer_simulator",
        token: str | None = None,
        instance: str | None = None,
    ) -> None:
        self._backend_name = backend
        self._token = token or os.environ.get("IBM_QUANTUM_TOKEN")
        self._instance = (
            instance or os.environ.get("IBM_QUANTUM_INSTANCE", "ibm-q/open/main")
        )
        self._transpiler = QiskitTranspiler()
        self._backend = self._init_backend()

    def _is_simulator(self) -> bool:
        return self._backend_name.startswith("aer_")

    def _init_backend(self) -> Any:
        if self._is_simulator():
            try:
                from qiskit_aer import AerSimulator
            except ImportError as exc:
                raise ImportError(
                    "Qiskit Aer not installed. Install with: pip install xqgb[ibm]"
                ) from exc
            return AerSimulator()

        if self._token is None:
            raise ValueError(
                "IBM Quantum token required for real hardware. "
                "Pass token= or set IBM_QUANTUM_TOKEN environment variable."
            )
        try:
            from qiskit_ibm_runtime import QiskitRuntimeService
        except ImportError as exc:
            raise ImportError(
                "qiskit-ibm-runtime not installed. Install with: pip install xqgb[ibm]"
            ) from exc
        service = QiskitRuntimeService(
            channel="ibm_quantum", token=self._token, instance=self._instance
        )
        return service.backend(self._backend_name)

    def run(self, circuit: GateCircuit, **kwargs: Any) -> GateSolverResult:
        """Execute a gate circuit on IBM Quantum (Aer sim or real hardware).

        Args:
            circuit: Decoded .xqg GateCircuit.
            **kwargs: Accepts ``n_shots`` to override circuit header value.

        Returns:
            GateSolverResult with measurement counts.

        Raises:
            ImportError: If qiskit packages are not installed.
            ValueError: If running on hardware without a valid token.
        """
        from qiskit import transpile as qiskit_transpile

        self._validate_circuit(circuit)
        n_shots = int(kwargs.get("n_shots", circuit.header.n_shots))
        qc = self._transpiler.transpile(circuit)
        qc_compiled = qiskit_transpile(qc, self._backend)

        t0 = time.perf_counter()
        if self._is_simulator():
            job = self._backend.run(qc_compiled, shots=n_shots)
            result = job.result()
            raw_counts: dict[str, int] = result.get_counts()
        else:
            from qiskit_ibm_runtime import SamplerV2
            sampler = SamplerV2(backend=self._backend)
            job = sampler.run([qc_compiled], shots=n_shots)
            result = job.result()
            raw_counts = dict(result[0].data.c.get_counts())
        elapsed = time.perf_counter() - t0

        # Normalise: remove spaces Qiskit inserts between registers
        counts = {k.replace(" ", ""): v for k, v in raw_counts.items()}
        return GateSolverResult(
            counts=counts,
            total_shots=sum(counts.values()),
            timing=elapsed,
            metadata={"backend": self._backend_name},
        )
```

- [ ] **Step 3: Lint check**

```bash
uv run ruff check xqgb/transpiler.py xqgb/ibm_quantum.py
```

Expected: 0 issues.

- [ ] **Step 4: Commit**

```bash
git add xqgb/transpiler.py xqgb/ibm_quantum.py
git commit -m "feat(xqgb): Qiskit/IonQ transpilers and IBM Quantum backend"
```

---

## W3 · Part 2 — `xqgb`: IonQ Backend and Registry

**Files:**
- Create: `xqgb/ionq.py`
- Create: `xqgb/registry.py`
- Create: `xqgb/tests/test_registry.py`
- Modify: `xqgb/__init__.py`

**Interfaces:**
- Consumes: all solver classes from W2 Part 2 and W3 Part 1, `IonQTranspiler` from W3 Part 1
- Produces: `SolverIonQ(GateSolver)`, `GATE_SOLVERS` dict, `build_gate_solver(name, **kwargs) -> GateSolver`

- [ ] **Step 1: Write failing tests** in `xqgb/tests/test_registry.py`

```python
"""Registry unit tests — no cloud credentials needed."""

import pytest

from xqgb.registry import GATE_SOLVERS, DEFAULT_GATE_SOLVER, build_gate_solver
from xqgb.cpu_sim import SolverCPUSim


def test_all_solvers_registered():
    expected = {"cpu-sim", "ibm-aer", "ibm-quantum", "ionq-sim", "ionq"}
    assert expected == set(GATE_SOLVERS.keys())


def test_default_solver_is_cpu_sim():
    assert DEFAULT_GATE_SOLVER == "cpu-sim"


def test_build_cpu_sim():
    solver = build_gate_solver("cpu-sim", seed=42)
    assert isinstance(solver, SolverCPUSim)


def test_build_unknown_raises():
    with pytest.raises(ValueError, match="Unknown gate solver"):
        build_gate_solver("not-a-real-solver")
```

Run: `uv run pytest xqgb/tests/test_registry.py` → expected: import error.

- [ ] **Step 2: Write `xqgb/ionq.py`**

```python
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later

"""IonQ gate-circuit backend via the ionq Python SDK."""

from __future__ import annotations

import os
import time
from typing import Any

from xqgb.decoder import GateCircuit
from xqgb.solver import GateSolver, GateSolverResult
from xqgb.transpiler import IonQTranspiler


class SolverIonQ(GateSolver):
    """IonQ trapped-ion gate backend.

    Routes to IonQ's cloud simulator (``backend="simulator"``, default) or
    physical QPU (``backend="qpu"``). Requires the ``ionq`` Python SDK and an
    API key.

    Args:
        backend: IonQ target — ``"simulator"`` or ``"qpu"``.
        token: IonQ API key. Falls back to the ``IONQ_API_KEY`` environment
            variable.

    Raises:
        ValueError: If no API key is available at construction time.
        ImportError: If the ``ionq`` SDK is not installed.
    """

    def __init__(
        self,
        backend: str = "simulator",
        token: str | None = None,
    ) -> None:
        self._backend = backend
        self._token = token or os.environ.get("IONQ_API_KEY")
        if self._token is None:
            raise ValueError(
                "IonQ API key required. Pass token= or set IONQ_API_KEY environment variable."
            )
        self._transpiler = IonQTranspiler()

    def run(self, circuit: GateCircuit, **kwargs: Any) -> GateSolverResult:
        """Execute a gate circuit on IonQ (simulator or QPU).

        IonQ returns a probability histogram rather than raw shot counts.
        This method converts probabilities to counts using the requested
        shot count, distributing any rounding remainder to the most probable
        outcome.

        Args:
            circuit: Decoded .xqg GateCircuit.
            **kwargs: Accepts ``n_shots`` to override circuit header value.

        Returns:
            GateSolverResult with measurement counts.

        Raises:
            ImportError: If the ``ionq`` SDK is not installed.
            ValueError: If the circuit contains gates unsupported by IonQ.
        """
        try:
            import ionq as ionq_sdk
        except ImportError as exc:
            raise ImportError(
                "ionq SDK not installed. Install with: pip install xqgb[ionq]"
            ) from exc

        self._validate_circuit(circuit)
        n_shots = int(kwargs.get("n_shots", circuit.header.n_shots))
        ionq_circuit = self._transpiler.transpile(circuit)

        t0 = time.perf_counter()
        client = ionq_sdk.IonQClient(api_key=self._token)
        job = client.create_job(
            target=self._backend,
            circuit=ionq_circuit,
            shots=n_shots,
        )
        result = client.get_results(job["id"], wait=True)
        elapsed = time.perf_counter() - t0

        counts = _histogram_to_counts(
            result.get("histogram", {}), n_shots, circuit.header.n_cbits
        )
        return GateSolverResult(
            counts=counts,
            total_shots=sum(counts.values()),
            timing=elapsed,
            metadata={"backend": self._backend, "job_id": job["id"]},
        )


def _histogram_to_counts(
    histogram: dict, n_shots: int, n_cbits: int
) -> dict[str, int]:
    """Convert IonQ probability histogram to integer shot counts.

    IonQ returns probabilities in [0, 1] keyed by state index as string.
    Truncates to ints and assigns rounding remainder to the most probable
    outcome so that total shots sum exactly to n_shots.

    Args:
        histogram: Dict mapping state index string to probability float.
        n_shots: Total number of shots to distribute.
        n_cbits: Number of classical bits (for zero-padding bitstrings).

    Returns:
        Dict mapping zero-padded binary bitstring to integer count.
    """
    if not histogram:
        return {}

    items = [(int(k), v) for k, v in histogram.items()]
    raw_counts = [(idx, int(prob * n_shots)) for idx, prob in items]
    total = sum(c for _, c in raw_counts)
    remainder = n_shots - total

    # Assign remainder to the most probable outcome
    if remainder > 0:
        most_probable_idx = max(items, key=lambda kv: kv[1])[0]
        raw_counts = [
            (idx, cnt + remainder if idx == most_probable_idx else cnt)
            for idx, cnt in raw_counts
        ]

    return {format(idx, f"0{n_cbits}b"): cnt for idx, cnt in raw_counts if cnt > 0}
```

- [ ] **Step 3: Write `xqgb/registry.py`**

```python
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Gate solver registry and factory for selecting a backend by name."""

from __future__ import annotations

from typing import Any

from xqgb.cpu_sim import SolverCPUSim
from xqgb.ibm_quantum import SolverIBMQuantum
from xqgb.ionq import SolverIonQ
from xqgb.solver import GateSolver

GATE_SOLVERS: dict[str, type[GateSolver]] = {
    "cpu-sim":     SolverCPUSim,
    "ibm-aer":     SolverIBMQuantum,
    "ibm-quantum": SolverIBMQuantum,
    "ionq-sim":    SolverIonQ,
    "ionq":        SolverIonQ,
}

DEFAULT_GATE_SOLVER = "cpu-sim"


def build_gate_solver(name: str, **kwargs: Any) -> GateSolver:
    """Construct a gate solver by registry name.

    Args:
        name: A key in :data:`GATE_SOLVERS`.
        **kwargs: Forwarded to the solver constructor. IBM backends accept
            ``backend``, ``token``, and ``instance``; IonQ backends accept
            ``backend`` and ``token``; CPU sim accepts ``seed``.

    Returns:
        A constructed :class:`GateSolver` instance.

    Raises:
        ValueError: If ``name`` is not a registered solver name.
    """
    if name not in GATE_SOLVERS:
        raise ValueError(
            f"Unknown gate solver {name!r}. "
            f"Choose one of: {', '.join(sorted(GATE_SOLVERS))}."
        )
    cls = GATE_SOLVERS[name]
    if name == "ibm-aer":
        kwargs.setdefault("backend", "aer_simulator")
    elif name == "ibm-quantum":
        kwargs.setdefault("backend", "ibm_brisbane")
    elif name == "ionq-sim":
        kwargs.setdefault("backend", "simulator")
    elif name == "ionq":
        kwargs.setdefault("backend", "qpu")
    return cls(**kwargs)
```

- [ ] **Step 4: Update `xqgb/__init__.py`** to expose registry

```python
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later

"""XQuad gate-circuit backend package."""

from xqgb.decoder import GateCircuit, XqgHeader, RawGateInstruction, decode_xqg
from xqgb.solver import GateSolver, GateSolverResult
from xqgb.registry import GATE_SOLVERS, DEFAULT_GATE_SOLVER, build_gate_solver

__all__ = [
    "GateCircuit",
    "XqgHeader",
    "RawGateInstruction",
    "decode_xqg",
    "GateSolver",
    "GateSolverResult",
    "GATE_SOLVERS",
    "DEFAULT_GATE_SOLVER",
    "build_gate_solver",
]
```

- [ ] **Step 5: Run all tests**

```bash
uv run pytest xqgb/ -v
```

Expected: all 12 tests pass (4 decoder + 5 CPU sim + 3 registry).

- [ ] **Step 6: Run Rust tests**

```bash
cargo test -p xqgbc
```

Expected: all pass.

- [ ] **Step 7: Lint**

```bash
uv run ruff check xqgb/
cargo clippy -p xqgbc -- -D warnings
```

Expected: 0 issues each.

- [ ] **Step 8: Commit**

```bash
git add xqgb/ionq.py xqgb/registry.py xqgb/tests/test_registry.py xqgb/__init__.py
git commit -m "feat(xqgb): IonQ backend and gate solver registry"
```

---

## W4 — Conformance Hardening: Gate ISA Parity

**Files:**
- Create: `conformance/gate_opcodes.yaml`
- Create: `scripts/check-gate-opcode-parity.py`
- Create: `scripts/gen-gate-vectors.py`
- Create: `conformance/vectors/gate/` (generated then committed)
- Modify: `conformance/build.rs` — add `gate` vector category runner
- Modify: `conformance/Cargo.toml` — add `xqgbc` dependency
- Modify: `conformance/src/lib.rs` — add `run_rust_gate` and `run_python_gate`

**Interfaces:**
- Consumes: `xqgbc::codec::{encode, decode}` from W1 Part 2; `xqgb.decoder.decode_xqg` from W2 Part 1
- Produces: CI checks that fail if Rust and Python gate ISA tables or wire codecs diverge

- [ ] **Step 1: Write `conformance/gate_opcodes.yaml`**

This is the canonical source of truth. Every gate opcode appears once; both the Rust parity check and Python parity check are validated against it.

```yaml
# Gate ISA Opcode Table — canonical source of truth for xqgbc and xqgb.
#
# Schema per entry:
#   code:     u8 wire byte (hex).
#   mnemonic: uppercase gate name.
#   operands: ordered list of {name, type, width}.
#             type ∈ {qubit, cbit, angle}
#             width: byte count on the wire (qubit=1, cbit=1, angle=4)
#
# Wire layout: [code u8] [operand bytes, left-to-right, big-endian]
# Angles are u32 binary-angular units (full turn = 2^32). No f64.

version: 1

gate_opcodes:

  # -------------------------------------------------------------------------
  # Meta
  # -------------------------------------------------------------------------
  - code: 0x80
    mnemonic: QNOP
    operands: []

  - code: 0x81
    mnemonic: QHALT
    operands: []

  - code: 0x82
    mnemonic: BARRIER
    operands: []

  - code: 0x83
    mnemonic: MEAS
    operands:
      - {name: q, type: qubit, width: 1}
      - {name: c, type: cbit,  width: 1}

  # -------------------------------------------------------------------------
  # Single-qubit fixed gates
  # -------------------------------------------------------------------------
  - code: 0x90
    mnemonic: H
    operands:
      - {name: q, type: qubit, width: 1}

  - code: 0x91
    mnemonic: X
    operands:
      - {name: q, type: qubit, width: 1}

  - code: 0x92
    mnemonic: Y
    operands:
      - {name: q, type: qubit, width: 1}

  - code: 0x93
    mnemonic: Z
    operands:
      - {name: q, type: qubit, width: 1}

  - code: 0x94
    mnemonic: S
    operands:
      - {name: q, type: qubit, width: 1}

  - code: 0x95
    mnemonic: SDG
    operands:
      - {name: q, type: qubit, width: 1}

  - code: 0x96
    mnemonic: T
    operands:
      - {name: q, type: qubit, width: 1}

  - code: 0x97
    mnemonic: TDG
    operands:
      - {name: q, type: qubit, width: 1}

  - code: 0x98
    mnemonic: SX
    operands:
      - {name: q, type: qubit, width: 1}

  # -------------------------------------------------------------------------
  # Single-qubit rotation gates
  # -------------------------------------------------------------------------
  - code: 0xA0
    mnemonic: RX
    operands:
      - {name: q,     type: qubit, width: 1}
      - {name: theta, type: angle, width: 4}

  - code: 0xA1
    mnemonic: RY
    operands:
      - {name: q,     type: qubit, width: 1}
      - {name: theta, type: angle, width: 4}

  - code: 0xA2
    mnemonic: RZ
    operands:
      - {name: q,     type: qubit, width: 1}
      - {name: theta, type: angle, width: 4}

  - code: 0xA3
    mnemonic: P
    operands:
      - {name: q,      type: qubit, width: 1}
      - {name: lambda, type: angle, width: 4}

  - code: 0xA4
    mnemonic: U
    operands:
      - {name: q,     type: qubit, width: 1}
      - {name: theta, type: angle, width: 4}
      - {name: phi,   type: angle, width: 4}
      - {name: lam,   type: angle, width: 4}

  # -------------------------------------------------------------------------
  # Two-qubit fixed gates
  # -------------------------------------------------------------------------
  - code: 0xB0
    mnemonic: CNOT
    operands:
      - {name: ctrl, type: qubit, width: 1}
      - {name: tgt,  type: qubit, width: 1}

  - code: 0xB1
    mnemonic: CZ
    operands:
      - {name: ctrl, type: qubit, width: 1}
      - {name: tgt,  type: qubit, width: 1}

  - code: 0xB2
    mnemonic: SWAP
    operands:
      - {name: q0, type: qubit, width: 1}
      - {name: q1, type: qubit, width: 1}

  - code: 0xB3
    mnemonic: ISWAP
    operands:
      - {name: q0, type: qubit, width: 1}
      - {name: q1, type: qubit, width: 1}

  # -------------------------------------------------------------------------
  # Two-qubit rotation gates
  # -------------------------------------------------------------------------
  - code: 0xC0
    mnemonic: CP
    operands:
      - {name: ctrl,   type: qubit, width: 1}
      - {name: tgt,    type: qubit, width: 1}
      - {name: lambda, type: angle, width: 4}

  - code: 0xC1
    mnemonic: RZZ
    operands:
      - {name: q0,    type: qubit, width: 1}
      - {name: q1,    type: qubit, width: 1}
      - {name: theta, type: angle, width: 4}

  - code: 0xC2
    mnemonic: CRX
    operands:
      - {name: ctrl,  type: qubit, width: 1}
      - {name: tgt,   type: qubit, width: 1}
      - {name: theta, type: angle, width: 4}
```

- [ ] **Step 2: Run the parity check (will fail — files not yet matched)**

```bash
uv run python scripts/check-gate-opcode-parity.py
```

Expected: `ERROR` — Python `_OPCODE_TABLE` and/or Rust `gate_opcodes!` not yet validated against the YAML. This confirms the script works.

- [ ] **Step 3: Write `scripts/check-gate-opcode-parity.py`**

```python
#!/usr/bin/env python3
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Validate Rust gate_opcodes! and Python _OPCODE_TABLE against gate_opcodes.yaml.

Exit 0 on full agreement. Exit 1 and print a diff on any discrepancy.
Run in CI after any change to xqgbc/src/table.rs or xqgb/decoder.py.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml  # pyyaml, dev-dep

ROOT = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# Load canonical YAML
# ---------------------------------------------------------------------------

def load_yaml() -> dict[int, dict]:
    """Returns {code: {mnemonic, operands: [{type, width}]}}."""
    raw = yaml.safe_load((ROOT / "conformance" / "gate_opcodes.yaml").read_text())
    return {
        int(entry["code"], 16): {
            "mnemonic": entry["mnemonic"],
            "operands": [
                {"type": op["type"], "width": op["width"]}
                for op in entry.get("operands", [])
            ],
        }
        for entry in raw["gate_opcodes"]
    }

# ---------------------------------------------------------------------------
# Parse Rust gate_opcodes! X-macro
# ---------------------------------------------------------------------------

_RUST_TYPE_WIDTH = {"Qubit": 1, "Cbit": 1, "GateAngle": 4}
_RUST_TYPE_NAME  = {"Qubit": "qubit", "Cbit": "cbit", "GateAngle": "angle"}

def load_rust() -> dict[int, dict]:
    """Parse xqgbc/src/table.rs gate_opcodes! entries.

    Entry shape in source:
        #[gate(0xA0, "RX", 1)]
        Rx { q: $crate::Qubit, theta: $crate::GateAngle },
    """
    src = (ROOT / "xqgbc" / "src" / "table.rs").read_text()
    # Match #[gate(code, "MNEM", arity)] followed by VariantName and optional { fields }
    pattern = re.compile(
        r'#\[gate\(\s*(0x[0-9A-Fa-f]{2})\s*,\s*"(\w+)"\s*,\s*\d+\s*\)\]\s*'
        r'\w+\s*(?:\{([^}]*)\})?',
        re.DOTALL,
    )
    out: dict[int, dict] = {}
    for m in pattern.finditer(src):
        code = int(m.group(1), 16)
        mnemonic = m.group(2)
        fields_str = (m.group(3) or "").strip()
        operands = []
        if fields_str:
            for field in fields_str.split(","):
                field = field.strip()
                if not field:
                    continue
                _, rust_type = field.split(":", 1)
                # Strip $crate:: path prefix used inside macro_rules! definitions
                rust_type = rust_type.strip().removeprefix("$crate::")
                width = _RUST_TYPE_WIDTH.get(rust_type)
                if width is None:
                    print(f"ERROR: unknown Rust operand type {rust_type!r} in table.rs")
                    sys.exit(1)
                operands.append({"type": _RUST_TYPE_NAME[rust_type], "width": width})
        out[code] = {"mnemonic": mnemonic, "operands": operands}
    return out

# ---------------------------------------------------------------------------
# Parse Python _OPCODE_TABLE
# ---------------------------------------------------------------------------

_PY_FORMAT_WIDTHS = {"q": ("qubit", 1), "c": ("cbit", 1), "a": ("angle", 4)}

def load_python() -> dict[int, dict]:
    """Parse xqgb/decoder.py _OPCODE_TABLE dict literal."""
    src = (ROOT / "xqgb" / "decoder.py").read_text()
    # Match lines like:  0xA0: ("RX", "qa"),
    pattern = re.compile(r"(0x[0-9A-Fa-f]{2})\s*:\s*\(\"(\w+)\"\s*,\s*\"([^\"]*)\"\s*\)")
    out: dict[int, dict] = {}
    for m in pattern.finditer(src):
        code = int(m.group(1), 16)
        mnemonic = m.group(2)
        fmt = m.group(3)
        operands = []
        for char in fmt:
            if char not in _PY_FORMAT_WIDTHS:
                print(f"ERROR: unknown format char {char!r} in decoder.py _OPCODE_TABLE")
                sys.exit(1)
            type_name, width = _PY_FORMAT_WIDTHS[char]
            operands.append({"type": type_name, "width": width})
        out[code] = {"mnemonic": mnemonic, "operands": operands}
    return out

# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------

def compare(label: str, got: dict[int, dict], want: dict[int, dict]) -> list[str]:
    errors: list[str] = []
    for code, expected in want.items():
        if code not in got:
            errors.append(f"  0x{code:02X} {expected['mnemonic']!r}: missing from {label}")
            continue
        actual = got[code]
        if actual["mnemonic"] != expected["mnemonic"]:
            errors.append(
                f"  0x{code:02X}: mnemonic {actual['mnemonic']!r} != {expected['mnemonic']!r} in {label}"
            )
        if actual["operands"] != expected["operands"]:
            errors.append(
                f"  0x{code:02X} {expected['mnemonic']!r}: operands mismatch in {label}\n"
                f"    yaml:  {expected['operands']}\n"
                f"    {label}: {actual['operands']}"
            )
    for code in got:
        if code not in want:
            errors.append(
                f"  0x{code:02X} {got[code]['mnemonic']!r}: in {label} but not in gate_opcodes.yaml"
            )
    return errors

def main() -> int:
    canonical = load_yaml()
    rust = load_rust()
    python = load_python()

    errors: list[str] = []
    errors += compare("Rust table.rs", rust, canonical)
    errors += compare("Python decoder.py", python, canonical)

    if errors:
        print("GATE OPCODE PARITY FAILURES:")
        for e in errors:
            print(e)
        return 1

    print(f"OK — {len(canonical)} gate opcodes match across YAML, Rust, and Python.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the parity check against live code**

```bash
uv run python scripts/check-gate-opcode-parity.py
```

Expected: `OK — 25 gate opcodes match across YAML, Rust, and Python.`

Fix any differences reported before proceeding.

- [ ] **Step 5: Write `scripts/gen-gate-vectors.py`**

This generates one codec vector per distinct operand shape (not per opcode — that would be 25 near-identical files). Each vector is a minimal valid `.xqg` with sentinel operand values, encoded by Python to guarantee the expected bytes are independent of the Rust encoder.

```python
#!/usr/bin/env python3
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Generate gate conformance vectors from gate_opcodes.yaml.

Creates conformance/vectors/gate/<name>/{circuit.xqg, expected_parse.json}
for codec vectors, and conformance/vectors/gate/bell_state/{...} for the
simulation vector.

Run after changing gate_opcodes.yaml or when adding new vector shapes.
Commit the generated files.
"""

from __future__ import annotations

import json
import math
import struct
import zlib
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
VECTORS = ROOT / "conformance" / "vectors" / "gate"

# Sentinel operand values (arbitrary but memorable)
_SENTINEL_QUBIT = 0x02
_SENTINEL_CBIT  = 0x01
_SENTINEL_ANGLE = 0x40000000  # π/2 as u32 (quarter turn)

_TAU = 2 * math.pi


def _encode_operand(op: dict) -> bytes:
    if op["type"] == "qubit":
        return bytes([_SENTINEL_QUBIT])
    if op["type"] == "cbit":
        return bytes([_SENTINEL_CBIT])
    if op["type"] == "angle":
        return struct.pack(">I", _SENTINEL_ANGLE)
    raise ValueError(f"unknown operand type: {op['type']}")


def _operand_value(op: dict) -> int:
    if op["type"] in ("qubit", "cbit"):
        return _SENTINEL_QUBIT if op["type"] == "qubit" else _SENTINEL_CBIT
    return _SENTINEL_ANGLE


def _make_xqg(instruction_bytes: bytes, n_qubits: int = 4, n_cbits: int = 4) -> bytes:
    """Wrap raw instruction bytes in a valid .xqg header."""
    crc = zlib.crc32(instruction_bytes) & 0xFFFFFFFF
    header = (
        b"XQGB"
        + bytes([1, n_qubits, n_cbits])
        + struct.pack(">I", 1)    # n_shots = 1
        + struct.pack(">I", crc)
    )
    return header + instruction_bytes


def operand_shape(entry: dict) -> str:
    """Compact shape key, e.g. 'q', 'qc', 'qa', 'qqq', 'qaaa'."""
    return "".join(
        {"qubit": "q", "cbit": "c", "angle": "a"}[op["type"]]
        for op in entry["operands"]
    )


def generate_codec_vectors(opcodes: list[dict]) -> None:
    """One vector per distinct operand shape (covers all wire-width combinations)."""
    seen_shapes: dict[str, str] = {}  # shape -> first mnemonic

    for entry in opcodes:
        code = int(entry["code"], 16)
        mnemonic = entry["mnemonic"]
        ops = entry.get("operands", [])
        shape = operand_shape(entry)

        if shape in seen_shapes:
            continue  # already covered by an earlier opcode with this shape
        seen_shapes[shape] = mnemonic

        name = f"codec_{mnemonic.lower()}"
        out_dir = VECTORS / name
        out_dir.mkdir(parents=True, exist_ok=True)

        # Build instruction bytes: [opcode] [operands...]
        instr_bytes = bytes([code]) + b"".join(_encode_operand(op) for op in ops)
        # Wrap in QHALT so the circuit is self-contained
        stream = instr_bytes + bytes([0x81])  # 0x81 = QHALT
        xqg = _make_xqg(stream)
        (out_dir / "circuit.xqg").write_bytes(xqg)

        # expected_parse.json: the two instructions the decoder must produce
        expected = [
            {"mnemonic": mnemonic, "operands": [_operand_value(op) for op in ops]},
            {"mnemonic": "QHALT",  "operands": []},
        ]
        (out_dir / "expected_parse.json").write_text(
            json.dumps(expected, indent=2) + "\n"
        )
        print(f"  wrote {name}/ (shape={shape!r})")


def generate_bell_state_vector() -> None:
    """Simulation vector: 2-qubit Bell state. Only {00, 11} outcomes allowed."""
    out_dir = VECTORS / "bell_state"
    out_dir.mkdir(parents=True, exist_ok=True)

    stream = (
        b"\x90\x00"      # H   q=0
        b"\xB0\x00\x01"  # CNOT ctrl=0 tgt=1
        b"\x83\x00\x00"  # MEAS q=0 c=0
        b"\x83\x01\x01"  # MEAS q=1 c=1
        b"\x81"          # QHALT
    )
    n_shots = 1024
    crc = zlib.crc32(stream) & 0xFFFFFFFF
    header = b"XQGB" + bytes([1, 2, 2]) + struct.pack(">I", n_shots) + struct.pack(">I", crc)
    (out_dir / "circuit.xqg").write_bytes(header + stream)

    # Bounds: each of {00, 11} must appear in at least 40% of shots.
    # Allows for shot noise while excluding degenerate outcomes.
    expected_counts = {
        "allowed_outcomes": ["00", "11"],
        "min_fraction_each": 0.40,
        "n_shots": n_shots,
    }
    (out_dir / "expected_counts.json").write_text(
        json.dumps(expected_counts, indent=2) + "\n"
    )
    print("  wrote bell_state/ (simulation vector)")


def main() -> None:
    raw = yaml.safe_load((ROOT / "conformance" / "gate_opcodes.yaml").read_text())
    opcodes = raw["gate_opcodes"]

    VECTORS.mkdir(parents=True, exist_ok=True)
    print("Generating gate codec vectors...")
    generate_codec_vectors(opcodes)
    print("Generating gate simulation vector...")
    generate_bell_state_vector()
    print("Done.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Generate and commit the vectors**

```bash
uv run python scripts/gen-gate-vectors.py
```

Expected output lists each generated directory. Verify the files look sane:

```bash
ls conformance/vectors/gate/
xxd conformance/vectors/gate/codec_rx/circuit.xqg
# Should show: 58 51 47 42 (XQGB magic), then header bytes, then A0 02 40 00 00 00 81
cat conformance/vectors/gate/codec_rx/expected_parse.json
```

Commit:

```bash
git add conformance/gate_opcodes.yaml conformance/vectors/gate/ scripts/
git commit -m "feat(conformance): gate_opcodes.yaml + codec/simulation conformance vectors"
```

- [ ] **Step 7: Extend `conformance/Cargo.toml` with `xqgbc` dep**

```toml
[dependencies]
xqvm.workspace   = true
xqasm.workspace  = true
xqgbc.workspace  = true          # add this line
serde            = { version = "1", features = ["derive"] }
serde_json       = "1"
tempfile         = "3"
```

- [ ] **Step 8: Add gate vector runners to `conformance/src/lib.rs`**

Add these two functions at the bottom of `conformance/src/lib.rs`:

```rust
// ---------------------------------------------------------------------------
// Gate codec conformance
// ---------------------------------------------------------------------------

/// Parsed form of `expected_parse.json` for gate codec vectors.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
pub struct ExpectedGateParse {
    /// Decoded instruction list the codec must produce.
    pub instructions: Vec<ParsedGateInstruction>,
}

/// One instruction in an `expected_parse.json`.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
pub struct ParsedGateInstruction {
    pub mnemonic: String,
    /// Raw operand values (qubit/cbit as u8, angles as u32).
    pub operands: Vec<u64>,
}

impl From<xqgbc::GateInstruction> for ParsedGateInstruction {
    fn from(instr: xqgbc::GateInstruction) -> Self {
        use xqgbc::GateInstruction::*;
        let (mnemonic, operands): (&str, Vec<u64>) = match instr {
            QNop                                   => ("QNOP",    vec![]),
            QHalt                                  => ("QHALT",   vec![]),
            Barrier                                => ("BARRIER", vec![]),
            Meas  { q, c }                         => ("MEAS",    vec![q.0 as u64, c.0 as u64]),
            H     { q }                            => ("H",       vec![q.0 as u64]),
            X     { q }                            => ("X",       vec![q.0 as u64]),
            Y     { q }                            => ("Y",       vec![q.0 as u64]),
            Z     { q }                            => ("Z",       vec![q.0 as u64]),
            S     { q }                            => ("S",       vec![q.0 as u64]),
            Sdg   { q }                            => ("SDG",     vec![q.0 as u64]),
            T     { q }                            => ("T",       vec![q.0 as u64]),
            Tdg   { q }                            => ("TDG",     vec![q.0 as u64]),
            Sx    { q }                            => ("SX",      vec![q.0 as u64]),
            Rx    { q, theta }                     => ("RX",      vec![q.0 as u64, theta.0 as u64]),
            Ry    { q, theta }                     => ("RY",      vec![q.0 as u64, theta.0 as u64]),
            Rz    { q, theta }                     => ("RZ",      vec![q.0 as u64, theta.0 as u64]),
            P     { q, lambda }                    => ("P",       vec![q.0 as u64, lambda.0 as u64]),
            U     { q, theta, phi, lam }           => ("U",       vec![q.0 as u64, theta.0 as u64, phi.0 as u64, lam.0 as u64]),
            Cnot  { ctrl, tgt }                    => ("CNOT",    vec![ctrl.0 as u64, tgt.0 as u64]),
            Cz    { ctrl, tgt }                    => ("CZ",      vec![ctrl.0 as u64, tgt.0 as u64]),
            Swap  { q0, q1 }                       => ("SWAP",    vec![q0.0 as u64, q1.0 as u64]),
            ISwap { q0, q1 }                       => ("ISWAP",   vec![q0.0 as u64, q1.0 as u64]),
            Cp    { ctrl, tgt, lambda }            => ("CP",      vec![ctrl.0 as u64, tgt.0 as u64, lambda.0 as u64]),
            Rzz   { q0, q1, theta }                => ("RZZ",     vec![q0.0 as u64, q1.0 as u64, theta.0 as u64]),
            Crx   { ctrl, tgt, theta }             => ("CRX",     vec![ctrl.0 as u64, tgt.0 as u64, theta.0 as u64]),
        };
        Self { mnemonic: mnemonic.to_owned(), operands }
    }
}

/// Run a gate codec vector through the Rust decoder.
///
/// Reads `circuit.xqg` and `expected_parse.json` from the vector dir,
/// decodes the binary with `xqgbc`, converts to `ParsedGateInstruction`
/// list, and compares to expected.
///
/// # Errors
/// Returns a human-readable error message on any mismatch.
pub fn run_rust_gate_codec(category: &str, name: &str) -> Result<(), String> {
    let dir = conformance_root().join("vectors").join(category).join(name);
    let bytes = fs::read(dir.join("circuit.xqg"))
        .map_err(|e| format!("read circuit.xqg: {e}"))?;
    let expected_json = fs::read_to_string(dir.join("expected_parse.json"))
        .map_err(|e| format!("read expected_parse.json: {e}"))?;

    let circuit = xqgbc::GateCircuit::decode(&bytes)
        .map_err(|e| format!("xqgbc::GateCircuit::decode failed: {e:?}"))?;
    let actual: Vec<ParsedGateInstruction> = circuit
        .instructions
        .into_iter()
        .map(ParsedGateInstruction::from)
        .collect();

    let expected: Vec<ParsedGateInstruction> = serde_json::from_str(&expected_json)
        .map_err(|e| format!("parse expected_parse.json: {e}"))?;

    if actual != expected {
        return Err(format!(
            "gate codec mismatch for {category}/{name}:\n  expected: {expected:?}\n  actual:   {actual:?}"
        ));
    }
    Ok(())
}

/// Run a gate codec vector through the Python decoder.
///
/// Shells out to `uv run python -m xqgb decode <circuit.xqg>` which must
/// print a JSON array of `{mnemonic, operands}` objects to stdout.
///
/// # Errors
/// Returns a human-readable error message on any mismatch.
pub fn run_python_gate_codec(category: &str, name: &str) -> Result<(), String> {
    let dir = conformance_root().join("vectors").join(category).join(name);
    let expected_json = fs::read_to_string(dir.join("expected_parse.json"))
        .map_err(|e| format!("read expected_parse.json: {e}"))?;
    let expected: Vec<ParsedGateInstruction> = serde_json::from_str(&expected_json)
        .map_err(|e| format!("parse expected_parse.json: {e}"))?;

    let runner = std::env::var("XQUAD_CONFORMANCE_PYTHON").unwrap_or_else(|_| "uv".into());
    let output = Command::new(&runner)
        .args(["run", "--no-sync", "python", "-m", "xqgb", "decode"])
        .arg(dir.join("circuit.xqg"))
        .current_dir(conformance_root().join(".."))
        .output()
        .map_err(|e| format!("failed to spawn `{runner} run python -m xqgb decode`: {e}"))?;

    if !output.status.success() {
        return Err(format!(
            "`xqgb decode` exited {}: stderr: {}",
            output.status,
            String::from_utf8_lossy(&output.stderr)
        ));
    }

    let stdout = std::str::from_utf8(&output.stdout)
        .map_err(|e| format!("python stdout not UTF-8: {e}"))?;
    let actual: Vec<ParsedGateInstruction> = serde_json::from_str(stdout.trim())
        .map_err(|e| format!("parse python stdout as JSON: {e}\n{stdout}"))?;

    if actual != expected {
        return Err(format!(
            "gate codec mismatch for {category}/{name}:\n  expected: {expected:?}\n  actual:   {actual:?}"
        ));
    }
    Ok(())
}

/// End-to-end gate codec check. Panics with a descriptive message on failure.
///
/// # Panics
/// Panics when the chosen runtime fails or the parse diverges from `expected_parse.json`.
#[expect(clippy::panic, reason = "test-harness entry point")]
pub fn check_gate_codec_vector(category: &str, name: &str, imp: Impl) {
    let result = match imp {
        Impl::Rust   => run_rust_gate_codec(category, name),
        Impl::Python => run_python_gate_codec(category, name),
    };
    result.unwrap_or_else(|e| panic!("[{imp:?}] {category}/{name}: {e}"));
}
```

- [ ] **Step 9: Add `xqgb decode` CLI entry point to `xqgb/__main__.py`**

The Python gate runner above calls `python -m xqgb decode <path>`. Add this module:

```python
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Minimal CLI for conformance harness: `python -m xqgb decode <path.xqg>`."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from xqgb.decoder import decode_xqg


def main() -> None:
    if len(sys.argv) < 3 or sys.argv[1] != "decode":
        print(
            "Usage: python -m xqgb decode <path.xqg>",
            file=sys.stderr,
        )
        sys.exit(2)

    data = Path(sys.argv[2]).read_bytes()
    circuit = decode_xqg(data)
    result = [
        {"mnemonic": instr.mnemonic, "operands": list(instr.operands)}
        for instr in circuit.instructions
    ]
    print(json.dumps(result))


if __name__ == "__main__":
    main()
```

- [ ] **Step 10: Extend `conformance/build.rs` to generate gate vector tests**

In `build.rs`, the `discover_vectors` function already scans `vectors/<category>/<name>/`. Gate codec vectors are discovered automatically because they live under `vectors/gate/`. Add a secondary discovery pass for the `expected_parse.json` sentinel:

```rust
// In discover_vectors(), change the required-files check to accept either
// the classic triple OR a gate codec vector:
if (dir.join("program.xqasm").exists()
    && dir.join("inputs.json").exists()
    && dir.join("expected.json").exists())
    || (dir.join("circuit.xqg").exists()
        && dir.join("expected_parse.json").exists())
{
    out.push((category.clone(), name));
}
```

And in `write_test_file`, dispatch on the vector kind:

```rust
// Replace the single call to xquad_conformance::check_vector with:
writeln!(
    body,
    "#[test]\nfn {fn_name}() {{\n    xquad_conformance::check_any_vector(\"{category}\", \"{name}\", xquad_conformance::Impl::{imp});\n}}\n"
)
```

Add `check_any_vector` to `lib.rs`:

```rust
/// Dispatch to the correct check function based on which files exist.
/// Classic vectors (program.xqasm + expected.json) use check_vector.
/// Gate codec vectors (circuit.xqg + expected_parse.json) use check_gate_codec_vector.
#[expect(clippy::panic, reason = "test-harness entry point")]
pub fn check_any_vector(category: &str, name: &str, imp: Impl) {
    let dir = conformance_root().join("vectors").join(category).join(name);
    if dir.join("circuit.xqg").exists() {
        check_gate_codec_vector(category, name, imp);
    } else {
        check_vector(category, name, imp);
    }
}
```

- [ ] **Step 11: Run the full conformance suite**

```bash
cargo test -p xquad-conformance
```

Expected: all prior vectors still pass, plus new `vector_gate_codec_*` tests appear and pass for both Rust and Python.

- [ ] **Step 12: Add parity check to CI**

In `.github/workflows/` (whichever workflow runs Python checks), add:

```yaml
- name: Gate opcode parity
  run: uv run python scripts/check-gate-opcode-parity.py
```

- [ ] **Step 13: Commit**

```bash
git add \
  conformance/Cargo.toml \
  conformance/src/lib.rs \
  conformance/build.rs \
  xqgb/__main__.py \
  scripts/check-gate-opcode-parity.py \
  scripts/gen-gate-vectors.py
git commit -m "feat(conformance): gate ISA parity check + codec conformance harness"
```

---

## Verification

### End-to-end (CPU sim — no cloud credentials needed)

```python
import math, struct, zlib
from xqgb import decode_xqg, build_gate_solver

# Build a 2-qubit Bell circuit .xqg in memory
# Note: rotation gates (RX, RY, RZ, P, U, CP, RZZ, CRX) encode angles as
# u32 binary-angular units (full turn = 2^32). Helper for those:
#   def encode_angle(r): return struct.pack(">I", round(r % (2*math.pi) / (2*math.pi) * 4_294_967_296) & 0xFFFFFFFF)
# Bell circuit has no rotation gates, so no angle encoding needed here.

stream = (
    b"\x90\x00"          # H q=0
    b"\xB0\x00\x01"      # CNOT ctrl=0 tgt=1
    b"\x83\x00\x00"      # MEAS q=0 c=0
    b"\x83\x01\x01"      # MEAS q=1 c=1
    b"\x81"              # QHALT
)
crc = zlib.crc32(stream) & 0xFFFFFFFF
header = b"XQGB" + bytes([1, 2, 2]) + struct.pack(">I", 1024) + struct.pack(">I", crc)
data = header + stream

circuit = decode_xqg(data)
solver = build_gate_solver("cpu-sim", seed=42)
result = solver.run(circuit)

print(result.counts)          # e.g. {"00": 510, "11": 514}
assert set(result.counts) <= {"00", "11"}
assert result.total_shots == 1024
print("Bell state verification passed.")
```

### Rust roundtrip

```bash
cargo test -p xqgbc -- --nocapture
```

### IBM Aer (requires `pip install xqgb[ibm]`)

```python
from xqgb import decode_xqg, build_gate_solver
# same data bytes as above
circuit = decode_xqg(data)
solver = build_gate_solver("ibm-aer")
result = solver.run(circuit)
print(result.counts)
assert set(result.counts) <= {"00", "11"}
```

### IonQ sim (requires `pip install xqgb[ionq]` and `IONQ_API_KEY`)

```python
import os
os.environ["IONQ_API_KEY"] = "your-key-here"
solver = build_gate_solver("ionq-sim")
result = solver.run(circuit)
print(result.counts)
```

---

## W5 — Missing Gate Primitives: `RESET`, `CCX`, `CSWAP`

**Background:** Three standard OpenQASM 3 primitives are absent from the v1 gate table:
`reset` (qubit mid-circuit reset to |0⟩), `ccx` (Toffoli / doubly-controlled-X), and
`cswap` (Fredkin / controlled-SWAP). Both IBM and IonQ support all three natively.
Without `RESET`, qubit reuse in error correction is impossible. Without `CCX`/`CSWAP`,
classical-reversible sub-circuits require decomposition into 1- and 2-qubit gates,
inflating circuit depth unnecessarily.

Partial `BARRIER` with a qubit list is deferred — it requires variable-length operands,
which would break the codec invariant that the opcode byte alone determines operand width.
The global `BARRIER` (0x82) remains sufficient.

**Opcode placement:**

| Byte | Mnemonic | Operands | Group |
|---|---|---|---|
| `0x88` | `RESET` | `q: Qubit` | Circuit control (gap after CF opcodes) |
| `0xB4` | `CCX` | `ctrl0: Qubit, ctrl1: Qubit, tgt: Qubit` | Three-qubit fixed gates |
| `0xB5` | `CSWAP` | `ctrl: Qubit, q0: Qubit, q1: Qubit` | Three-qubit fixed gates |

`0xB4–0xBF` extends the existing two-qubit fixed-gate group (`0xB0–0xB3`).

**Files:**
- Modify: `conformance/gate_opcodes.yaml` — 3 new entries
- Modify: `xqgbc/src/table.rs` — 3 entries in `gate_opcodes!`
- Modify: `xqgbc/src/types/opcode.rs` — 3 variants
- Modify: `xqgbc/src/types/instruction.rs` — 3 variants + `opcode()`/`mnemonic()` arms
- Modify: `xqgbc/src/codec.rs` — encode/decode arms
- Modify: `xqgbc/src/verifier.rs` — extend `check_qubits` for 3-qubit instructions
- Modify: `xqgb/decoder.py` — 3 entries in `_OPCODE_TABLE` (`"qqq"` / `"qq"` formats)
- Modify: `xqgb/cpu_sim.py` — `RESET` collapses statevector to |0⟩ on qubit q; `CCX`
  and `CSWAP` applied via `_apply_three()` using 8×8 unitaries
- Modify: `xqgb/transpiler.py` — add `QiskitTranspiler` and `IonQTranspiler` mappings

**Interfaces:**
- Consumes: W1 (xqgbc), W2–W3 (xqgb), W4 (conformance)
- Produces: `RESET`/`CCX`/`CSWAP` in Rust codec and Python decoder; CPU sim `_apply_three()`
  helper for 8×8 unitaries; parity check updated to 32 opcodes

---

- [ ] **Step 1: Update `conformance/gate_opcodes.yaml`**

```yaml
  - code: 0x88
    mnemonic: RESET
    operands:
      - {name: q, type: qubit, width: 1}

  - code: 0xB4
    mnemonic: CCX
    operands:
      - {name: ctrl0, type: qubit, width: 1}
      - {name: ctrl1, type: qubit, width: 1}
      - {name: tgt,   type: qubit, width: 1}

  - code: 0xB5
    mnemonic: CSWAP
    operands:
      - {name: ctrl, type: qubit, width: 1}
      - {name: q0,   type: qubit, width: 1}
      - {name: q1,   type: qubit, width: 1}
```

- [ ] **Step 2: Add entries to `xqgbc/src/table.rs`**

After `Barrier`/`Meas` group, add `RESET` in the circuit-control section; add `CCX`/`CSWAP` after `ISwap` in the two-qubit fixed group:

```rust
            /// Reset qubit to |0⟩ (mid-circuit).
            #[gate(0x88, "RESET", 1)]
            Reset { q: $crate::Qubit },

            // (in two-qubit fixed group, after ISwap)
            /// Toffoli (doubly-controlled-X) gate.
            #[gate(0xB4, "CCX", 3)]
            Ccx { ctrl0: $crate::Qubit, ctrl1: $crate::Qubit, tgt: $crate::Qubit },

            /// Fredkin (controlled-SWAP) gate.
            #[gate(0xB5, "CSWAP", 3)]
            Cswap { ctrl: $crate::Qubit, q0: $crate::Qubit, q1: $crate::Qubit },
```

- [ ] **Step 3: Extend `opcode.rs`, `instruction.rs`, `codec.rs`** following the same
pattern as existing single- and two-qubit gates. `check_qubits` in `verifier.rs` gains
two new arms returning `&[ctrl0.0, ctrl1.0, tgt.0]` and `&[ctrl.0, q0.0, q1.0]`.

- [ ] **Step 4: Add `_apply_three` helper to `xqgb/cpu_sim.py`**

```python
def _apply_three(sv, n, q0, q1, q2, u):
    """Apply 8x8 unitary to (q0, q1, q2) in an n-qubit statevector."""
    sv = sv.reshape([2] * n)
    axes = [q0, q1, q2] + [i for i in range(n) if i not in (q0, q1, q2)]
    sv = sv.transpose(axes).reshape(8, -1)
    sv = u @ sv
    sv = sv.reshape([2, 2, 2] + [2] * (n - 3))
    inv_axes = [0] * n
    for new, old in enumerate(axes):
        inv_axes[old] = new
    return sv.transpose(inv_axes).reshape(-1)
```

`RESET` collapses the statevector: project onto `q == 0`, renormalise.

```python
if m == "RESET":
    q = int(ops[0])
    sv = sv.reshape([2] * n_qubits)
    # Zero out all amplitudes where qubit q == 1
    idx = [slice(None)] * n_qubits
    idx[q] = 1
    sv[tuple(idx)] = 0.0
    sv = sv.reshape(-1)
    norm = np.sqrt((sv * sv.conj()).real.sum())
    if norm > 0:
        sv /= norm
    pos += 1
    continue
```

- [ ] **Step 5: Run tests and parity check**

```bash
cargo test -p xqgbc
uv run pytest xqgb/ -v
uv run python scripts/check-gate-opcode-parity.py
# Expected: OK — 32 gate opcodes match
```

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(xqgbc,xqgb): RESET, CCX (Toffoli), CSWAP (Fredkin) gate primitives"
```

---

## W6 · Part 2 — Wide-Form CF Jumps

**Background:** `CF_JZ`/`CF_JNZ`/`CF_JUMP` use `u8` label IDs (max 255 distinct labels
per circuit). Circuits compiled from unrolled `for` loops or deep conditional chains can
exceed this limit. Wide-form counterparts with `u16` IDs follow the `Jump1`/`Jump2`
precedent from the QUBO ISA exactly, and require no header changes — the 15-byte header
is kept intact, matching the QUBO format's unchanging 15-byte structure.

The header CRC remains stream-only (consistent with the QUBO format's design: header
fields are validated structurally by the verifier, not by the checksum). No feature-flags
byte is added; format evolution is handled by version bumps.

**Wide-form CF opcodes (in gap `0x8A–0x8C`):**

| Byte | Mnemonic | Operands |
|---|---|---|
| `0x8A` | `CF_JUMP2` | `id: u16` |
| `0x8B` | `CF_JZ2` | `c: Cbit, id: u16` |
| `0x8C` | `CF_JNZ2` | `c: Cbit, id: u16` |

**Files:**
- Modify: `conformance/gate_opcodes.yaml` — 3 new CF wide-form entries (type `label16`, width 2)
- Modify: `xqgbc/src/table.rs` — 3 new CF wide-form entries in `gate_opcodes!`
- Modify: `xqgbc/src/types/opcode.rs` — 3 new variants
- Modify: `xqgbc/src/types/instruction.rs` — 3 new variants
- Modify: `xqgbc/src/codec.rs` — encode/decode arms (`u16` already has `EncodeOperand`/`DecodeOperand`)
- Modify: `xqgbc/src/verifier.rs` — wide-form jumps participate in label-consistency check
- Modify: `xqgb/decoder.py` — `_OPCODE_TABLE` entries; `'L'` format char for `u16` labels
- Modify: `xqgb/cpu_sim.py` — handle `CF_JUMP2`/`CF_JZ2`/`CF_JNZ2` in dispatch loop

**Interfaces:**
- Consumes: W1, W4, W6 Part 1
- Produces: 3 wide-form CF opcodes; `'L'` format char in `_OPCODE_TABLE` decoder

---

- [ ] **Step 1: Add 3 entries to `conformance/gate_opcodes.yaml`**

```yaml
  - code: 0x8A
    mnemonic: CF_JUMP2
    operands:
      - {name: id, type: label16, width: 2}

  - code: 0x8B
    mnemonic: CF_JZ2
    operands:
      - {name: c,  type: cbit,    width: 1}
      - {name: id, type: label16, width: 2}

  - code: 0x8C
    mnemonic: CF_JNZ2
    operands:
      - {name: c,  type: cbit,    width: 1}
      - {name: id, type: label16, width: 2}
```

Also add `label16` to the type maps in `check-gate-opcode-parity.py`.

- [ ] **Step 2: Add entries to `xqgbc/src/table.rs`**

```rust
            #[gate(0x8A, "CF_JUMP2", 0)]
            CfJump2 { id: u16 },
            #[gate(0x8B, "CF_JZ2", 0)]
            CfJz2 { c: $crate::Cbit, id: u16 },
            #[gate(0x8C, "CF_JNZ2", 0)]
            CfJnz2 { c: $crate::Cbit, id: u16 },
```

- [ ] **Step 3: Extend `xqgbc/src/verifier.rs`** — the label-consistency check already
uses pattern matching; extend all three `match` arms to also cover the `2` variants:

```rust
            let referenced = match instr {
                GateInstruction::CfJump  { id }
                | GateInstruction::CfJump2 { id }
                | GateInstruction::CfJz   { id, .. }
                | GateInstruction::CfJz2  { id, .. }
                | GateInstruction::CfJnz  { id, .. }
                | GateInstruction::CfJnz2 { id, .. } => Some(*id as u16),
                _ => None,
            };
```

Note: `CF_TARGET` label IDs remain `u8` (the target declaration), but `CF_JUMP2` can
reference any `u16`. This means a `CF_JUMP2` referencing id > 255 will always fail the
consistency check — which is correct since no `CF_TARGET` can have id > 255. Wide-form
is useful only when you have many distinct labels, not larger individual label IDs. If
label IDs > 255 are needed, `CF_TARGET2` would be required (deferred).

- [ ] **Step 4: Update `xqgb/decoder.py`** — add `'L'` format character for `u16`
label operands and add 3 opcode entries:

```python
elif char == "L":   # u16 label (wide-form CF)
    if pos + consumed + 2 > len(stream):
        raise ValueError(f"Stream truncated reading u16 label at offset {pos + consumed}")
    (val,) = struct.unpack_from(">H", stream, pos + consumed)
    values.append(val)
    consumed += 2
```

```python
    0x8A: ("CF_JUMP2", "L"),
    0x8B: ("CF_JZ2",   "cL"),
    0x8C: ("CF_JNZ2",  "cL"),
```

- [ ] **Step 5: Extend `xqgb/cpu_sim.py`** — in the CF dispatch block:

```python
if m in ("CF_JUMP2", "CF_JUMP"):
    pos = _find_label(instructions, ops[0])
    continue
if m in ("CF_JZ2", "CF_JZ"):
    pos = _find_label(instructions, ops[1]) if cbits[ops[0]] == 0 else pos + 1
    continue
if m in ("CF_JNZ2", "CF_JNZ"):
    pos = _find_label(instructions, ops[1]) if cbits[ops[0]] != 0 else pos + 1
    continue
```

- [ ] **Step 6: Write tests** — encode a circuit with 260 `CF_TARGET` + `CF_JUMP2`
labels (impossible with `u8`), round-trip through Rust codec, verify Python decoder
reconstructs all jump targets correctly.

- [ ] **Step 7: Run suite and parity check**

```bash
cargo test -p xqgbc && uv run pytest xqgb/ -v
uv run python scripts/check-gate-opcode-parity.py
# Expected: OK — 35 gate opcodes match (32 from W5 + 3 wide-form CF)
```

- [ ] **Step 8: Commit**

```bash
git commit -m "feat(xqgbc,xqgb): wide-form CF_JUMP2/JZ2/JNZ2 with u16 label IDs"
```

---

## W7 — Parameterized Circuits

**Background:** VQE, QAOA, and similar variational algorithms execute the same circuit
structure hundreds or thousands of times with different rotation angles. Currently every
parameter sweep requires re-encoding the full `.xqg` file.

**Design:** Format version 2 adds a parameter preamble immediately after the 15-byte
header. The header itself is unchanged (same magic, same field layout as v1). The version
byte distinguishes the two:

```
v1 file (15 bytes header, then instruction stream):
  [15-byte XQGB header, version=1] [instruction stream]

v2 file (15 bytes header + parameter preamble, then instruction stream):
  [15-byte XQGB header, version=2] [n_params: u8] [u32 BE × n_params] [instruction stream]
```

The CRC in the header covers the stream bytes only (i.e. the instruction stream excluding
the parameter preamble). The parameter preamble is validated structurally: the decoder
reads `n_params` and then exactly `4 × n_params` bytes before handing the stream to the
instruction decoder.

Angle operands that reference a parameter slot use the high bit of their `u32` value as
a flag: bit 31 = 0 → literal binary-angular value; bit 31 = 1 → parameter-slot reference
(bits 0–7 = slot index). This is backward-compatible: v1 decoders that encounter a
bit-31-set angle operand will interpret it as an astronomically large rotation angle —
incorrect but not a crash. Correct decoding requires version = 2.

**Files:**
- Modify: `xqgbc/src/program.rs` — add `params: Vec<GateAngle>` to `GateCircuit`;
  encode v2 preamble when `params` is non-empty; decode v2 preamble when `version == 2`;
  add `GateAngle::is_param_ref()`, `GateAngle::param_slot()`, `GateAngle::from_param_slot()`
- Modify: `xqgbc/src/verifier.rs` — every `is_param_ref()` angle must have slot index `< n_params`
- Modify: `xqgb/decoder.py` — read v2 preamble when `data[4] == 2`; expose `circuit.params`;
  add `circuit.bind(values: list[float]) -> GateCircuit`
- Modify: `xqgb/solver.py` — `GateSolver.run()` gains optional `params: list[float]` kwarg

**Interfaces:**
- Consumes: W1 (codec, program format); W6 Part 2 (no dependency — header unchanged)
- Produces: `GateCircuit.params`, `GateCircuit.bind()`, `GateSolver.run(params=...)`

---

- [ ] **Step 1: Add `is_param_ref` and `param_slot` to `GateAngle`** in
`xqgbc/src/types/operand.rs`:

```rust
impl GateAngle {
    /// True when this value encodes a parameter-slot reference (bit 31 set).
    pub fn is_param_ref(self) -> bool { self.0 & 0x8000_0000 != 0 }

    /// The slot index when `is_param_ref()` is true.
    pub fn param_slot(self) -> u8 { (self.0 & 0xFF) as u8 }

    /// Construct a parameter-slot reference.
    pub fn from_param_slot(slot: u8) -> Self { Self(0x8000_0000 | slot as u32) }
}
```

- [ ] **Step 2: Extend `GateCircuit::encode()`** — if `self.params` is non-empty, set
version byte = 2 and emit preamble (`n_params: u8`, then each param as `u32 BE`) before
the instruction stream. CRC is still computed over the instruction stream bytes only.

`GateCircuit::decode()` branches on `bytes[4]`: 1 → stream starts at byte 15; 2 → read
preamble starting at byte 15, stream starts at `15 + 1 + 4 * n_params`.

- [ ] **Step 3: Extend `xqgbc/src/verifier.rs`** — after the qubit/cbit bounds check
loop, add a second pass: every angle operand with `is_param_ref()` set must have
`param_slot() < circuit.params().len()`.

- [ ] **Step 4: Implement `bind()` in `xqgb/decoder.py`**

```python
def bind(self, values: list[float]) -> "GateCircuit":
    """Return a new GateCircuit with all parameter references replaced by concrete angles."""
    _PARAM_FLAG = 0x8000_0000
    def resolve(op):
        if isinstance(op, int) and op & _PARAM_FLAG:
            slot = op & 0xFF
            return round(values[slot] % (2 * math.pi) / (2 * math.pi) * 4_294_967_296) & 0xFFFFFFFF
        return op
    new_instrs = [
        RawGateInstruction(i.mnemonic, tuple(resolve(o) for o in i.operands))
        for i in self.instructions
    ]
    return GateCircuit(header=self.header, params=[], instructions=new_instrs)
```

- [ ] **Step 5: Write tests** — a v2 parameterized `RX` circuit with `θ = slot 0`,
default 0. Encode, round-trip decode, bind `θ = π`, assert same distribution as literal
`RX(π)`. Also: v1 files still decode identically.

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(xqgbc,xqgb): v2 format — parameterized circuits with preamble parameter table"
```

---

## W6 · Part 3 — Classical Integer Registers

**Background:** The CF opcodes (`CF_JZ`/`CF_JNZ`) can only branch on a single `Cbit`
(one measurement bit). OpenQASM 3 also supports `int` classical registers that accumulate
values, support arithmetic, and can be compared in multi-bit conditions
(`if (syndrome == 3)`). Without this, multi-qubit error syndrome decoding must happen
off-chip.

**Design:** A classical register file of 8 `u16` slots (`CR0`–`CR7`). Format version 3
adds an `n_cregs: u8` preamble byte immediately after the parameter preamble (or directly
after the 15-byte header if no parameter preamble is present). The 15-byte header is
unchanged; the version byte (= 3) signals the preamble. New opcode group `0xE0–0xEF`:

| Byte | Mnemonic | Operands | Description |
|---|---|---|---|
| `0xE0` | `CSET` | `reg: u8, val: u8` | Load 8-bit immediate into classical register |
| `0xE1` | `CADD` | `dst: u8, src: u8` | `CR[dst] += CR[src]` |
| `0xE2` | `CSUB` | `dst: u8, src: u8` | `CR[dst] -= CR[src]` |
| `0xE3` | `CINC` | `reg: u8` | `CR[reg] += 1` |
| `0xE4` | `CDEC` | `reg: u8` | `CR[reg] -= 1` |
| `0xE5` | `CMBIT` | `reg: u8, c: Cbit` | Copy `Cbit` value into `CR[reg]` bit 0 |
| `0xE6` | `CJEQ` | `r0: u8, r1: u8, id: u8` | Jump to label if `CR[r0] == CR[r1]` |
| `0xE7` | `CJLT` | `r0: u8, r1: u8, id: u8` | Jump to label if `CR[r0] < CR[r1]` |
| `0xE8` | `CJGT` | `r0: u8, r1: u8, id: u8` | Jump to label if `CR[r0] > CR[r1]` |

`CF_LOOP`/`CF_NEXT` (deliberately omitted from W6 Part 1) can now be expressed as
`CSET` + `CF_TARGET` + (body) + `CDEC` + `CJGT reg 0 label`, which is explicit and
auditable.

`CMBIT` bridges the Cbit/classical-register boundary: after a MEAS, copy the result
into a register accumulator for syndrome arithmetic.

**Files:**
- Modify: `conformance/gate_opcodes.yaml` — 9 new entries (type `creg`, width 1)
- Modify: `xqgbc/src/table.rs`, `opcode.rs`, `instruction.rs`, `codec.rs` — 9 new variants
- Modify: `xqgbc/src/verifier.rs` — `CJEQ`/`CJLT`/`CJGT` participate in label-consistency
  check; `CSET`/`CMBIT` register-index bounds check (`< n_cregs`)
- Modify: `xqgb/decoder.py` — 9 entries with `'r'` format char for `creg` operands
- Modify: `xqgb/cpu_sim.py` — add `cregs = [0] * 8` to `simulate()`; dispatch all `C*`
  opcodes; `CJEQ`/`CJLT`/`CJGT` call `_find_label`
- No transpiler changes — backends receive the classical registers as opaque; IBM and IonQ
  do not support this natively and `QiskitTranspiler`/`IonQTranspiler` should raise
  `ValueError` on any `0xE*` opcode

**Interfaces:**
- Consumes: W6 Part 1 (CF label infrastructure), W6 Part 2 (header v2 `flags`/`n_cregs`)
- Produces: 8-slot u16 classical register file; arithmetic/comparison opcodes; loop
  counter idiom via `CSET`+`CDEC`+`CJGT`; `CMBIT` bridge from Cbit to register

---

- [ ] **Step 1: Implement version 3 preamble** — `GateCircuit::encode()` emits version = 3
  when `n_cregs > 0`. The preamble layout after the 15-byte header: optional param block
  (if `n_params > 0`), then `n_cregs: u8`. `GateCircuit::decode()` branches on version:
  1 → stream only; 2 → param preamble + stream; 3 → param preamble + `n_cregs` byte + stream.

- [ ] **Step 2: Add 9 entries to `xqgbc/src/table.rs`** in a new `0xE0–0xE8` group.
  `u8` operands already have `EncodeOperand`/`DecodeOperand`; no new types needed.

- [ ] **Step 3: Extend `xqgbc/src/verifier.rs`**
  - `CSET`/`CMBIT`/`CINC`/`CDEC`/`CADD`/`CSUB`: check all `reg` operands `< n_cregs`
  - `CJEQ`/`CJLT`/`CJGT`: check reg operands + check `id` is in `declared_labels`

- [ ] **Step 4: Extend `xqgb/cpu_sim.py` `simulate()` loop**

```python
cregs = [0] * 8   # u16 classical registers

# In dispatch:
if m == "CSET":  cregs[ops[0]] = ops[1]; pos += 1; continue
if m == "CADD":  cregs[ops[0]] = (cregs[ops[0]] + cregs[ops[1]]) & 0xFFFF; pos += 1; continue
if m == "CSUB":  cregs[ops[0]] = (cregs[ops[0]] - cregs[ops[1]]) & 0xFFFF; pos += 1; continue
if m == "CINC":  cregs[ops[0]] = (cregs[ops[0]] + 1) & 0xFFFF; pos += 1; continue
if m == "CDEC":  cregs[ops[0]] = (cregs[ops[0]] - 1) & 0xFFFF; pos += 1; continue
if m == "CMBIT": cregs[ops[0]] = cbits[ops[1]]; pos += 1; continue
if m == "CJEQ":  pos = _find_label(instructions, ops[2]) if cregs[ops[0]] == cregs[ops[1]] else pos + 1; continue
if m == "CJLT":  pos = _find_label(instructions, ops[2]) if cregs[ops[0]] <  cregs[ops[1]] else pos + 1; continue
if m == "CJGT":  pos = _find_label(instructions, ops[2]) if cregs[ops[0]] >  cregs[ops[1]] else pos + 1; continue
```

- [ ] **Step 5: Write tests** — a syndrome accumulation circuit: measure 3 qubits into
  cbits, use `CMBIT` to accumulate into `CR0`, branch on `CJEQ CR0 CR1` to apply a
  correction gate. Verify CPU sim produces the expected counts.

- [ ] **Step 6: Run full suite**

```bash
cargo test -p xqgbc && uv run pytest xqgb/ -v
uv run python scripts/check-gate-opcode-parity.py
# Expected: OK — 44 gate opcodes match
```

- [ ] **Step 7: Commit**

```bash
git commit -m "feat(xqgbc,xqgb): classical integer register file (CSET/CADD/CINC/CMBIT/CJEQ…)"
```

---

## W6 · Part 1 — Classical Control Flow: `if`/`else` via Flat Jumps

**Background:** OpenQASM 3 adds classical control flow that conditions gate application on
`bit` register values (measurement results). The only construct requiring genuine runtime
hardware support is `if`/`else` — `for` and `while` are always unrolled by the compiler
before encoding, and `break`/`continue` resolve to unconditional jumps within the
unrolled body.

**Design:** Four opcodes in the gap `0x84–0x87`, mirroring the `TARGET`/`JUMP1`/`JUMPI1`
pattern from the QUBO ISA. `CF_TARGET` is a passive label marker — the verifier collects
all declared IDs and checks that every jump references one, exactly as `TARGET` does in
`xqvm`. The executor and transpiler advance past `CF_TARGET` without action.

```
0x84  CF_TARGET  { id: u8 }              — passive jump-destination label
0x85  CF_JUMP    { id: u8 }              — unconditional jump to label
0x86  CF_JZ      { c: Cbit, id: u8 }    — jump to label if cbit c == 0
0x87  CF_JNZ     { c: Cbit, id: u8 }    — jump to label if cbit c == 1
```

How OpenQASM 3 constructs lower to these (compiler responsibility):

```
if (c[0] == 1) { X q[1]; }
→  CF_JZ  c=0 id=1
   X q=1
   CF_TARGET id=1

if (c[0] == 1) { X q[1]; } else { H q[1]; }
→  CF_JZ  c=0 id=1
   X q=1
   CF_JUMP id=2
   CF_TARGET id=1
   H q=1
   CF_TARGET id=2

for i in [0:N] { G; }   →  G · G · … (N copies; unrolled before encoding)
while (c) { … }         →  compile error; no hardware runtime support
break                   →  CF_JUMP <loop_exit_label>  (resolved by compiler)
continue                →  CF_JUMP <loop_top_label>   (resolved by compiler)
```

**Files:**
- Modify: `conformance/gate_opcodes.yaml` — add 4 CF entries
- Modify: `xqgbc/src/table.rs` — add 4 entries to `gate_opcodes!`
- Modify: `xqgbc/src/types/opcode.rs` — add 4 variants to `GateOpcode`
- Modify: `xqgbc/src/types/instruction.rs` — add 4 variants to `GateInstruction`
- Modify: `xqgbc/src/codec.rs` — extend `encode`/`decode` match arms
- Modify: `xqgbc/src/verifier.rs` — add label-consistency pass (mirrors XQVM)
- Modify: `xqgb/decoder.py` — add 4 entries to `_OPCODE_TABLE`
- Modify: `xqgb/cpu_sim.py` — index-based loop + cbit register + forward label scan
- Modify: `xqgb/transpiler.py` — forward-scan body for `QiskitTranspiler`; raise for IonQ

**Interfaces:**
- Consumes: all of W1–W4
- Produces: `CF_TARGET`/`CF_JUMP`/`CF_JZ`/`CF_JNZ` in Rust codec and Python decoder;
  verifier label-consistency check; CPU sim branches on Cbit values; Qiskit transpiler
  collects body by scanning forward to the next CF_TARGET

---

- [ ] **Step 1: Update `conformance/gate_opcodes.yaml`**

Append after the `CRX` entry. Note the new operand type `label` (width 1, encodes as
`u8`); add it to `check-gate-opcode-parity.py`'s `_PY_FORMAT_WIDTHS` and
`_RUST_TYPE_WIDTH` maps as an alias for a raw `u8`:

```yaml
  # -------------------------------------------------------------------------
  # Classical control flow  (0x84–0x87; gap between circuit-control and H)
  # Mirrors TARGET/JUMP1/JUMPI1 from the QUBO ISA.
  # -------------------------------------------------------------------------
  - code: 0x84
    mnemonic: CF_TARGET
    operands:
      - {name: id, type: label, width: 1}

  - code: 0x85
    mnemonic: CF_JUMP
    operands:
      - {name: id, type: label, width: 1}

  - code: 0x86
    mnemonic: CF_JZ
    operands:
      - {name: c,  type: cbit,  width: 1}
      - {name: id, type: label, width: 1}

  - code: 0x87
    mnemonic: CF_JNZ
    operands:
      - {name: c,  type: cbit,  width: 1}
      - {name: id, type: label, width: 1}
```

---

- [ ] **Step 2: Write failing tests in `xqgbc/tests/roundtrip.rs`** (append)

```rust
use xqgbc::{GateInstruction, Cbit, Qubit, codec};
use xqgbc::program::GateCircuit;
use xqgbc::verifier::static_verify;

#[test]
fn roundtrip_cf_target() {
    let instr = GateInstruction::CfTarget { id: 42 };
    let bytes = codec::encode(&instr);
    assert_eq!(bytes, [0x84, 42]);
    let (decoded, consumed) = codec::decode(&bytes).unwrap();
    assert_eq!(decoded, instr);
    assert_eq!(consumed, 2);
}

#[test]
fn roundtrip_cf_jump() {
    let instr = GateInstruction::CfJump { id: 0 };
    let bytes = codec::encode(&instr);
    assert_eq!(bytes, [0x85, 0x00]);
    let (decoded, consumed) = codec::decode(&bytes).unwrap();
    assert_eq!(decoded, instr);
    assert_eq!(consumed, 2);
}

#[test]
fn roundtrip_cf_jz() {
    let instr = GateInstruction::CfJz { c: Cbit(1), id: 7 };
    let bytes = codec::encode(&instr);
    assert_eq!(bytes, [0x86, 0x01, 0x07]);
    let (decoded, consumed) = codec::decode(&bytes).unwrap();
    assert_eq!(decoded, instr);
    assert_eq!(consumed, 3);
}

#[test]
fn roundtrip_cf_jnz() {
    let instr = GateInstruction::CfJnz { c: Cbit(0), id: 3 };
    let bytes = codec::encode(&instr);
    assert_eq!(bytes, [0x87, 0x00, 0x03]);
    let (decoded, consumed) = codec::decode(&bytes).unwrap();
    assert_eq!(decoded, instr);
    assert_eq!(consumed, 3);
}

#[test]
fn verifier_accepts_cf_if_circuit() {
    // if (c0 == 1) { X q1; }
    let circuit = GateCircuit::new(2, 1, 100, vec![
        GateInstruction::Meas     { q: Qubit(0), c: Cbit(0) },
        GateInstruction::CfJz     { c: Cbit(0), id: 1 },
        GateInstruction::X        { q: Qubit(1) },
        GateInstruction::CfTarget { id: 1 },
        GateInstruction::QHalt,
    ]);
    static_verify(&circuit).unwrap();
}

#[test]
fn verifier_rejects_undeclared_label() {
    let circuit = GateCircuit::new(1, 1, 100, vec![
        GateInstruction::Meas  { q: Qubit(0), c: Cbit(0) },
        GateInstruction::CfJz  { c: Cbit(0), id: 99 },  // label 99 never declared
        GateInstruction::QHalt,
    ]);
    assert!(static_verify(&circuit).is_err());
}
```

Run: `cargo test -p xqgbc` → expected: compile error (variants not yet defined).

---

- [ ] **Step 3: Add 4 entries to `xqgbc/src/table.rs`**

Inside `gate_opcodes!`, after the `Meas` entry (`0x83`) and before `H` (`0x90`):

```rust
            // --- Classical control flow (mirrors QUBO ISA TARGET/JUMP/JUMPI) ---
            /// Passive jump-destination label; no runtime effect.
            #[gate(0x84, "CF_TARGET", 0)]
            CfTarget { id: u8 },

            /// Unconditional jump to label.
            #[gate(0x85, "CF_JUMP", 0)]
            CfJump { id: u8 },

            /// Jump to label if classical bit c is 0.
            #[gate(0x86, "CF_JZ", 0)]
            CfJz { c: $crate::Cbit, id: u8 },

            /// Jump to label if classical bit c is 1.
            #[gate(0x87, "CF_JNZ", 0)]
            CfJnz { c: $crate::Cbit, id: u8 },
```

`qubit_arity` is `0` for all four — `check_qubits()` already ignores them via the `_ =>
&[]` fallthrough.

---

- [ ] **Step 4: Extend `xqgbc/src/types/opcode.rs`**

Add to the `GateOpcode` enum:

```rust
    CfTarget = 0x84,
    CfJump   = 0x85,
    CfJz     = 0x86,
    CfJnz    = 0x87,
```

Add to `TryFrom<u8>`:

```rust
    0x84 => Ok(Self::CfTarget),
    0x85 => Ok(Self::CfJump),
    0x86 => Ok(Self::CfJz),
    0x87 => Ok(Self::CfJnz),
```

---

- [ ] **Step 5: Extend `xqgbc/src/types/instruction.rs`**

Add variants to `GateInstruction`:

```rust
    CfTarget { id: u8 },
    CfJump   { id: u8 },
    CfJz     { c: Cbit, id: u8 },
    CfJnz    { c: Cbit, id: u8 },
```

Extend `opcode()`:

```rust
    Self::CfTarget { .. } => GateOpcode::CfTarget,
    Self::CfJump   { .. } => GateOpcode::CfJump,
    Self::CfJz     { .. } => GateOpcode::CfJz,
    Self::CfJnz    { .. } => GateOpcode::CfJnz,
```

Extend `mnemonic()`:

```rust
    GateOpcode::CfTarget => "CF_TARGET",
    GateOpcode::CfJump   => "CF_JUMP",
    GateOpcode::CfJz     => "CF_JZ",
    GateOpcode::CfJnz    => "CF_JNZ",
```

---

- [ ] **Step 6: Extend `xqgbc/src/codec.rs`**

In `encode()`:

```rust
GateInstruction::CfTarget { id }    => { id.encode_into(&mut buf); }
GateInstruction::CfJump   { id }    => { id.encode_into(&mut buf); }
GateInstruction::CfJz  { c, id }    => { c.encode_into(&mut buf); id.encode_into(&mut buf); }
GateInstruction::CfJnz { c, id }    => { c.encode_into(&mut buf); id.encode_into(&mut buf); }
```

In `decode()`:

```rust
GateOpcode::CfTarget => GateInstruction::CfTarget { id: read!(u8) },
GateOpcode::CfJump   => GateInstruction::CfJump   { id: read!(u8) },
GateOpcode::CfJz     => GateInstruction::CfJz  { c: read!(Cbit), id: read!(u8) },
GateOpcode::CfJnz    => GateInstruction::CfJnz { c: read!(Cbit), id: read!(u8) },
```

`u8` already implements `EncodeOperand`/`DecodeOperand`.

---

- [ ] **Step 7: Extend `xqgbc/src/verifier.rs`** — label-consistency pass

Add a second pass after the existing qubit/cbit bounds check. Structure mirrors XQVM's
verifier: collect declared labels first, then verify every jump references one.

```rust
// Collect all declared CF_TARGET label IDs.
let declared_labels: std::collections::HashSet<u8> = instrs
    .iter()
    .filter_map(|i| {
        if let GateInstruction::CfTarget { id } = i { Some(*id) } else { None }
    })
    .collect();

// Every jump must reference a declared CF_TARGET.
for instr in instrs {
    let referenced = match instr {
        GateInstruction::CfJump { id }
        | GateInstruction::CfJz  { id, .. }
        | GateInstruction::CfJnz { id, .. } => Some(*id),
        _ => None,
    };
    if let Some(id) = referenced {
        if !declared_labels.contains(&id) {
            return Err(GateVerifyError::UndeclaredLabel { id });
        }
    }
}
```

Add the new error variant to `GateVerifyError`:

```rust
#[error("jump references undeclared CF_TARGET label id {id}")]
UndeclaredLabel { id: u8 },
```

---

- [ ] **Step 8: Run Rust tests**

```bash
cargo test -p xqgbc
```

Expected: all prior tests pass plus the 6 new CF roundtrip and verifier tests.

---

- [ ] **Step 9: Update `xqgb/decoder.py` `_OPCODE_TABLE`**

Add a `'l'` format character for label/`u8` operands (distinct from `'q'` and `'c'`
semantically; decoded identically as a raw `u8`):

```python
    0x84: ("CF_TARGET", "l"),
    0x85: ("CF_JUMP",   "l"),
    0x86: ("CF_JZ",     "cl"),
    0x87: ("CF_JNZ",    "cl"),
```

Add `'l'` to `_decode_operands`:

```python
        elif char == "l":
            if pos + consumed >= len(stream):
                raise ValueError(
                    f"Truncated label operand for opcode 0x{opcode:02X} "
                    f"at offset {pos + consumed}"
                )
            values.append(stream[pos + consumed])
            consumed += 1
```

Add `'l'` to `_PY_FORMAT_WIDTHS` in `check-gate-opcode-parity.py`:

```python
_PY_FORMAT_WIDTHS = {
    "q": ("qubit", 1), "c": ("cbit", 1), "a": ("angle", 4), "l": ("label", 1)
}
```

Also add `"label"` to `_RUST_TYPE_WIDTH` and `_RUST_TYPE_NAME` (mapped from `u8`):

```python
_RUST_TYPE_WIDTH = {"Qubit": 1, "Cbit": 1, "GateAngle": 4, "u8": 1}
_RUST_TYPE_NAME  = {"Qubit": "qubit", "Cbit": "cbit", "GateAngle": "angle", "u8": "label"}
```

---

- [ ] **Step 10: Update `xqgb/cpu_sim.py`**

Replace the `for instr in instructions` loop in `simulate()` with an index-based loop
that maintains a `cbits` array and branches on label lookup. `CF_TARGET` is a no-op
(executor advances past it, same as XQVM's `TARGET`).

```python
def simulate(self, n_qubits, n_cbits, n_shots, instructions):
    sv = np.zeros(2**n_qubits, dtype=np.complex128)
    sv[0] = 1.0
    cbits = [0] * n_cbits   # updated by MEAS during execution
    meas_map: list[tuple[int, int]] = []

    pos = 0
    while pos < len(instructions):
        instr = instructions[pos]
        m = instr.mnemonic
        ops = instr.operands

        if m in ("QNOP", "BARRIER", "QHALT", "CF_TARGET"):
            pos += 1
            continue
        if m == "MEAS":
            meas_map.append((ops[0], ops[1]))
            pos += 1
            continue
        if m == "CF_JUMP":
            pos = _find_label(instructions, ops[0])
            continue
        if m == "CF_JZ":
            pos = _find_label(instructions, ops[1]) if cbits[ops[0]] == 0 else pos + 1
            continue
        if m == "CF_JNZ":
            pos = _find_label(instructions, ops[1]) if cbits[ops[0]] != 0 else pos + 1
            continue

        sv = self._apply(sv, n_qubits, instr)
        pos += 1

    probs = (sv * sv.conj()).real
    probs = np.maximum(probs, 0.0)
    probs /= probs.sum()
    sampled = self._rng.choice(2**n_qubits, size=n_shots, p=probs)
    return self._build_histogram(sampled, meas_map, n_cbits)
```

Add the label-lookup helper (O(n) scan; acceptable since CPU sim is PoC-only):

```python
def _find_label(instructions: list, label_id: int) -> int:
    """Return the index of CF_TARGET with the given id."""
    for i, instr in enumerate(instructions):
        if instr.mnemonic == "CF_TARGET" and instr.operands[0] == label_id:
            return i
    raise ValueError(f"CF_TARGET id={label_id} not found — verifier should have caught this")
```

---

- [ ] **Step 11: Update `xqgb/transpiler.py`**

In `QiskitTranspiler.transpile()`, handle CF opcodes before the generic `_QISKIT_MAP`
lookup. `CF_TARGET` is skipped (no Qiskit equivalent needed). For `CF_JZ`/`CF_JNZ`,
collect the body by scanning forward to the matching `CF_TARGET`, then emit using
`if_test`:

```python
            if instr.mnemonic == "CF_TARGET":
                continue
            if instr.mnemonic == "CF_JUMP":
                # unconditional jump: only legal as else-skip in compiled if/else;
                # the surrounding if_test context handles this implicitly — skip.
                continue
            if instr.mnemonic in ("CF_JZ", "CF_JNZ"):
                c_idx, label_id = instr.operands[0], instr.operands[1]
                # Collect body: instructions between here and CF_TARGET label_id.
                body, else_body = _collect_cf_body(circuit.instructions, idx, label_id)
                creg = qc.cregs[0]
                condition_val = 0 if instr.mnemonic == "CF_JZ" else 1
                with qc.if_test((creg[c_idx], condition_val)) as else_ctx:
                    for body_instr in body:
                        _emit_single(qc, body_instr)
                    if else_body:
                        with else_ctx:
                            for body_instr in else_body:
                                _emit_single(qc, body_instr)
                # Advance idx past the whole if/else block.
                idx = _find_cf_target(circuit.instructions, label_id)
                continue
```

Add helpers `_collect_cf_body` and `_find_cf_target` as module-level functions. Their
implementation is straightforward forward scans; they are not shown here to keep the plan
concise — implement them alongside the transpiler update.

In `IonQTranspiler.transpile()`, raise on any CF opcode — IonQ requires full unrolling:

```python
            if instr.mnemonic.startswith("CF_"):
                raise ValueError(
                    f"IonQ does not support runtime classical control. "
                    f"Unroll {instr.mnemonic!r} before encoding to .xqg."
                )
```

---

- [ ] **Step 12: Write Python tests** in `xqgb/tests/test_cf.py`

```python
"""Tests for CF_TARGET/CF_JUMP/CF_JZ/CF_JNZ in decoder and CPU sim."""
import struct
import zlib
import pytest
from xqgb.decoder import decode_xqg, GateCircuit, XqgHeader, RawGateInstruction
from xqgb.cpu_sim import SolverCPUSim


def _build_xqg(n_qubits, n_cbits, n_shots, stream):
    crc = zlib.crc32(stream) & 0xFFFFFFFF
    header = (b"XQGB" + bytes([1, n_qubits, n_cbits])
              + struct.pack(">I", n_shots) + struct.pack(">I", crc))
    return header + stream


def _make_circuit(n_qubits, n_cbits, n_shots, instrs):
    return GateCircuit(
        header=XqgHeader(n_qubits=n_qubits, n_cbits=n_cbits, n_shots=n_shots),
        instructions=instrs,
    )


def _I(mnemonic, *operands):
    return RawGateInstruction(mnemonic=mnemonic, operands=operands)


def test_decode_cf_target():
    stream = bytes([0x84, 0x05, 0x81])
    circuit = decode_xqg(_build_xqg(1, 1, 1, stream))
    assert circuit.instructions[0].mnemonic == "CF_TARGET"
    assert circuit.instructions[0].operands == (5,)


def test_decode_cf_jz():
    stream = bytes([0x86, 0x00, 0x01, 0x81])
    circuit = decode_xqg(_build_xqg(1, 1, 1, stream))
    assert circuit.instructions[0].mnemonic == "CF_JZ"
    assert circuit.instructions[0].operands == (0, 1)


def test_cpu_sim_cf_jz_skips_gate_when_cbit_zero():
    """CF_JZ jumps past X when cbit 0 is 0 (never set); qubit stays |0⟩."""
    circuit = _make_circuit(1, 1, 200, [
        _I("CF_JZ",    0, 1),
        _I("X",        0),
        _I("CF_TARGET", 1),
        _I("MEAS",     0, 0),
        _I("QHALT"),
    ])
    result = SolverCPUSim(seed=0).run(circuit)
    assert result.counts == {"0": 200}


def test_cpu_sim_cf_jnz_applies_gate_when_cbit_nonzero():
    """X sets q0 to |1⟩; MEAS records c0=1; CF_JNZ executes the second X → back to |0⟩."""
    circuit = _make_circuit(1, 1, 200, [
        _I("X",         0),
        _I("MEAS",      0, 0),
        _I("CF_JNZ",    0, 1),
        _I("CF_TARGET", 2),       # else branch (skipped)
        _I("MEAS",      0, 0),
        _I("CF_JUMP",   3),
        _I("CF_TARGET", 1),       # then branch
        _I("X",         0),       # flip back to |0⟩
        _I("CF_TARGET", 3),
        _I("MEAS",      0, 0),
        _I("QHALT"),
    ])
    result = SolverCPUSim(seed=0).run(circuit)
    assert result.counts == {"0": 200}
```

Run: `uv run pytest xqgb/tests/test_cf.py -v`

Expected: all 4 tests pass.

---

- [ ] **Step 13: Run the full test suite and parity check**

```bash
cargo test -p xqgbc
uv run pytest xqgb/ -v
uv run ruff check xqgb/
cargo clippy -p xqgbc -- -D warnings
uv run python scripts/check-gate-opcode-parity.py
```

Expected: 0 failures, 0 warnings, parity check prints `OK — 29 gate opcodes match`.

---

- [ ] **Step 14: Commit**

```bash
git add \
  conformance/gate_opcodes.yaml \
  xqgbc/src/table.rs \
  xqgbc/src/types/opcode.rs \
  xqgbc/src/types/instruction.rs \
  xqgbc/src/codec.rs \
  xqgbc/src/verifier.rs \
  xqgb/decoder.py \
  xqgb/cpu_sim.py \
  xqgb/transpiler.py \
  xqgb/tests/test_cf.py \
  scripts/check-gate-opcode-parity.py
git commit -m "feat(xqgbc,xqgb): classical control-flow opcodes CF_TARGET/JUMP/JZ/JNZ"
```