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
Metadata compatibility shim for the Quip Network chain client.

Quip runtimes from ``specVersion`` 116 onward serve Metadata **V16** from
``state_getMetadata``. ``substrate-interface`` decodes through ``scalecodec``,
whose ``MetadataAll`` enum ends at V14, so ``SubstrateInterface.init_runtime``
fails with a bare ``ValueError: Index '16' not present in Enum type mapping``
before any xqsa code runs. No release of either library decodes V16 today, so
this is not a dependency bump: it is a client-side wall that needs a shim.

The runtime still serves older metadata through the versioned runtime API, so
this module fetches V14 explicitly via
``state_call("Metadata_metadata_at_version", 14)`` and falls back to the stock
``state_getMetadata`` for nodes that predate that API. ``init_runtime`` reaches
metadata through exactly one method, ``SubstrateInterface.get_block_metadata``,
so the fetch itself is one override. A second override makes ``init_runtime``
transactional, because the stock one assigns the new runtime version before it
fetches metadata and would otherwise strand a client on stale metadata when the
fetch fails. Both are mixed into a subclass built per ``SubstrateInterface``
class by :func:`v14_interface_class`. Nothing global is patched: an unrelated
``substrate-interface`` user in the same process keeps the stock behaviour.

``substrate-interface`` is imported lazily (by :func:`connect`, or by the caller
that passes the base class to :func:`v14_interface_class`), so importing this
module never requires the ``[quip]`` extra.

Usage::

    from xqsa.quip_metadata import connect

    iface = connect("wss://bootnode-1.aglais.quip.network:20049/rpc")
    iface.init_runtime()

