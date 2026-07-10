## Summary

<!-- Describe what this MR changes and why. Reference related issues with #issue-number. -->

## Type of Change
- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [ ] Documentation update

## Checklist

### Preflight Checks
Run locally what CI enforces. Mark a language N/A if this MR does not touch it.
- [ ] `make preflight-rs` passes -- fmt, taplo, clippy, rustdoc, deny, unit/integration/doc tests (or N/A)
- [ ] `make preflight-py` passes -- taplo, ruff format + lint, pytest (or N/A)
- [ ] `make preflight-parity` passes -- opcode parity, conformance, example smoke (if opcode or VM semantics changed)

### Optional Checks
- [ ] `make test-miri` passes -- run if the MR adds or changes `unsafe` code (not a CI gate)
- [ ] `make test-quip` passes -- run if the MR changes SolverQuip (`xqsa/quip*.py`); needs a running Quip devnet or testnet -- see `docs/solverquip-testing.md`

### Commits & Documentation
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
