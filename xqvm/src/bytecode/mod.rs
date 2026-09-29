// `opcodes!` is defined inside `types::table` with `#[macro_export]`, so it
// lands at the xqvm crate root automatically.
#[macro_use]
pub(super) mod types;
pub(super) mod builder;
pub mod codec;
pub(super) mod cursor;
pub mod error;
pub(super) mod jump_table;
pub(super) mod program;
pub(super) mod stream;

// ---------------------------------------------------------------------------
// Public API re-exports
// ---------------------------------------------------------------------------

pub use builder::{InstructionBuilder, LabelId};
pub(crate) use cursor::Cursor;
pub use jump_table::JumpTable;
pub use program::{Program, ProgramDecodeError};
pub use stream::InstructionStream;
pub use types::{Instruction, Opcode, Register, RegisterEffect, StackEffect};
