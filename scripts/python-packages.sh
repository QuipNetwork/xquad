#!/usr/bin/env bash
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# The xquad Python distribution set.
#
# Sourced by scripts/python-dists.sh (which builds, verifies and publishes
# them) and scripts/smoke-wheels.sh (which opens, installs and imports what
# that produced), so the two cannot disagree about what "every package"
# means. The smoke test is the guard QUI-1020 added; a package missing from
# its list is a package with no guard at all, and nothing would say so.
#
# Adding or renaming a distribution is a one-line edit here and nowhere else.
#
# Not standalone: `source` it, don't execute it directly.

# Every name below is read by the sourcing script, never by this file.
# shellcheck disable=SC2034

# The pyo3 cdylib. maturin builds it -- abi3 wheels for linux-x86_64 and
# linux-aarch64 plus a source dist -- rather than hatchling, so build,
# install and layout each special-case it. Named on its own rather than
# folded into PEERS for that reason.
CDYLIB=xqffi

# The pure-Python peers and the umbrella, in dependency order with the
# umbrella last. Publishing walks this list and a package must never be live
# on PyPI before the packages it requires; the smoke test installs in the
# same order, for the same reason.
PEERS=(xqvm_py xqcp xqsa xquad)

# Every distribution, cdylib first: the peers all depend on it.
PACKAGES=("${CDYLIB}" "${PEERS[@]}")