The shim depends on the runtime continuing to answer
``Metadata_metadata_at_version(14)``. If a future runtime drops V14, every
``substrate-interface`` client in the stack breaks at once and a migration to an
async client becomes forced; :class:`~xqsa.quip_codec.QuipMetadataError` is what
reports that day, naming the version the node actually serves.
"""

from __future__ import annotations

import functools
import logging
import os
import sys
from collections.abc import Mapping
from typing import Any

from xqsa.quip_codec import QuipMetadataError

__all__ = ["METADATA_AT_VERSION_API", "TARGET_METADATA_VERSION", "connect", "v14_interface_class"]

logger = logging.getLogger(__name__)

# The runtime API entry point that serves metadata at a caller-chosen version,
# and the version we ask it for: the newest one `scalecodec` 1.2.x decodes.
METADATA_AT_VERSION_API = "Metadata_metadata_at_version"
TARGET_METADATA_VERSION = 14

# The runtime API takes a SCALE-encoded u32, which for a plain u32 is just the
# little-endian bytes (0x0e000000 for 14).
_TARGET_VERSION_ARG = "0x" + TARGET_METADATA_VERSION.to_bytes(4, "little").hex()

# `MetadataVersioned` is a u32 magic number followed by the `MetadataAll` enum,
# whose variant index is the metadata version. Reading byte 4 of the raw
# `state_getMetadata` blob names the version without a second runtime API call.
_MAGIC_NUMBER_BYTES = 4

# The runtime state `SubstrateInterface.init_runtime` assigns before it fetches
# metadata. `block_hash`/`block_id` and `runtime_version` each guard one of its
# early returns, so a half-finished call leaves the next one convinced there is
# nothing to do -- with the new version recorded beside the old metadata.
_RUNTIME_STATE_ATTRS = ("block_hash", "block_id", "runtime_version", "transaction_version", "metadata")


class _V14MetadataMixin:
    """Fetches chain metadata at V14 through the versioned runtime API.

    Mixed ahead of a ``SubstrateInterface`` base by :func:`v14_interface_class`.
    Overrides ``get_block_metadata`` and ``init_runtime``; every other method is
    the stock one.
    """

    def init_runtime(self, block_hash: str | None = None, block_id: int | None = None) -> Any:
        """Run the stock ``init_runtime``, rolling its state back if it fails.

        The stock implementation assigns ``block_hash``, ``block_id``,
        ``runtime_version`` and ``transaction_version`` and only then fetches
        metadata. If the fetch raises -- a dropped socket mid-upgrade, or this
        shim rejecting a version it cannot decode -- the instance is left
        claiming the new runtime version while still holding the previous
        runtime's metadata. The next call matches that version against the
        chain's, returns early, and every storage read afterwards is decoded
        against metadata that no longer describes the runtime: silent wrong
        answers rather than an error.

        Restoring the snapshot makes the call all-or-nothing, so a retry after a
        transient failure re-fetches instead of short-circuiting.
        """
        snapshot = {name: getattr(self, name, None) for name in _RUNTIME_STATE_ATTRS}
        try:
            return super().init_runtime(block_hash=block_hash, block_id=block_id)  # type: ignore[misc]
        except Exception:
            for name, value in snapshot.items():
                setattr(self, name, value)
            raise

    def get_block_metadata(self, block_hash: str | None = None, decode: bool = True) -> Any:
        """Return the block's metadata, preferring the V14 runtime API.

        Falls back to the stock ``state_getMetadata`` when the runtime does not
        offer V14, so nodes predating the versioned runtime API keep working.

        ``decode=False`` is left to the stock implementation entirely. Undecoded,
        the caller is asking for what the node serves, and the stock method
        answers with the whole JSON-RPC response rather than a blob -- returning
        a V14 hex string here instead would make the return type depend on which
        node answered. Only ``init_runtime`` consumes this method, and only with
        ``decode=True``.

        Raises:
            QuipMetadataError: if the node serves metadata neither path can
                decode. A node that serves no metadata at all is a different
                fault and its exception travels unchanged; see below.
        """
        if not decode:
            return super().get_block_metadata(block_hash=block_hash, decode=False)  # type: ignore[misc]

        raw, fault = self._raw_v14_metadata(block_hash)
        if raw is not None:
            return self._decode_versioned(raw)

        try:
            return super().get_block_metadata(block_hash=block_hash, decode=True)  # type: ignore[misc]
        except Exception as exc:
            blob = self._served_metadata_blob(block_hash)
            if blob is None:
                # Both paths failed and the node handed over no metadata to
                # look at, so there is no evidence its metadata version is the
                # problem -- the likeliest reading is that it is not answering
                # at all. QuipMetadataError would misname that and, because
                # SolverQuip deliberately lets that type through its connection
                # handler, would also deny the caller the remedy that actually
                # applies. Let the transport fault travel as itself.
                raise
            raise QuipMetadataError(self._undecodable_metadata_message(blob, fault)) from exc

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _raw_v14_metadata(self, block_hash: str | None) -> tuple[str | None, Exception | None]:
        """Return the V14 metadata blob, or ``None`` to fall back to the stock path.

        ``None`` covers every way the runtime API can decline: the node does not
        implement it, it implements it but has no V14 to give (``Option::None``),
        or the call itself faults. Any of those means the stock
        ``state_getMetadata`` is the only remaining source, and letting it try is
        strictly better than failing here -- if it also fails, the caller turns
        that into a :class:`QuipMetadataError`.

        The second element separates the two: an exception when the call could
        not be made or its answer not read, ``None`` when the runtime answered
        and simply had no V14. Only the latter licenses the error message to say
        the runtime does not serve V14; a dropped socket is a claim about the
        network, not the chain.
        """
        params: list[Any] = [METADATA_AT_VERSION_API, _TARGET_VERSION_ARG]
        if block_hash:
            params.append(block_hash)

        try:
            response = self.rpc_request("state_call", params)  # type: ignore[attr-defined]
            if not isinstance(response, Mapping) or response.get("error"):
                return None, None
            result = response.get("result")
            if not result:
                return None, None
            # Decode the WHOLE payload as `Option<Vec<u8>>`: the option byte and
            # the compact length prefix are part of that encoding. Stripping
            # either by hand first and then decoding double-strips, and fails
            # with a nonsense demand for ~1e43 more bytes.
            option = self._scale_object("Option<Vec<u8>>", result)
            return option.decode() or None, None
        except Exception as exc:  # noqa: BLE001 -- any fault here means "fall back", not "fail".
            logger.debug(
                "%s(%d) unavailable, falling back to state_getMetadata: %s",
                METADATA_AT_VERSION_API,
                TARGET_METADATA_VERSION,
                exc,
            )
            return None, exc

    def _decode_versioned(self, raw: str) -> Any:
        """Decode a raw ``MetadataVersioned`` blob.

        Raises:
            QuipMetadataError: if the V14 blob the runtime returned does not decode.
        """
        try:
            metadata = self._scale_object("MetadataVersioned", raw)
            metadata.decode()
            return metadata
        except Exception as exc:
            raise QuipMetadataError(
                f"the Quip node returned metadata for {METADATA_AT_VERSION_API}"
                f"({TARGET_METADATA_VERSION}) that does not decode as MetadataVersioned: {exc}"
            ) from exc

    def _scale_object(self, type_string: str, data: str) -> Any:
        """Build a SCALE object of ``type_string`` over the hex payload ``data``."""
        from scalecodec.base import ScaleBytes

        return self.runtime_config.create_scale_object(type_string, data=ScaleBytes(data))  # type: ignore[attr-defined]

    def _undecodable_metadata_message(self, blob: bytes, fault: Exception | None) -> str:
        """Explain the double failure, naming the metadata version the node serves.

        ``blob`` is the raw ``state_getMetadata`` payload the node did return,
        which is what licenses this message to talk about the node's metadata at
        all. ``fault`` is the exception that stopped the V14 runtime-API call, or
        ``None`` if the runtime answered it and had no V14. The two failures have
        different remedies, so they get different sentences: only the second is
        evidence about the runtime.
        """
        served = self._served_metadata_version(blob)
        version = f"V{served}" if served is not None else "an unreadable version of"
        url = getattr(self, "url", None) or "the configured RPC URL"
        preamble = (
            f"the Quip node at {url} serves {version} runtime metadata, which the installed "
            f"substrate-interface/scalecodec cannot decode (they stop at V{TARGET_METADATA_VERSION}), "
        )
        if fault is not None:
            return (
                f"{preamble}and {METADATA_AT_VERSION_API}({TARGET_METADATA_VERSION}) could not be "
                f"called to get a version that decodes: {fault}. Check connectivity to the node "
                f"before concluding its runtime is incompatible."
            )
        return (
            f"{preamble}and its runtime does not answer "
            f"{METADATA_AT_VERSION_API}({TARGET_METADATA_VERSION}). "
            f"SolverQuip needs a runtime that still exposes metadata V{TARGET_METADATA_VERSION}."
        )

    def _served_metadata_blob(self, block_hash: str | None) -> bytes | None:
        """Best-effort fetch of the raw blob ``state_getMetadata`` serves.

        ``None`` means the node produced no blob to inspect: the RPC faulted, or
        its answer carried no readable result. That is a statement about the node
        being reachable, not about the chain's metadata, and
        :meth:`get_block_metadata` turns on the difference.
        """
        try:
            response = self.rpc_request("state_getMetadata", [block_hash] if block_hash else [])  # type: ignore[attr-defined]
            result = response["result"]
            return bytes.fromhex(result[2:] if result.startswith("0x") else result)
        except Exception:  # noqa: BLE001 -- the caller is already reporting a failure.
            return None

    @staticmethod
    def _served_metadata_version(blob: bytes) -> int | None:
        """Read the metadata version out of a ``MetadataVersioned`` blob.

        Returns ``None`` when the blob is too short to carry a version byte, so
        the error message degrades rather than masking itself.
        """
        if len(blob) <= _MAGIC_NUMBER_BYTES:
            return None
        return blob[_MAGIC_NUMBER_BYTES]


@functools.cache
def v14_interface_class(base: type) -> type:
    """Return a ``base`` subclass that decodes chain metadata at V14.

    ``base`` is the ``SubstrateInterface`` class to specialize, passed in rather
    than imported so this module stays free of the optional ``[quip]`` extra.
    Cached, so repeated calls for one base yield one class.
    """
    return type(f"V14{base.__name__}", (_V14MetadataMixin, base), {})


_CA_ENV_VARS = ("SSL_CERT_FILE", "SSL_CERT_DIR", "WEBSOCKET_CLIENT_CA_BUNDLE")


def _default_ca_bundle() -> str | None:
    """Return certifi's CA bundle path where TLS would otherwise have none.

    python.org and uv-managed Pythons on macOS ship no CA bundle, so a TLS
    handshake fails without one. Returns ``None`` on every other platform, whose
    system trust store must keep applying, and whenever the user configured CAs
    through ``SSL_CERT_FILE``, ``SSL_CERT_DIR`` or ``WEBSOCKET_CLIENT_CA_BUNDLE``.

    Raises:
        ImportError: if ``certifi`` is needed and not installed.
    """
    if sys.platform != "darwin" or any(os.environ.get(name) for name in _CA_ENV_VARS):
        return None
    import certifi

    return certifi.where()


def connect(url: str, **kwargs: Any) -> Any:
    """Open a ``SubstrateInterface`` to ``url`` that decodes metadata at V14.

    The shim lives on the returned instance's class alone; an unrelated
    ``substrate-interface`` client in the same process is untouched.

    On macOS, a ``wss://`` URL verifies TLS against certifi's CA bundle unless
    the caller passed ``ws_options`` (forwarded untouched) or configured CAs
    through ``SSL_CERT_FILE``, ``SSL_CERT_DIR`` or
    ``WEBSOCKET_CLIENT_CA_BUNDLE``. Other platforms keep their system trust
    store.

    Raises:
        ImportError: if ``substrate-interface`` is not installed, or ``certifi``
            is not installed and the certifi default applies.
    """
    import substrateinterface

    # websocket-client loads the system store only when no ca_certs is given, so
    # the bundle is supplied only where that store is empty (see _default_ca_bundle).
    if url.startswith("wss://") and "ws_options" not in kwargs and (bundle := _default_ca_bundle()):
        kwargs["ws_options"] = {"sslopt": {"ca_certs": bundle}}
    return v14_interface_class(substrateinterface.SubstrateInterface)(url=url, **kwargs)
