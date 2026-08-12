# Install XQuad

This page installs XQuad, confirms the install works, and gets the
runnable examples this book uses. It also covers what a GPU or QPU solver
needs beyond the base install. [First Problem](first-problem.md) picks up
from here with a real run.

## Install

The [root README's Install section](https://gitlab.com/quip.network/xquad/-/blob/main/README.md#install)
is the canonical install reference for both ecosystems this project ships
into -- prebuilt wheel platforms, the Rust toolchain required to build the
`xqffi` extension from source, and installing individual Python packages
instead of the umbrella. The two commands most readers need:

```sh
cargo install xqcli      # gives you the `xquad` binary
pip install xquad        # Python umbrella: the full pipeline
```

## Verify the Install

`xquad --version` prints the installed CLI's version, confirming the
`xqcli` binary is on `PATH` and runs.

```sh
$ python -c "import xquad; print('ok')"
ok
```

The Rust binary installs cleanly. The Python side can still fail: `pip
install xquad` reports success, and `import xquad` still raises
`ModuleNotFoundError`. Re-running `pip install xquad` does not change
the outcome. If that happens, skip straight to [Get the
Examples](#get-the-examples) below: its `git clone` plus `make deps-py`
path gives a working `import xquad` regardless of what the published
wheel does.

<!-- xquad:defect QUI-1020 -->
> **Known issue.** The published wheels for `xquad`, `xqcp`, `xqsa` and `xqvm_py` carry no
> importable package directory, so the install reports success while `import xquad` fails;
> the Rust CLI is unaffected. Use the `git clone` plus `make deps-py` path in [Get the
> Examples](#get-the-examples) below for a working install today. Report problems at the
> [issue tracker](https://gitlab.com/quip.network/xquad/-/issues).

## Get the Examples

Every `uv run python examples/...` command in this book, including on
the next page, runs against a checkout of this repository -- the
examples are not part of any published package, so `cargo install` and
`pip install` above do not put them on disk. Get one and set up the
Python workspace once:

```sh
git clone https://gitlab.com/quip.network/xquad.git
cd xquad
make deps-py
```

`make deps-py` needs [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
and the Rust toolchain `cargo install xqcli` above already needed. It
syncs the Python workspace and wires up cross-package imports, so
`uv run python examples/<name>/runner.py` then works from the repository
root -- the same command every example and cookbook page in this book
uses.

## GPU and QPU Install Prerequisites

The base install runs CPU simulated annealing only, through `xqsa`'s
`dwave-cpu` backend. Every other solver -- a local GPU or a real D-Wave
QPU -- needs an extra on top, and the extra needs hardware or credentials
this page cannot install for you. `xqsa/pyproject.toml` defines the
extras, and `xquad/pyproject.toml` forwards to the ones it re-exports:

- `cuda` -- `cupy-cuda12x>=13.0`, `nvidia-cuda-nvrtc-cu12`, and
  `nvidia-cuda-runtime-cu12`, for a local NVIDIA GPU.
- `dwave` -- `dwave-system>=1.0`, for the real D-Wave QPU.
- `metal` -- `pyobjc-framework-Metal>=11.0`, marked `sys_platform ==
  'darwin'`, for a local Apple GPU. The marker means `pip install
  xquad[metal]` succeeds on Linux and installs nothing.
- `quip` -- `substrate-interface>=1.7.4,<2` plus `quip-signer>=0.2.2`,
  for the Quip network solver, which needs a `QUIP_RPC_URL` and a
  configured signer; see [Quip Network](../solving/quip-network.md) for
  what that solver does. This extra exists only on `xqsa`:
  `xquad/pyproject.toml` has no matching entry, so `pip install
  xquad[quip]` does not install it; `pip install xqsa[quip]` does.

`xqcp`, `xqffi`, and `xqvm_py` define no optional dependencies at all.

<!-- xquad:defect QUI-1020 -->
> **Known issue.** `xquad` declares `cuda`, `dwave` and `metal` extras but no `quip`
> extra, so `pip install xquad[quip]` fails while every sibling extra installs
> successfully. Install directly from `xqsa` instead: `pip install xqsa[quip]`. Report
> problems at the [issue tracker](https://gitlab.com/quip.network/xquad/-/issues).

The only extra this page can fully specify is `[dwave]`: it needs a D-Wave
Leap account and a `DWAVE_API_TOKEN`, and `dwave ping` confirms both. A
local GPU (`[cuda]` or `[metal]`) is another option; see [Local
Solvers](../solving/local.md#driver-prerequisites) for those two extras
and their driver checks.

`pip install xquad[cuda]`, `xquad[metal]`, and `xquad[dwave]` each forward
to the matching `xqsa` extra, and extras are composable:
`pip install "xquad[cuda,dwave]"`.

A missing extra does not break the base install: `import xqsa` never
fails just because an optional extra is absent. Only constructing the
solver class that needs it does, raising `ImportError` with a
`pip install xqsa[...]` hint naming the extra to add.

Per-solver parameters, driver-level troubleshooting, and what a QPU
result contains that a CPU one does not are covered in
[Solving Overview](../solving/README.md), [Local Solvers](../solving/local.md),
and [D-Wave QPU](../solving/dwave-qpu.md). This section only covers what
to install before you get there.

## Where to Go From Here

- **[First Problem](first-problem.md)** -- run a complete problem end to
  end and get a real answer back.
- **[What Happened](what-happened.md)** -- the explanation of what that
  run just did.
- **[XQVM Reference](../xqvm/README.md)** -- a minimal hand-written
  `.xqasm` program, if you want to see the machine underneath before
  running anything larger.
- **[Toolchain Map](../concepts/README.md)** -- the pieces XQuad is built
  from, and how they hand off to each other.
