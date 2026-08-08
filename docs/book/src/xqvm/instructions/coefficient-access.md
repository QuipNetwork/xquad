# Coefficient Access

Read and write the linear (bias) and quadratic (coupling) coefficients of a
`Model` register. Missing entries read as \\(0\\); writes create the entry on the
first call. All coefficient values are `i64`; `reg` must hold `Model`.

## Linear Coefficients

| Code | Mnemonic | Arguments | Stack Effect | Register Effect | Description |
|------|----------|-----------|--------------|-----------------|-------------|
| `0x60` | `GETLINE` | `reg: Register` | \\([\ldots, i] \to [\ldots, h_i]\\) | `read` | Pop \\(i\\). Push \\(\text{linear}[i]\\) (\\(0\\) if absent). |
| `0x61` | `SETLINE` | `reg: Register` | \\([\ldots, i, v] \to [\ldots]\\) | `mutate` | Pop \\(v\\), then \\(i\\). Set \\(\text{linear}[i] \leftarrow v\\). |
| `0x62` | `ADDLINE` | `reg: Register` | \\([\ldots, i, \delta] \to [\ldots]\\) | `mutate` | Pop \\(\delta\\), then \\(i\\). Accumulate: \\(\text{linear}[i] \mathrel{+}= \delta\\). |

## Quadratic Coefficients

| Code | Mnemonic | Arguments | Stack Effect | Register Effect | Description |
|------|----------|-----------|--------------|-----------------|-------------|
| `0x63` | `GETQUAD` | `reg: Register` | \\([\ldots, i, j] \to [\ldots, J_{ij}]\\) | `read` | Pop \\(j\\), then \\(i\\). Push \\(\text{quad}[i,j]\\) (\\(0\\) if absent). |
| `0x64` | `SETQUAD` | `reg: Register` | \\([\ldots, i, j, v] \to [\ldots]\\) | `mutate` | Pop \\(v\\), then \\(j\\), then \\(i\\). Set \\(\text{quad}[i,j] \leftarrow v\\). |
| `0x65` | `ADDQUAD` | `reg: Register` | \\([\ldots, i, j, \delta] \to [\ldots]\\) | `mutate` | Pop \\(\delta\\), then \\(j\\), then \\(i\\). Accumulate: \\(\text{quad}[i,j] \mathrel{+}= \delta\\). |

## Sparse Storage

Coefficients are stored in sparse `BTreeMap` structures:

- **Linear:** \\(\text{BTreeMap}\langle\text{usize}, \text{i64}\rangle\\) keyed by variable index.
- **Quadratic:** \\(\text{BTreeMap}\langle(\text{usize}, \text{usize}), \text{i64}\rangle\\) keyed by variable pair.

Missing entries implicitly have value \\(0\\). Setting a coefficient to \\(0\\) removes
it from the map, keeping memory usage proportional to the number of non-zero
terms.

## Key Normalisation

Quadratic coefficient keys are normalised so that \\(i \le j\\). Calling
`SETQUAD` or `ADDQUAD` with \\(i > j\\) silently swaps the indices. This means
\\(\text{quad}[3, 5]\\) and \\(\text{quad}[5, 3]\\) refer to the same entry.
