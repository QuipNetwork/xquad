# Allocators

Instructions for creating quantum/combinatorial objects (models, samples) and
vectors in registers.

## Model Allocators

| Code | Mnemonic | Arguments | Stack Effect | Register Effect | Description |
|------|----------|-----------|--------------|-----------------|-------------|
| `0x40` | `BQMX` | `reg: Register` | \\([\ldots, n] \to [\ldots]\\) | `write` | Pop \\(n\\). Allocate a binary QUBO model with variable domain \\(\\{0, 1\\}\\). |
| `0x41` | `SQMX` | `reg: Register` | \\([\ldots, n] \to [\ldots]\\) | `write` | Pop \\(n\\). Allocate a spin Ising model with variable domain \\(\\{-1, 1\\}\\). |
| `0x42` | `XQMX` | `reg: Register` | \\([\ldots, n, k] \to [\ldots]\\) | `write` | Pop \\(k\\), then \\(n\\). Allocate a discrete (chromatic) model with signed centered variable domain \\(\\{-k, -(k{-}1), \ldots, k{-}2, k{-}1\\}\\). Errors with `InvalidDiscreteK` when \\(k < 2\\). |

All model allocators create an `XqmxModel` with empty linear and quadratic
coefficient maps. The parameter \\(n\\) determines the number of variables.

## Sample Allocators

| Code | Mnemonic | Arguments | Stack Effect | Register Effect | Description |
|------|----------|-----------|--------------|-----------------|-------------|
| `0x43` | `BSMX` | `reg: Register` | \\([\ldots, n] \to [\ldots]\\) | `write` | Pop \\(n\\). Allocate a binary sample with \\(\text{values} = [0; n]\\). |
| `0x44` | `SSMX` | `reg: Register` | \\([\ldots, n] \to [\ldots]\\) | `write` | Pop \\(n\\). Allocate a spin sample with \\(\text{values} = [-1; n]\\) (spin-down default). |
| `0x45` | `XSMX` | `reg: Register` | \\([\ldots, n, k] \to [\ldots]\\) | `write` | Pop \\(k\\), then \\(n\\). Allocate a discrete sample with signed centered domain \\(\\{-k, -(k{-}1), \ldots, k{-}2, k{-}1\\}\\) and \\(\text{values} = [0; n]\\). Errors with `InvalidDiscreteK` when \\(k < 2\\). |

Samples hold a vector of variable assignments. The default value depends on the
domain: \\(0\\) for binary and discrete, \\(-1\\) for spin. Because the discrete
domain is symmetric around zero (\\(\\{-k, \ldots, k{-}1\\}\\)), the default \\(0\\)
is always in-domain.

## Vec Allocators

| Code | Mnemonic | Arguments | Stack Effect | Register Effect | Description |
|------|----------|-----------|--------------|-----------------|-------------|
| `0x4A` | `VEC` | `reg: Register` | \\([\ldots] \to [\ldots]\\) | `write` | Create an empty integer vec. Identical to `VECI` at runtime. |
| `0x4B` | `VECI` | `reg: Register` | \\([\ldots] \to [\ldots]\\) | `write` | Create an empty `VecInt`. |
| `0x4C` | `VECX` | `reg: Register` | \\([\ldots] \to [\ldots]\\) | `write` | Create an empty `VecXqmx` (vector of models). |

## Domain Types

| Domain | Variable values | Created by |
|--------|----------------|------------|
| Binary | \\(\\{0, 1\\}\\) | `BQMX`, `BSMX` |
| Spin | \\(\\{-1, 1\\}\\) | `SQMX`, `SSMX` |
| Discrete(\\(k\\)) | \\(\\{-k, -(k{-}1), \ldots, k{-}2, k{-}1\\}\\) (requires \\(k \ge 2\\)) | `XQMX`, `XSMX` |
