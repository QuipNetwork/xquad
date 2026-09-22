## Summary

<!-- Describe what this MR changes and why. Reference related issues with QUI-NNN. -->

## Compatibility

Pick one. The answer decides the target branch, so it has to match the one chosen on the form.

- [ ] **Non-breaking** -- targets `main`, ships in the next patch release
- [ ] **Breaking** -- targets `dev`, ships in the next minor, and the title carries `!`

Before 1.0 the minor is the breaking bump and the patch carries fixes *and*
features, so the question is "does this break?" and not "is this a fix?".
`docs/guide/gitflow-protocol.md` has the full routing table.

### If non-breaking, justify it (2-3 lines, required)

<!-- Which of these did this change touch, and why can a consumer pinned to
     the current minor take it without editing their code?
       - public Rust or Python API surface
       - bytecode encoding, the opcode table, or VM semantics
       - assembly syntax, or the CLI's flags and output
       - a conformance vector's expected result
     "Internal only" is a valid answer. Say which internals.

     Only this side asks for prose, and the asymmetry is deliberate. A
     breaking claim is self-limiting: it routes the work to `dev` and accepts
     the wait, so nobody makes it carelessly. A non-breaking claim is the
     cheap one -- one tick, and the change is in the next patch release that
     every consumer pinned to `0.x.y` picks up without reading anything. Two
     or three lines is what turns that tick into something a reviewer can
     disagree with. -->

### If breaking

- [ ] Atomic spec-MR: all four layers, or an `Atomic-Spec-Exempt:` trailer
- [ ] Migration guide entry noted for the next breaking release, if this change needs one

## Checklist

### Preflight Checks
Run locally what CI enforces. Mark a language N/A if this MR does not touch it.
- [ ] `make preflight-rs` passes -- fmt, taplo, clippy, rustdoc, deny, unit/integration/doc tests (or N/A)
- [ ] `make preflight-py` passes -- taplo, ruff format + lint, pytest, uv.lock freshness (or N/A)
- [ ] `make preflight-parity` passes -- opcode parity, conformance, example smoke (if opcode or VM semantics changed)
- [ ] `make preflight-docs` passes -- generated-doc freshness, docs drift, README length guards (if `docs/book/` or a generated doc source changed)
- [ ] `make preflight-policy` passes -- changelog render, release-notes scoping, containment, branch version, atomic spec-MR and commit-message guards
- [ ] Lockfiles (`Cargo.lock`, `uv.lock`) are regenerated and committed if a dependency or version changed

### Optional Checks
- [ ] `make test-miri` passes -- run if the MR adds or changes `unsafe` code (not a CI gate)
- [ ] `make test-quip` passes -- run if the MR changes SolverQuip (`xqsa/quip*.py`); needs a running Quip devnet or testnet -- see `docs/guide/solverquip-testing.md`

### Commits & Documentation
- [ ] This MR's title follows Conventional Commits (`<type>[(scope)][!]: <description>`) -- enforced by `verify:policy`, and by the title pattern on the MR form
- [ ] Commit subject lines are 72 characters or fewer and use the imperative mood
- [ ] Commits are signed off (`git commit -s`)
- [ ] All public items are documented

### License Compliance Checklist
- [ ] All new files include the required AGPL-3.0-or-later license header
- [ ] Any new dependencies are AGPL-3.0-or-later compatible
- [ ] Any new Python dependencies (`pyproject.toml` / `uv.lock`) are AGPL-3.0-or-later compatible
- [ ] NOTICE file updated if adding AGPL-3.0-or-later compatible dependencies.
- [ ] `deny.toml` updated if the license is not listed and it's compatible with AGPL-3.0-or-later
- [ ] No proprietary or incompatible code was incorporated
