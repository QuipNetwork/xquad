# Portfolio Rebalance

Choose a signed integer weight for each asset, trading expected return off
against a risk matrix, subject to the weights summing to a budget. Negative
weights are short positions, which is the whole reason this example is not a
binary select-or-not model.

## QUBO formulation

- **Input**: number of assets N, a return per asset, a symmetric N x N risk matrix flattened row-major
- **Model**: N integer variables `x_i` in `[-5, 5]`, declared with `lo=`/`hi=`. The model holds `y_i = x_i - lo` in `{0, ..., 10}`; coefficients are written over `x` and `sample.value()` shifts back on the way out.
- **Objective**: `-sum(r_i * x_i) + sum_{i <= j} C_ij * x_i * x_j`
- **Constraint**: `sum(x_i) = B`, as the penalty `P * (sum_i x_i - B)^2`
- **Energy**: shifted twice over, and not comparable with any other example's. The ranged domain drops the constant that substituting `x = y + lo` produces, because XQMX carries no offset field, and the budget square drops its own `P*B^2` the way EQUALITY does. Both shifts are uniform across assignments, so argmin is exact even though the number is not the objective's true value.

### Encoding strategy

The budget constraint is written out by hand rather than handed to
`apply_equality`. Every high-level constraint in the VM expands under
`x^2 = x`, which holds for binary variables only, so xqcp refuses all of them
off a binary model. Expanded, `P * (sum_i x_i - B)^2` is `P` on each diagonal,
`2P` on each off-diagonal pair, and `-2PB` on each linear coefficient. Writing
that square is the same work the HLF would have done, and it is what the open
question about per-domain expansions is about.

The ranged domain then does its own rewriting underneath. Each quadratic write
records `w*lo` against the linear coefficient of both named indices, because
`w * x_i * x_j` expands to `w*y_i*y_j + w*lo*y_i + w*lo*y_j + w*lo^2` once
`x = y + lo` is substituted. A write to the diagonal lands both corrections on
the one index, giving the `2*w*lo` that squaring asks for. None of that is
visible in the model-building code above.

`lo` and `hi` are literals. A runtime `lo` would want the decoder's single
calldata scalar, which the output loop bound already spends, and xqcp raises
naming both rather than picking one.

## DSL methods used

- `problem.define_model(size=N, domain=Domain.INTEGER, lo=-5, hi=5)` -- ranged integer weights
- `model.linear[i].add(w)` and `model.quadratic[i, j].add(w)` -- the only operations a non-binary model supports
- `sample.value(i)` -- the weight in the domain it was declared over, rather than the stored `y`

## What this example proves

`valid == 1` proves that the domain check, the record-layer shift and the
decode compose on a chosen assignment. It proves nothing about optimisation:
no solver samples an integer model yet, so `--solver` is accepted, ignored,
and the pipeline runs against a hand-picked weight vector that sits inside the
domain and sums to the budget. Integer lowering is XQSA v0.5.0 work.

## Pipeline overview

1. **CP** (`xqcp`) -- generate returns and a symmetric risk matrix, declare N ranged integer weights, and write the objective and the budget square as coefficients
2. **Assemble** -- `.xqasm` text to bytecode via `xquad.asm`
3. **Encode** -- run encoder on chosen XQVM to produce the XQMX model
4. **Sample** -- skipped; the runner supplies the assignment itself
5. **Verify** -- verifier checks every weight is inside `{0, ..., 10}` against the `k` replayed from `define_model`, then computes energy
6. **Decode** -- decoder reads each weight and adds `lo`, giving the signed weights back

## Usage

```sh
uv run python examples/portfolio_rebalance/runner.py --seed 42
uv run python examples/portfolio_rebalance/runner.py --n 6 --interpreter rust
```

| Flag | Default | Description |
|------|---------|-------------|
| `--n` | `5` | Number of assets |
| `--solver` | `dwave-cpu` | Accepted and ignored |
| `--interpreter` | `python` | XQVM backend: `python` or `rust` |
| `--seed` | `42` | Random seed |
| `-o` | stdout | Write JSON result to file |

## Canonical output

`example-smoke` validates both interpreters produce `valid == 1` with
`--seed 42 --solver dwave-cpu`. The smoke test is invariant-based --
it checks validity, not exact output.
