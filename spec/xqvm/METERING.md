# XQVM Step Metering

## What a Step Is

A step is a unit of metered execution cost, not an instruction. Every
program dispatches exactly as many instructions as its control flow visits,
but a program that dispatches few instructions can still do a large amount
of work if those instructions touch data the program itself built --
evaluating a model with a million coefficients, expanding a constraint over
a thousand indices, copying a model out of a register. Step metering exists
so that the cost an embedder charges (`WeightPerStep × steps`, per the
Substrate pallet) tracks the work done, not the instruction count.

One step is calibrated to be one `NOP` dispatch: dispatching any instruction
costs at least one step, and no step is priced below the operation it is
charged for.

Every instruction charges [`BASE_STEPS`](#the-constants) before dispatch.
Opcodes whose work scales with program-controlled data -- the length of a
vector, the size of a model, the number of indices in a constraint -- charge
additional units *before* doing that work, following the same discipline as
the byte budget described in `spec/xqvm/SPEC.md` (Runtime Limits): a program
that cannot pay for the work does none of it.

## The Constants

The six constants below are shared, value for value, between the Rust VM
(`xqvm/src/metering.rs`) and the Python reference VM (`xqvm_py/metering.py`).
`scripts/check-metering-parity.py` enforces that the three sources -- the
Rust constants, the Python constants, and this table -- agree. Changing a
value here is a breaking change to observable behaviour and must be made in
all three places at once.

| Constant | Value | Meaning |
|----------|-------|---------|
| `BASE_STEPS` | 1 | One instruction dispatch. |
| `COEFF_WRITE_STEPS` | 13 | One coefficient written into a model's sparse map. |
| `MODEL_TERM_STEPS` | 1 | One model term accumulated into an energy. |
| `GRID_CELL_STEPS` | 3 | One cell of a grid's linear surface read by a row or column scan. |
| `SAMPLE_COPY_STEPS` | 1 | One sample element written. |
| `ELEMENT_COPY_STEPS` | 1 | One `i64` copied out of a vec. |

### How the constants were measured

The constants are calibrated against wall-clock measurements of the native
Rust VM, not chosen a priori. `xqvm/examples/metering_calibration.rs` runs
each operation under test opposite a paired baseline that isolates it from
everything the surrounding program also pays for (PUSH operands, dispatch
overhead, setup), takes the median of 11 independent trials, and reports the
ratio to `NOP`'s per-dispatch cost. Each constant is that ratio rounded up
to the nearest integer, floored at 1 -- rounding down would let an operation
be metered for less than it costs, which is the underpricing this metering
exists to close.

The canonical calibration run:

| Quantity | Measured ns (median) | Ratio to `NOP` | Rounded constant |
|----------|----------------------:|----------------:|-------------------|
| `nop_ns` | 3.137 | 1.00 | (the unit itself) |
| `coeff_write_ns` | 28.159 | 8.98 | 9 |
| `coeff_rmw_ns` | 40.017 | 12.75 | 13 |
| | | | `COEFF_WRITE_STEPS = max(9, 13) = 13` |
| `energy_term_ns` | 1.434 | 0.46 | `MODEL_TERM_STEPS = 1` |
| `grid_cell_ns` | 7.049 | 2.25 | `GRID_CELL_STEPS = 3` |
| `element_copy_ns` | 0.003 | 0.00 | `ELEMENT_COPY_STEPS = 1` (`SAMPLE_COPY_STEPS` shares this measurement) |

`GRID_CELL_STEPS` is measured over a *model* grid, whose cells are sparse-map
lookups, rather than a sample grid, whose cells are vec indices. `ROWSUM`,
`COLSUM`, `ROWFIND` and `COLFIND` accept either surface, and pricing the
cheaper one would underprice the dearer. The measured row is fully
populated so that every lookup hits: a scan over a sparse row can
short-circuit on misses, and calibrating against that would price the scan
below what a program that populates its grid actually costs.

`COEFF_WRITE_STEPS` prices two distinct operations and is the maximum of the
two: `coeff_write_ns` measures the insert-only write (`SETLINE`, `SETQUAD`)
and `coeff_rmw_ns` the read-modify-write one (`ADDLINE`, `ADDQUAD`, and every
constraint expansion, which look the entry up before inserting it). Taking
the maximum keeps the constant from being cheaper than either operation it
stands for -- and the read-modify-write path is where the coefficient-write
volume actually is, since `ONEHOTR`, `ONEHOTC`, `EQUALITY`, `ATLEAST`,
`ATLEASTW` and `REDUCE` all write through it.

These are native, single-machine measurements offered as the derivation of
the constants above, not a normative performance guarantee: a conforming
implementation must charge these unit counts, not reproduce these
nanosecond figures.

## The Per-Opcode Cost Table

Every opcode charges `BASE_STEPS` for its own dispatch. The table below
lists the *additional* charge an opcode adds on top of that base cost, and
the point in its execution at which the charge lands -- always before the
work it prices.

| Opcode | Additional charge | Count expression |
|--------|--------------------|-------------------|
| `LVAL` | Cost of copying the current loop value, when it is an XQMX element | [`value_copy_steps`](#value_copy_steps) of the element |
| `ITER` (`vec<int>` source) | One `ELEMENT_COPY_STEPS` per element copied into the loop frame | `range.len() * ELEMENT_COPY_STEPS` |
| `ITER` (`vec<xqmx>` source) | Cost of copying every element in the sliced range | sum of [`value_copy_steps`](#value_copy_steps) over the slice |
| `INPUT` | Cost of copying the calldata value, when it is a model or a vec | [`value_copy_steps`](#value_copy_steps) of the calldata value |
| `OUTPUT` | Cost of copying the register value, when it is a model or a vec | [`value_copy_steps`](#value_copy_steps) of the register value |
| `BSMX` | One `SAMPLE_COPY_STEPS` per sample element allocated | `size * SAMPLE_COPY_STEPS` |
| `SSMX` | One `SAMPLE_COPY_STEPS` per sample element allocated | `size * SAMPLE_COPY_STEPS` |
| `XSMX` | One `SAMPLE_COPY_STEPS` per sample element allocated | `size * SAMPLE_COPY_STEPS` |
| `SLACK` | Two `ELEMENT_COPY_STEPS` per entry appended | `entries * 2 * ELEMENT_COPY_STEPS` |
| `SETLINE` | One `COEFF_WRITE_STEPS`, model targets only | `COEFF_WRITE_STEPS` |
| `ADDLINE` | One `COEFF_WRITE_STEPS`, model targets only | `COEFF_WRITE_STEPS` |
| `SETQUAD` | One `COEFF_WRITE_STEPS`, model targets only | `COEFF_WRITE_STEPS` |
| `ADDQUAD` | One `COEFF_WRITE_STEPS`, model targets only | `COEFF_WRITE_STEPS` |
| `ROWSUM` | One `GRID_CELL_STEPS` per cell of the row it scans | `cols * GRID_CELL_STEPS` |
| `COLSUM` | One `GRID_CELL_STEPS` per cell of the column it scans | `rows * GRID_CELL_STEPS` |
| `ROWFIND` | One `GRID_CELL_STEPS` per cell of the row it scans | `cols * GRID_CELL_STEPS` |
| `COLFIND` | One `GRID_CELL_STEPS` per cell of the column it scans | `rows * GRID_CELL_STEPS` |
| `ONEHOTR` | Worst-case cost of the equality expansion over the row's indices | [`equality_expansion_steps`](#equality_expansion_steps)`(cols)` |
| `ONEHOTC` | Worst-case cost of the equality expansion over the column's indices | [`equality_expansion_steps`](#equality_expansion_steps)`(rows)` |
| `EQUALITY` | Worst-case cost of the equality expansion | [`equality_expansion_steps`](#equality_expansion_steps)`(len(indices))` |
| `ATLEAST` | Worst-case cost of the equality expansion over the original and slack indices | [`equality_expansion_steps`](#equality_expansion_steps)`(n + num_slacks)` |
| `ATLEASTW` | Worst-case cost of the equality expansion over the original and slack indices | [`equality_expansion_steps`](#equality_expansion_steps)`(n + num_slacks)` |
| `EXCLUDE` | One `COEFF_WRITE_STEPS` for the quadratic term it writes | `COEFF_WRITE_STEPS` |
| `IMPLIES` | Two `COEFF_WRITE_STEPS`, for the linear and the quadratic term it writes | `2 * COEFF_WRITE_STEPS` |
| `REDUCE` | Four `COEFF_WRITE_STEPS`, for the three quadratic and one linear term the Rosenberg reduction writes | `4 * COEFF_WRITE_STEPS` |
| `ENERGY` | Cost of copying the sample and accumulating every model term | [`model_eval_steps`](#model_eval_steps)`(sample_len, terms)` |
| `RANGE` (empty loop) | One `BASE_STEPS` per instruction the skip scan consumes | `skipped * BASE_STEPS` |
| `ITER` (empty slice) | One `BASE_STEPS` per instruction the skip scan consumes | `skipped * BASE_STEPS` |

The four grid scans charge *after* validating their operand and *before*
walking the grid. An ungridded register or a row or column index outside its
axis raises the fault it raised before this metering existed, having charged
only `BASE_STEPS`; a scan that is going to happen pays for its full extent
up front, whether or not it short-circuits. `ROWFIND` and `COLFIND` are
charged for the whole row or column even though they stop at the first
match, for the same reason the equality expansions are charged their worst
case: what the scan will actually touch is not known until it has run.

The extent is program-controlled and bounded only by the register's declared
size, which `RESIZE` checks (`rows * cols <= size`). That is an allocation
bound, not a work-per-step bound: a register declared with the whole
allocation budget can be gridded as a single row, and at `BASE_STEPS` alone
one `ROWSUM` over it would buy a scan of the entire budget for one step.

`RESIZE` itself is not charged beyond the base cost. It stores `rows` and
`cols` after checking them and touches no cell; the work its dimensions
imply is charged to the scans that read them.

Every opcode not listed above -- including `BQMX`, `SQMX`, `XQMX`, and
`RANGE` whenever it actually opens a loop -- charges only `BASE_STEPS`. This
is stated explicitly rather than left to inference, because both cases look
at first glance like they should cost more:

- **`RANGE` is not charged when it opens a loop.** `RANGE` pops `start` and
  `count` and stores only the loop frame's `current`/`end` bounds; it
  materialises no values. The loop it opens is still metered -- every `NEXT`
  that advances the loop pays `BASE_STEPS` for its own dispatch -- so
  entering a loop is O(1) work and is priced accordingly.

  Skipping one is not. When `count <= 0` (and, for `ITER`, when the slice is
  empty) the body is not entered: the implementation instead scans forward
  through the instruction stream, tracking nesting depth, until it reaches
  the matching `NEXT`. That scan is O(body length), so each instruction it
  consumes -- including the terminating `NEXT` -- charges `BASE_STEPS`,
  exactly as a dispatched instruction does. Without this an empty-bodied
  loop in a hot path would rent an unmetered walk over the deployed
  bytecode for a handful of steps. The scanned instructions are metered but
  *not* dispatched, so they do not increment the instruction count; the step
  count is the observable quantity, and it includes them. A scan that runs
  off the end of the stream without finding its `NEXT` has still charged for
  every instruction it consumed before it faults.
- **`BQMX`, `SQMX`, and `XQMX` are not charged beyond the base cost.** These
  opcodes allocate an *empty* model: no coefficients are written, so there
  is nothing to charge `COEFF_WRITE_STEPS` for. This is unlike the sample
  allocators `BSMX`, `SSMX`, and `XSMX`, which fill a sample buffer with
  `size` values and are charged `size * SAMPLE_COPY_STEPS` for doing so. An
  implementer expecting the model and sample allocators to be priced
  symmetrically will guess wrong without this note.

## The Formulas

### `equality_expansion_steps`

```
equality_expansion_steps(n) = (n + pairs(n)) * COEFF_WRITE_STEPS
pairs(n) = (n * (n - 1)) / 2
```

One linear term per index and one quadratic term per unordered pair -- the
same shape as the byte-budget charge for the same expansion (the Rust VM's
`equality_expansion_bytes`, which the `XQMX size` row of `spec/xqvm/SPEC.md`'s
Runtime Limits table describes as an implementation-defined allocation
budget). All arithmetic is integer and saturating: `n * (n - 1)`,
`/ 2`, `n + pairs(n)`, and the final multiplication by `COEFF_WRITE_STEPS`
each saturate at `u64::MAX` rather than wrapping, so an `n` large enough to
overflow the pair count prices out to the maximum charge and is refused by
the step budget rather than wrapping into an affordable number.

This is a worst-case bound, not a measurement of the work the expansion
actually does. Repeated indices in `indices` collide on the same sparse-map
key, so the expansion can write fewer entries than `equality_expansion_steps`
charges for. The charge must still be computed and paid before the
expansion runs, because the number of distinct entries the expansion will
produce is not known until after the collisions have been resolved, and a
program that could defer payment until then could make the host do
unbounded work for free.

### `model_eval_steps`

```
model_eval_steps(sample_len, terms) = sample_len * SAMPLE_COPY_STEPS
                                     + terms * MODEL_TERM_STEPS
```

`terms` is the model's linear-entry count plus its quadratic-entry count.
Both multiplications and the addition saturate. This charges for `ENERGY`
copying the sample out of its register (`sample_len` elements) and then
accumulating every model term into the energy (`terms` terms); both halves
are computed and charged before either the copy or the accumulation begins.

### `value_copy_steps`

```
value_copy_steps(v) = match v:
    Unset | Int            -> 0
    VecInt(elements)        -> len(elements) * ELEMENT_COPY_STEPS
    Sample(s)                -> len(s.values) * SAMPLE_COPY_STEPS
    Model(m)                  -> (m.linear_len() + m.quadratic_len()) * COEFF_WRITE_STEPS
    VecXqmx(elements)         -> sum over elements of value_copy_steps(element)
```

Every intermediate sum and product saturates. Scalars (`Unset`, `Int`) are
free: copying one is a machine-word copy regardless of what value it holds.
Everything else is charged for what it actually holds -- copying a model
clones its coefficient maps, so the charge is proportional to how many
coefficients the model has, not to a fixed per-value cost. The `VecXqmx`
case recurses rather than inlining the `Model` formula, so an element is
charged for what it actually is: a vec of models costs the coefficient rate
per element, and a vec of samples the sample rate. `ITER` charges the same
elements one at a time through this same function, so the recursion is what
keeps a vec charged identically whether it is copied whole or iterated.
This function is the charge for `LVAL`, `INPUT`, and `OUTPUT` wherever the
value in question may be a model or a vector; the per-opcode table above
names it explicitly at each site.

## Known Slack: `EQUALITY` and the `ATLEAST` Family

`EQUALITY`, `ATLEAST`, and `ATLEASTW` each clone the contents of their
`indices` register (and, for `EQUALITY`, the `coeffs` register too) a few
instructions above the point where `equality_expansion_steps` is charged,
in order to validate vector lengths and compute the model growth the
expansion will require. Each clone is O(n) in the number of indices; the
charge that follows prices the O(n^2) expansion.

This means up to O(n) element copies happen before the step charge lands,
uncharged by `equality_expansion_steps` (which prices the expansion, not
the setup that determines its size). This is deliberate slack, not an
oversight: closing it would require restructuring all three handlers so
that the length validation and growth computation happen without cloning,
and the O(n) cost it hides is dominated by the O(n^2) charge that follows
for any `n` large enough to matter. A conforming implementation is not
required to reproduce this slack exactly -- the observable contract is the
step *count*, defined by the formulas above, not the sequence of internal
clones that precedes it -- but an implementation that charges for the
setup clones in addition to the formula would overcharge relative to this
specification and fail conformance.

## Conformance

Step counts are observable: two implementations of this specification
executing the same program, against the same calldata, must report the
same total step count, and the same count at every point the count can be
observed (for example through a tracer). This is a stronger requirement
than agreeing on outputs, because two implementations can compute the same
result by different internal routes that would charge different amounts
under a less precisely specified metering scheme -- which is exactly why
the base cost, the per-opcode additional charges, the three formulas, and
the known slack above are all normative rather than left to
implementation discretion. The order in which an opcode validates its
register operands is observable in the same way, since it decides which
error a program with two ill-typed registers sees: `ENERGY` validates its
model register completely -- that it holds an XQMX, and that the XQMX is in
model mode -- before it looks at its sample register at all, and charges
only after both operands have passed. Checking one property across both
operands before checking the other is not conforming: for `ENERGY r0 r1`
with a sample-mode XQMX in `r0` and an int in `r1`, the fault belongs to
`r0`.

The *identity* of that fault is not uniform across the two shipped VMs, and
this specification does not pin it. `xqvm/src/error.rs` has no mode-error
variant at all: a `RegVal` is either a model or a sample, so the Rust VM
reports a sample in a model slot as a register-type error, where `xqvm_py`,
whose `XQMX` carries a mode flag, reports a mode error. That divergence
spans every mode check rather than `ENERGY` alone and predates step
metering; it is tracked separately. Normative here are the operand order
and the placement of the charge after validation, which the two VMs do
agree on.

Step counts bound the work a *conforming* execution does, not the work an
instrumented one does. An implementation that snapshots register values
around each dispatch in order to drive a tracer copies O(register) data per
instruction that this specification does not charge for, because the
observable step count must not depend on whether a tracer is attached. An
embedder that meters untrusted programs must therefore run them untraced, or
account for the tracing overhead outside the step budget.

`xqvm_py/tests` and `xqvm/tests/integration.rs` each carry step-count
assertions cross-checked against this specification; `scripts/check-metering-parity.py`
checks that the constants agree across `xqvm/src/metering.rs`,
`xqvm_py/metering.py`, and the constants table above.
