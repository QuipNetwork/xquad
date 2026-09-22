# Copyright (C) 2026 Postquant Labs Incorporated
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Fund a Quip Network account from a devnet/testnet faucet.

The faucet is a small HTTP service alongside a Quip node's RPC endpoint. It
dispenses a fixed drip once a destination's on-chain balance is at or below
one drip, and rate-limits per destination. :func:`fund_from_faucet` wraps the
``POST <url>/request`` contract, including the one automatic retry on a
429 rate limit; a 403 (balance already above one drip) is never retried.
"""

from __future__ import annotations

import json
import logging
import os
import ssl
import time
import urllib.error
import urllib.request

from xqsa import quip_metadata
from xqsa.quip_codec import QuipError

logger = logging.getLogger("xqsa.quip.faucet")

# Faucet default dispense, 10 AGLS (12 decimals). Runtime/operator-dependent --
# a given faucet deployment may drip a different amount.
DEFAULT_DRIP_PLANCK = 10_000_000_000_000


class QuipFaucetError(QuipError):
    """Raised when a faucet funding request fails.

    Carries the HTTP status (``None`` for a transport-level failure) and the
    parsed JSON error body, so a caller can distinguish a rate limit, a
    balance-ceiling denial, and a malformed request from each other.
    """

    def __init__(self, status: int | None, body: dict, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


def _post_request(url: str, dest: str, amount: int | None, timeout: float) -> tuple[int, dict]:
    """Send one funding request to ``url``; return ``(status, body)``.

    Raises:
        QuipFaucetError: on a transport-level failure (``URLError``,
            ``TimeoutError``, or other ``OSError``), with ``status=None``.
    """
    payload: dict = {"dest": dest}
    if amount is not None:
        payload["amount"] = amount
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # Verifies TLS with the same CA default as xqsa.quip_metadata.connect, so
    # an https:// faucet works on macOS without an exported SSL_CERT_FILE.
    # ssl ignores WEBSOCKET_CLIENT_CA_BUNDLE, so it is honoured here explicitly.
    cafile = os.environ.get("WEBSOCKET_CLIENT_CA_BUNDLE") or quip_metadata._default_ca_bundle()
    context = ssl.create_default_context(cafile=cafile)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:  # noqa: S310 -- operator-supplied faucet URL
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {}
        return exc.code, body
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise QuipFaucetError(None, {}, f"faucet request to {url} failed: {exc}") from exc


def fund_from_faucet(dest: str, *, url: str, amount: int | None = None, timeout: float = 40.0) -> dict:
    """Request faucet funding for ``dest`` and return the drip's JSON body.

    Retries exactly once on a 429 rate limit, sleeping for the server's
    ``retry_after_seconds``. A 403 (the faucet only tops up accounts at or
    below one drip) is never retried.

    Args:
        dest: The destination account, as the faucet expects it (e.g. a
            ``0x``-prefixed hex account ID).
        url: The faucet's base URL; ``/request`` is appended.
        amount: Requested drip size in plancks. Omitted from the request body
            when ``None``, letting the faucet apply its own default.
        timeout: Per-attempt socket timeout in seconds.

    Returns:
        The faucet's parsed JSON response body on success.

    Raises:
        QuipFaucetError: on a 403, a 400, a second consecutive 429, any other
            non-2xx status, or a transport-level failure.
    """
    request_url = url.rstrip("/") + "/request"
    status, body = _post_request(request_url, dest, amount, timeout)

    if status == 429:
        retry_after = float(body.get("retry_after_seconds", 5))
        logger.debug("faucet %s rate limited %s, retrying after %ss", request_url, dest, retry_after)
        time.sleep(retry_after)
        status, body = _post_request(request_url, dest, amount, timeout)

    if 200 <= status < 300:
        logger.info("faucet %s funded %s: %s planck", request_url, dest, body.get("amount"))
        return body

    if status == 403:
        balance = body.get("free_balance_plancks")
        suffix = f" (free_balance_plancks={balance})" if balance is not None else ""
        raise QuipFaucetError(
            status,
            body,
            f"faucet {request_url} refused {dest}: already funded above one drip{suffix}",
        )

    error = body.get("error", f"HTTP {status}")
    raise QuipFaucetError(status, body, f"faucet {request_url} refused {dest}: {error}")
