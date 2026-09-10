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
Tests for the V14 metadata shim (xqsa.quip_metadata).

The shim is exercised over a stub interface rather than ``substrate-interface``:
it never imports the library itself (the base class is passed in), so these run
everywhere, without the ``[quip]`` extra and without a chain. ``scalecodec`` is
stubbed for the same reason -- the real V14 decode is covered live by
``test_quip_live.py``.

Two stubs matter: one that serves ``Metadata_metadata_at_version`` and one that
does not, covering the runtime-API path, the ``state_getMetadata`` fallback, and
the error raised when neither yields decodable metadata. A third exercises the
transactional ``init_runtime`` override by reproducing the library's
assign-then-fetch order.

The runtime-API fixtures are encoded as real ``Option<Vec<u8>>`` values and the
stub runtime config decodes them, so the envelope the shim insists on decoding
whole -- discriminator, compact length, payload -- is actually under test rather
than asserted about a decoder that ignores its input.
"""

from __future__ import annotations

import sys
import types

import pytest

from xqsa.quip_codec import QuipMetadataError
from xqsa.quip_metadata import (
    METADATA_AT_VERSION_API,
    TARGET_METADATA_VERSION,
    v14_interface_class,
)

# A V14 metadata blob as the runtime API would return it, and the decoded object
# the stub runtime config yields for it. Contents are opaque to the shim.
V14_BLOB = "0x6d6574610e00"
STOCK_METADATA = object()

# `state_getMetadata` blobs: 4-byte "meta" magic then the MetadataAll variant
# index, which is the metadata version.
METADATA_V16_BLOB = "0x6d657461" + "10" + "deadbeef"
METADATA_V14_BLOB = "0x6d657461" + "0e" + "deadbeef"


def _encode_option_vec_u8(blob: str | None) -> str:
    """SCALE-encode ``Option<Vec<u8>>`` over a hex payload, as the runtime does.

    ``Some`` is the 0x01 discriminator, then the vector's compact-encoded length,
    then the bytes. Only single-byte compact lengths (under 64 bytes) are
    supported, which covers every fixture here.
    """
    if blob is None:
        return "0x00"
    body = bytes.fromhex(blob.removeprefix("0x"))
    if len(body) >= 64:
        raise ValueError("fixture payloads must be under 64 bytes (single-byte compact length)")
    return "0x01" + bytes([len(body) << 2]).hex() + body.hex()


def _decode_option_vec_u8(payload: str) -> str | None:
    """Decode ``Option<Vec<u8>>`` the way ``scalecodec`` would, and as strictly.

    The point is to fail on the same inputs the real decoder fails on: a payload
    that has had its option byte or its length prefix stripped off first must not
    quietly pass here, because the shim's whole-envelope contract is what these
    tests exist to pin.
    """
    raw = bytes.fromhex(payload.removeprefix("0x"))
    if not raw:
        raise ValueError("empty Option<Vec<u8>> payload")
    if raw[0] == 0x00:
        return None
    if raw[0] != 0x01:
        raise ValueError(f"0x{raw[0]:02x} is not an Option discriminator")
    if len(raw) < 2:
        raise ValueError("Option::Some without a Vec<u8> length prefix")
    if raw[1] & 0b11 != 0b00:
        raise ValueError("fixture payloads use single-byte compact lengths only")
    length, body = raw[1] >> 2, raw[2:]
    if len(body) != length:
        raise ValueError(f"Vec<u8> declares {length} bytes but carries {len(body)}")
    return "0x" + body.hex()


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _stub_scalecodec(monkeypatch) -> None:
    """Make ``from scalecodec.base import ScaleBytes`` resolve to an identity wrapper.

    The shim only uses ``ScaleBytes`` to hand a hex payload to
    ``runtime_config.create_scale_object``, and the stub runtime config below
    decodes that hex itself, so passing it through unchanged is enough.
    """
    base = types.ModuleType("scalecodec.base")
    base.ScaleBytes = lambda data: data  # type: ignore[attr-defined]
    package = types.ModuleType("scalecodec")
    package.base = base  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "scalecodec", package)
    monkeypatch.setitem(sys.modules, "scalecodec.base", base)


class _ScaleObject:
    """A SCALE object whose ``decode()`` returns a preset value."""

    def __init__(self, decoded: object) -> None:
        self._decoded = decoded

    def decode(self) -> object:
        return self._decoded


class _RuntimeConfig:
    """Serves ``Option<Vec<u8>>`` and ``MetadataVersioned`` objects for the shim.

    ``Option<Vec<u8>>`` is decoded from the payload it is actually handed, so a
    shim that pre-strips the option byte or the length prefix fails here the way
    it would against ``scalecodec``. ``received`` records every request, which is
    how a test asserts what reached the decoder.
    """

    def __init__(self, *, decode_raises: Exception | None = None) -> None:
        self._decode_raises = decode_raises
        self.received: list[tuple[str, object]] = []

    def create_scale_object(self, type_string: str, data: object) -> _ScaleObject:
        self.received.append((type_string, data))
        if type_string == "Option<Vec<u8>>":
            return _ScaleObject(_decode_option_vec_u8(str(data)))
        if self._decode_raises is not None:
            raise self._decode_raises
        return _ScaleObject(None)  # MetadataVersioned: decode() mutates, the object is the result.


class _StubInterface:
    """Stands in for ``SubstrateInterface``: canned RPC replies, recorded calls.

    ``v14`` is the inner blob the runtime API serves (``None`` for a runtime that
    offers no V14). ``state_call_error`` makes the runtime API absent -- either
    by raising (an older node that does not implement it) or by returning a
    JSON-RPC error object. ``served`` is what stock ``state_getMetadata``
    returns, and ``stock_raises`` is how that stock path fails.
    """

    url = "ws://stub:9944"

    def __init__(
        self,
        *,
        v14: str | None = V14_BLOB,
        state_call_error: Exception | str | None = None,
        served: str | None = METADATA_V16_BLOB,
        stock_raises: Exception | None = None,
        decode_raises: Exception | None = None,
    ) -> None:
        self.runtime_config = _RuntimeConfig(decode_raises=decode_raises)
        self._v14 = v14
        self._state_call_error = state_call_error
        self._served = served
        self._stock_raises = stock_raises
        self.calls: list[tuple[str, list]] = []

    def rpc_request(self, method: str, params: list) -> dict:
        self.calls.append((method, params))
        if method == "state_call":
            if isinstance(self._state_call_error, Exception):
                raise self._state_call_error
            if self._state_call_error is not None:
                return {"error": {"message": self._state_call_error}}
            return {"result": _encode_option_vec_u8(self._v14)}
        if method == "state_getMetadata":
            if self._served is None:
                raise ConnectionError("state_getMetadata unavailable")
            return {"result": self._served}
        raise AssertionError(f"unexpected RPC {method}")

    def get_block_metadata(self, block_hash: str | None = None, decode: bool = True) -> object:
        """The stock path the shim falls back to."""
        self.calls.append(("get_block_metadata", [block_hash, decode]))
        if self._stock_raises is not None:
            raise self._stock_raises
        return STOCK_METADATA


def _shimmed(**kwargs) -> _StubInterface:
    """Build a shimmed stub: the mixin over :class:`_StubInterface`."""
    return v14_interface_class(_StubInterface)(**kwargs)


def _methods(iface: _StubInterface) -> list[str]:
    return [name for name, _ in iface.calls]


# ---------------------------------------------------------------------------
# The V14 runtime-API path
# ---------------------------------------------------------------------------


class TestVersionedRuntimeApi:
    """A node that serves ``Metadata_metadata_at_version`` never hits the stock path."""

    def test_metadata_comes_from_the_runtime_api(self) -> None:
        iface = _shimmed()
        metadata = iface.get_block_metadata()
        assert isinstance(metadata, _ScaleObject)
        assert _methods(iface) == ["state_call"]

    def test_requests_version_14_as_a_little_endian_u32(self) -> None:
        iface = _shimmed()
        iface.get_block_metadata()
        assert iface.calls[0] == ("state_call", [METADATA_AT_VERSION_API, "0x0e000000"])
        assert TARGET_METADATA_VERSION == 14

    def test_block_hash_is_forwarded_to_state_call(self) -> None:
        iface = _shimmed()
        iface.get_block_metadata(block_hash="0xabc")
        assert iface.calls[0][1][2] == "0xabc"

    def test_decode_false_delegates_to_the_stock_path(self) -> None:
        # Undecoded, the caller is asking for what the node serves, and stock
        # answers with the whole JSON-RPC response. Routing it through the V14
        # path would return a hex blob instead, making the type depend on which
        # node answered.
        iface = _shimmed()
        assert iface.get_block_metadata(decode=False) is STOCK_METADATA
        assert _methods(iface) == ["get_block_metadata"]

    def test_the_whole_option_envelope_reaches_the_decoder(self) -> None:
        # The shim decodes `Option<Vec<u8>>` over the untouched payload. Stripping
        # the option byte or the compact length by hand first double-strips and
        # fails with a nonsense demand for ~1e43 more bytes, so what is handed to
        # the decoder is the contract worth pinning.
        iface = _shimmed()
        iface.get_block_metadata()
        assert iface.runtime_config.received == [
            ("Option<Vec<u8>>", "0x01186d6574610e00"),
            ("MetadataVersioned", V14_BLOB),
        ]

    def test_undecodable_v14_blob_raises_rather_than_falling_back(self) -> None:
        # The runtime answered, so falling back would hide a real decode bug.
        iface = _shimmed(decode_raises=ValueError("bad blob"))
        with pytest.raises(QuipMetadataError, match="does not decode as MetadataVersioned"):
            iface.get_block_metadata()
        assert "get_block_metadata" not in _methods(iface)


# ---------------------------------------------------------------------------
# The state_getMetadata fallback
# ---------------------------------------------------------------------------


class TestStockFallback:
    """Nodes without the versioned runtime API keep working (older DevNet images)."""

    def test_falls_back_when_state_call_raises(self) -> None:
        iface = _shimmed(state_call_error=ConnectionError("no such runtime API"))
        assert iface.get_block_metadata() is STOCK_METADATA
        assert _methods(iface) == ["state_call", "get_block_metadata"]

    def test_falls_back_on_a_json_rpc_error_response(self) -> None:
        iface = _shimmed(state_call_error="Method not found")
        assert iface.get_block_metadata() is STOCK_METADATA

    def test_falls_back_when_the_runtime_has_no_v14(self) -> None:
        # `Option::None`: the API exists but this runtime offers no V14.
        iface = _shimmed(v14=None)
        assert iface.get_block_metadata() is STOCK_METADATA

    def test_fallback_forwards_the_block_hash(self) -> None:
        iface = _shimmed(v14=None)
        iface.get_block_metadata(block_hash="0xabc")
        assert ("get_block_metadata", ["0xabc", True]) in iface.calls


# ---------------------------------------------------------------------------
# The error when neither path works
# ---------------------------------------------------------------------------


class TestUndecodableMetadata:
    """Both paths failing yields an error naming the version, not an enum index."""

    def test_names_the_served_metadata_version(self) -> None:
        iface = _shimmed(v14=None, stock_raises=ValueError("Index '16' not present in Enum type mapping"))
        with pytest.raises(QuipMetadataError, match=r"serves V16 runtime metadata") as excinfo:
            iface.get_block_metadata()
        assert "ws://stub:9944" in str(excinfo.value)
        assert METADATA_AT_VERSION_API in str(excinfo.value)

    def test_chains_the_underlying_decode_failure(self) -> None:
        cause = ValueError("Index '16' not present in Enum type mapping")
        iface = _shimmed(v14=None, stock_raises=cause)
        with pytest.raises(QuipMetadataError) as excinfo:
            iface.get_block_metadata()
        assert excinfo.value.__cause__ is cause

    def test_an_answered_runtime_api_with_no_v14_blames_the_runtime(self) -> None:
        # The runtime replied "Method not found": that IS evidence about the
        # runtime, so the message may say so.
        iface = _shimmed(state_call_error="Method not found", stock_raises=ValueError("boom"))
        with pytest.raises(QuipMetadataError, match="does not answer"):
            iface.get_block_metadata()

    def test_a_transport_fault_does_not_blame_the_runtime(self) -> None:
        # A dropped socket declines the V14 path the same way an absent runtime
        # API does, but it is a claim about the network. Saying "its runtime does
        # not answer Metadata_metadata_at_version(14)" here sends the reader after
        # a compatibility problem that may not exist.
        iface = _shimmed(
            state_call_error=ConnectionError("socket closed"),
            stock_raises=ValueError("boom"),
        )
        with pytest.raises(QuipMetadataError, match="could not be called") as excinfo:
            iface.get_block_metadata()
        message = str(excinfo.value)
        assert "socket closed" in message
        assert "does not answer" not in message

    def test_an_unreachable_node_is_not_reported_as_a_metadata_fault(self) -> None:
        # Every path failed and the node produced no metadata to inspect, so
        # there is nothing to support a claim about its metadata version. The
        # transport fault travels as itself, and SolverQuip turns it into the
        # QuipConnectionError whose remedy -- retry, check the URL -- applies;
        # QuipMetadataError is deliberately exempt from that conversion.
        outage = ConnectionError("socket closed")
        iface = _shimmed(state_call_error=outage, served=None, stock_raises=outage)
        with pytest.raises(ConnectionError, match="socket closed"):
            iface.get_block_metadata()

    def test_degrades_on_a_truncated_metadata_blob(self) -> None:
        # Shorter than the 4-byte magic number, so there is no version byte.
        iface = _shimmed(v14=None, served="0x6d65", stock_raises=ValueError("boom"))
        with pytest.raises(QuipMetadataError, match="unreadable version"):
            iface.get_block_metadata()

    def test_a_v14_serving_node_never_reaches_this_error(self) -> None:
        # Belt and braces: a node whose stock path works is reported verbatim.
        iface = _shimmed(v14=None, served=METADATA_V14_BLOB)
        assert iface.get_block_metadata() is STOCK_METADATA


# ---------------------------------------------------------------------------
# Transactional init_runtime
# ---------------------------------------------------------------------------


class _RuntimeStateStub:
    """Reproduces ``SubstrateInterface.init_runtime``'s assign-then-fetch order.

    The library records the chain's ``specVersion`` on the instance and only then
    fetches metadata, and a later call returns early once that version matches.
    A fetch that fails in between is therefore what leaves a client holding the
    previous runtime's metadata under the new runtime's version number.

    ``offline_until`` is the number of leading ``init_runtime`` attempts during
    which every RPC and the stock metadata path fault, so a test can fail once
    and then retry against a node that has come back.
    """

    SPEC_VERSION = 116
    url = "ws://stub:9944"

    def __init__(self, *, offline_until: int = 0) -> None:
        self.metadata = None
        self.runtime_version = None
        self.transaction_version = None
        self.block_hash = None
        self.block_id = None
        self.runtime_config = _RuntimeConfig()
        self._offline_until = offline_until
        self.attempts = 0
        self.calls: list[str] = []

    @property
    def _offline(self) -> bool:
        return self.attempts <= self._offline_until

    def init_runtime(self, block_hash: str | None = None, block_id: int | None = None) -> None:
        self.attempts += 1
        if self.runtime_version == self.SPEC_VERSION:
            return
        self.block_hash = block_hash or "0xhead"
        self.block_id = block_id
        self.runtime_version = self.SPEC_VERSION
        self.transaction_version = 1
        self.metadata = self.get_block_metadata(block_hash=self.block_hash, decode=True)

    def rpc_request(self, method: str, params: list) -> dict:
        self.calls.append(method)
        if self._offline:
            raise ConnectionError("socket closed")
        if method == "state_call":
            return {"result": _encode_option_vec_u8(V14_BLOB)}
        raise AssertionError(f"unexpected RPC {method}")

    def get_block_metadata(self, block_hash: str | None = None, decode: bool = True) -> object:
        """The stock path, reached only while the node is offline."""
        if self._offline:
            raise ConnectionError("socket closed")
        raise AssertionError("a reachable node is served by the V14 runtime API, not the stock path")


class TestTransactionalInitRuntime:
    """A failed metadata fetch must not leave the runtime version half-advanced."""

    def test_the_unshimmed_base_strands_stale_runtime_state(self) -> None:
        # Pins the library behaviour the override exists to correct: without it,
        # the retry below would short-circuit on the recorded version.
        base = _RuntimeStateStub(offline_until=1)
        with pytest.raises(ConnectionError):
            base.init_runtime()
        assert base.runtime_version == _RuntimeStateStub.SPEC_VERSION
        assert base.metadata is None

    def test_a_failed_fetch_rolls_the_runtime_state_back(self) -> None:
        iface = v14_interface_class(_RuntimeStateStub)(offline_until=1)
        with pytest.raises(ConnectionError, match="socket closed"):
            iface.init_runtime()
        assert iface.runtime_version is None
        assert iface.transaction_version is None
        assert iface.metadata is None
        assert iface.block_hash is None
        assert iface.block_id is None

    def test_a_retry_after_the_failure_fetches_metadata_again(self) -> None:
        iface = v14_interface_class(_RuntimeStateStub)(offline_until=1)
        with pytest.raises(ConnectionError):
            iface.init_runtime()
        iface.init_runtime()
        assert iface.runtime_version == _RuntimeStateStub.SPEC_VERSION
        assert isinstance(iface.metadata, _ScaleObject)
        assert "state_call" in iface.calls

    def test_a_clean_run_leaves_the_state_the_stock_call_set(self) -> None:
        iface = v14_interface_class(_RuntimeStateStub)()
        iface.init_runtime()
        assert iface.runtime_version == _RuntimeStateStub.SPEC_VERSION
        assert iface.block_hash == "0xhead"
        assert isinstance(iface.metadata, _ScaleObject)


# ---------------------------------------------------------------------------
# Isolation: the shim must not leak into other substrate-interface users
# ---------------------------------------------------------------------------


class TestIsolation:
    def test_the_base_class_is_left_untouched(self) -> None:
        before = _StubInterface.get_block_metadata
        shimmed = v14_interface_class(_StubInterface)
        assert _StubInterface.get_block_metadata is before
        assert shimmed is not _StubInterface
        assert issubclass(shimmed, _StubInterface)

    def test_an_unshimmed_instance_keeps_stock_behaviour(self) -> None:
        assert _StubInterface().get_block_metadata() is STOCK_METADATA

    def test_the_subclass_is_built_once_per_base(self) -> None:
        assert v14_interface_class(_StubInterface) is v14_interface_class(_StubInterface)
