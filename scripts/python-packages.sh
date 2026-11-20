#!/usr/bin/env bash
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# The xquad Python distribution and the import packages it carries.
#
# Sourced by scripts/python-dists.sh (which builds, verifies and publishes
# it) and scripts/smoke-wheels.sh (which opens, installs and imports what
# that produced), so the two cannot disagree about what the wheel holds.
# scripts/check-readme-length.sh reads PACKAGES too, for its scope.
# The smoke test is the guard QUI-1020 added; a package missing from its
# list is a package with no guard at all, and nothing would say so.
#
# Adding or renaming an import package also touches the root
# pyproject.toml (`python-packages`, ruff's `known-first-party`, pytest's
# `testpaths`), the test-py path list in the Makefile, which overrides
# `testpaths`, and, for a package the content guard names, the required
# members in scripts/python-dists.sh.
#
# Not standalone: `source` it, don't execute it directly.

# Every name below is read by the sourcing script, never by this file.
# shellcheck disable=SC2034

# The one PyPI distribution. maturin builds it from the root pyproject.toml:
# abi3 wheels for linux-x86_64 and linux-aarch64 plus a source dist, all in
# dist/ at the repo root.
DIST=xquad

# The import package that carries the compiled extension (`xqffi.xqffi`),
# its type stubs and its PEP 561 marker. Named on its own because it is the
# one package allowed files that are not Python source.
NATIVE=xqffi

# Every import package in the wheel, the native one first.
PACKAGES=("${NATIVE}" xqcp xqsa xquad)
