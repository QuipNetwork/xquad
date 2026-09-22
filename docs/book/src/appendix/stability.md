# Stability

XQuad is pre-1.0. The root
[`README.md`](https://gitlab.com/quip.network/xquad/-/blob/main/README.md)
says so directly: the instruction set, the binary format, and the public
API may still change before v1.0, and production use is not recommended
yet. Read everything below in that light -- it describes the project's
current process discipline, not a promise that anything is frozen.

## What Is Versioned Together

Eight packages ship from this repository, three to crates.io (`xqvm`,
`xqasm`, `xqcli`) and five to PyPI (`xqffi`, `xqvm_py`, `xqcp`, `xqsa`,
`xquad`). All eight always carry the same version. They release together:
one release MR bumps every version, one tag triggers the one pipeline that
publishes all eight, and the changelog is generated from that same tag.
There is no independent release cadence per package today.

## What CI Actually Guards

Two things fail the build if they drift, checked mechanically by default
rather than caught only by review discipline; the second of the two has
a deliberate, contributor-controlled way out, noted below:

- **Rust and Python VM agreement.** [Conformance](../embedding/conformance.md)
  checks that `xqvm` and `xqvm_py` agree on every behaviour a vector
  covers. Coverage is real but partial -- see that page for what "partial"
  means concretely.
- **Spec and implementation agreement.** Any change to VM semantics --
  opcode table, control flow, stack depth, type system, or the high-level
  constraint expansions -- must touch four things in the same change: the
  normative `spec/xqvm/` files, the Rust implementation, the Python
  reference implementation, and the conformance vectors. CI's
  atomic-spec-MR guard rejects a change that touches only some of them,
  unless a commit in the range carries an `Atomic-Spec-Exempt: <reason>`
  trailer, which deliberately bypasses it for a one-sided change such as
  aligning one implementation to the other's existing behaviour. This
  keeps the four descriptions of VM behaviour from drifting apart
  silently by default; it does not keep the behaviour itself from
  changing, and the exemption is a contributor's call, not a machine
  guarantee.

Both guarantee how a change to observable behaviour is made. Neither
guarantees that observable behaviour stays fixed.

## What Is Not Guaranteed

Nothing here promises binary compatibility across versions. Versions follow
semver as Cargo reads it, where the leftmost non-zero field is the major: before
`1.0` a breaking change bumps the minor (`0.4` to `0.5`) and a patch release
(`0.4.0` to `0.4.1`) carries only compatible changes, fixes and features alike.
That policy governs the crate versions -- it is not a commitment that nothing
observable will ever break before `1.0`. Treat every `0.x` release as a
snapshot, not a foundation to build on without re-checking. If your use
case needs a stability guarantee this project does not yet make, [file an
issue](https://gitlab.com/quip.network/xquad/-/issues) against the
repository rather than assuming one.

## Reading the Documentation for an Older Release

The published book tracks the most recent release. Every tagged release carries
its own copy of these pages in the repository, so an older version is a checkout
away:

```sh
git switch --detach v0.4.0
make build-docs      # renders to docs/book/build/
make serve-docs      # serves it and opens a browser
```

`make serve-docs` rebuilds on edit, so it is also the way to preview a change
before opening a merge request. Return with `git switch -`.

Tags are listed with `git tag --list 'v*' | sort -V`, and the released packages
for each are on [crates.io](https://crates.io/crates/xqvm) and
[PyPI](https://pypi.org/project/xquad/). Tags carrying `-rc` or `-beta` are
pre-releases: they are on both registries, but the published book never
describes one.
