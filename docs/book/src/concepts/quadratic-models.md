# Quadratic Models

Every backend XQuad targets agrees on one thing: a single number, computed
from a candidate assignment, that the solver tries to make as small as
possible. That number is the model's energy, and a quadratic model is the
recipe for computing it:

$$H(x) = \sum_i \text{linear}[i] \cdot x_i + \sum_{i<j} \text{quadratic}[i,j] \cdot x_i \cdot x_j$$

`x` is a vector of variables. `linear[i]` scales with variable `i`'s own
value, and `quadratic[i,j]` scales with the product of `i` and `j`'s values
-- a cost, or a saving if negative. Read those as "the cost of turning `i`
on" and "the extra cost of turning `i` and `j` on together" when `x` is
binary; [Three Domains](#three-domains) below covers two domains where
nothing is "on." No term touches three variables at once -- that is what
"quadratic" means here. `H` is called the model's Hamiltonian, borrowing
the term physicists use for a system's total energy, because the annealing
hardware XQuad can target literally is a physical system settling toward a
low-energy state.

XQVM builds this structure directly: `BQMX`/`SQMX`/`XQMX` allocate a model
register, and `SETLINE`/`SETQUAD`/`ADDLINE`/`ADDQUAD` write the two
coefficient maps one term at a time. See
[Allocators](../xqvm/instructions/allocators.md) and
[Coefficient Access](../xqvm/instructions/coefficient-access.md) for the
instructions.

## Why Optimisation Problems Become One of These

A combinatorial problem -- which cities to visit in what order, or which
items fit in the knapsack -- usually starts life as a set of decisions
plus rules those decisions must obey. A quadratic model has no separate
mechanism for rules: it is one function, and a solver's only operation is
minimising it. Every constraint has to become part of the same `H`, or the
solver never sees it. The
[Penalties](#penalties-folding-a-constraint-into-the-objective) section
below is that mechanism, and the concept this page most wants you to leave
with.

## A Worked Example: Max-Cut

Take Max-Cut: split a graph's nodes into two groups to maximise the total
weight of edges that cross between them. \\(x_i \in \\{0, 1\\}\\) marks
which group node \\(i\\) is in. For each weighted edge \\((i, j, w)\\):

$$\text{linear}[i] \mathrel{+}= -w, \qquad \text{linear}[j] \mathrel{+}= -w, \qquad \text{quadratic}[i,j] \mathrel{+}= 2w$$

That edge's contribution to \\(H\\) is \\(-w(x_i + x_j - 2x_ix_j)\\), and
\\(x_i + x_j - 2x_ix_j\\) is the binary XOR of \\(x_i\\) and \\(x_j\\): it
is \\(1\\) when \\(x_i \neq x_j\\) and \\(0\\) when they match. That edge
contributes \\(-w\\) exactly when it crosses the partition, and \\(0\\)
otherwise. Summed over every edge, \\(H\\) is the negative of the
cut weight, so minimising \\(H\\) maximises the cut. `examples/maxcut/runner.py`
builds exactly this model.

For three nodes and edges \\((0,1,3)\\), \\((1,2,5)\\), \\((0,2,1)\\), the
rule above gives:

$$\text{linear} = \\{0: -4,\ 1: -8,\ 2: -6\\}, \qquad \text{quadratic} = \\{(0,1): 6,\ (1,2): 10,\ (0,2): 2\\}$$

Two assignments, worked by hand:

- \\(x = (1, 1, 0)\\): \\(H = (-4)(1) + (-8)(1) + (-6)(0) + 6(1)(1) + 10(1)(0) + 2(1)(0) = -12 + 6 = -6\\).
  Nodes 0 and 1 share a group, so only the two edges crossing to node 2
  count: \\(5 + 1 = 6\\), and \\(-6\\) is the negative of that, matching.
- \\(x = (1, 0, 1)\\): \\(H = (-4)(1) + (-8)(0) + (-6)(1) + 6(1)(0) + 10(0)(1) + 2(1)(1) = -10 + 2 = -8\\).
  Every edge except \\((0,2)\\) crosses: \\(3 + 5 = 8\\), matching. This is
  the lowest of the four distinct cuts -- a partition and its complement
  always have the same energy, so the eight assignments give four cuts --
  so it is the Max-Cut optimum for this graph.

## Three Domains

`x_i` is not always a 0/1 bit. XQVM supports three domains, and the choice
affects one thing above all: whether a backend can solve the result.

| Domain | Values | Model allocator | Sample allocator |
|---|---|---|---|
| Binary | \\(\\{0, 1\\}\\) | `BQMX` | `BSMX` |
| Spin | \\(\\{-1, 1\\}\\) | `SQMX` | `SSMX` |
| Discrete(\\(k\\)) | \\(\\{-k, \ldots, k{-}1\\}\\), \\(k \ge 2\\) | `XQMX` | `XSMX` |

**Binary** is QUBO -- Quadratic Unconstrained Binary Optimisation -- the
domain most combinatorial formulations target directly: a variable is
either selected or not. The Max-Cut model above is binary.

**Spin** is the Ising model: each variable is a magnetic moment pointing up
or down, \\(-1\\) or \\(+1\\), which is the domain quantum annealing
hardware minimises natively. A spin variable's linear coefficient rewards
one orientation and penalises the other by the same amount, rather than
switching a cost on or off. Binary and spin describe the same choices:
substituting \\(x_i = (s_i + 1)/2\\) (or \\(s_i = 2x_i - 1\\)) turns any
binary Hamiltonian into a spin Hamiltonian over the same variables, with
rescaled coefficients and one additive constant that does not change which
assignment is optimal. Build a model in one domain and you can rebuild it
in the other; between these two the choice is free, since every current
`xqsa` solver accepts both, so pick whichever domain the problem is
natural in.

**Discrete** generalises past two states to give a variable \\(2k\\)
integer values directly, suited to a quantity with a natural ordering or
magnitude -- a position in a short list -- without one-hot encoding it
into several binary variables first. Unlike binary and spin,
it is not a relabelling of the other two: it cannot encode an unordered
categorical choice, because a quadratic form over integer variables
cannot express that two values merely differ without also expressing by
how much.

Discrete is the domain with a real consequence: every current `xqsa`
solver rejects it. A discrete `XqmxModel` is a real thing you can build in
XQVM bytecode today, but there is no backend yet that can solve one. See
[Backends](backends.md).

A model and a sample share a domain and a variable count, but not a shape:
a model is two sparse coefficient maps, `linear` and `quadratic`, while a
sample is one value per variable and nothing else.
[Allocators](../xqvm/instructions/allocators.md) covers both families and
their default values in full.

## Energy

Given a model and a candidate assignment (a sample), `ENERGY` computes
\\(H(x)\\) for that specific `x` and pushes the result -- the same
formula as the model's Hamiltonian above, evaluated at one point.
"Energy" and "Hamiltonian value at x" mean the same thing throughout
XQuad's documentation. A solver's job is to search for the `x` that
minimises this value; XQVM's job is only to compute it, which is what lets
a program check a solver's answer independently rather than trust it. See
[Energy Evaluation](../xqvm/instructions/energy.md).

## Penalties: Folding a Constraint into the Objective

The constraint instructions below expand assuming binary variables, where
\\(x^2 = x\\). That identity does not hold for spin variables, where
\\(x^2 = 1\\) always: apply these instructions to a `BQMX` model, and
convert a spin model to binary first if you need one of them (see
[Three Domains](#three-domains)).

Take the simplest useful constraint: exactly one of a set of variables
should be 1 (a one-hot choice -- pick exactly one city to visit first, one
colour for this node). Written as a penalty term:

$$H \mathrel{+}= P \cdot \left(\sum_i x_i - 1\right)^2$$

The squared term is zero exactly when the constraint holds (the sum is 1)
and strictly positive for every assignment that violates it -- pick zero
variables and the sum is 0, pick two and the sum is 2, either way
\\((\sum x_i - 1)^2 \ge 1\\). Adding this to \\(H\\) means violating the
constraint always costs at least \\(P\\) units of energy on top of whatever
the rest of the objective says. A solver minimising the combined \\(H\\)
has a direct incentive to satisfy the constraint, without any code path in
the solver that knows constraints exist -- it is minimising one number, the
same way it always does.

This is the general shape behind most of XQVM's high-level constraint
instructions: `ONEHOTR`/`ONEHOTC` are exactly the sum above over a grid
row or column, and `EXCLUDE`, `IMPLIES`, `EQUALITY`, `ATLEAST`, and
`ATLEASTW` are the same idea applied to different rules: mutual exclusion,
implication, weighted equality, at-least-`k` with unit weights, and
at-least-`k` with arbitrary weights. The instruction reference also
documents `REDUCE`, which uses the same technique -- a term that is zero
exactly when a condition holds -- but to enforce an algebraic identity
between variables rather than a rule from the problem; that is a different
enough job to read about on its own. The full expansion for each
instruction -- which linear and quadratic coefficients change by how much
-- is reference material, not a concept, and lives on
[High-Level Constraints](../xqvm/instructions/constraints.md) and in the
normative
[HLF specification](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqvm/HLF.md).
This page is not repeating that table; the mechanism above is what to carry
forward from it.

## Choosing a Penalty Weight

\\(P\\) is not a detail to default and forget -- it is the real engineering
decision in penalty-based modelling, and it fails in both directions.

Too low, and the solver buys its way out of the constraint: \\(P\\) must
exceed the largest objective improvement any violating assignment can buy
over the best feasible one, or the minimiser takes that trade, because
nothing in \\(H\\) told it not to. Suppose the Max-Cut model above also
carried a one-hot constraint over nodes 0 and 2 only, with weight \\(P\\).
The unconstrained optimum \\(x = (1, 0, 1)\\) worked out above violates
it; the best assignments that satisfy it are \\(x = (1, 1, 0)\\), worked
out above, and \\(x = (0, 0, 1)\\), both at \\(H = -6\\). Violating the constraint lowers the
energy from \\(-6\\) to \\(-8\\), an improvement of \\(2\\), so
`penalty = 1` costs less than it saves and the minimiser still prefers
the infeasible optimum. `penalty = 2` ties the two, and \\(P\\) must
exceed \\(2\\) before satisfying the constraint wins outright.

Too high does not corrupt the model itself. For any two assignments that
both satisfy the constraint, the penalty term is 0 on both, so their
energy difference is exactly the objective's difference, independent of
\\(P\\); `ENERGY` computes that difference with exact `i64` arithmetic, so
nothing in \\(H\\) degrades. What a large \\(P\\) costs happens outside the
model. On a fixed-precision device, an objective difference much smaller
than \\(P\\) can fall below the device's resolution once the combined
\\(H\\) is scaled to fit it, so the device stops representing that
difference at all. And a large \\(P\\) raises the energy barrier between
feasible regions, so a heuristic such as simulated annealing gets trapped
in whichever feasible region it reaches first and stops exploring for a
better one elsewhere.

Getting this right in practice -- how to size \\(P\\) relative to your
objective, and how to tell from a solver's output which failure mode you
hit -- belongs to
[Constraints](../modelling/constraints.md#choosing-a-penalty-weight).
What to take from this page is that the weight is a parameter you choose,
not one XQVM chooses for you: every constraint instruction pops `penalty`
off the stack as an ordinary operand.
