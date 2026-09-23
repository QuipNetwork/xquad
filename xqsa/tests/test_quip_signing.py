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
Tests for the Quip Network extrinsic-assembly layer (xqsa.quip_signing).

These exercise our half of the contract -- keystore persistence, SCALE
SignedPayload composition (including the >256-byte blake2_256 rule), the v4
wire splice, the signed-extension extras order, and the pre-submit
``verify_envelope`` self-check. The crypto itself belongs to ``quip_signer``
(its own parity vectors), so it is not re-tested here.

The ``quip_signer`` extension ships in the ``[quip]`` extra, so the whole
module is marked ``quip`` and skipped when the binding is unimportable.
"""

from __future__ import annotations

import hashlib
import json
import stat

import pytest

quip_signer = pytest.importorskip(
    "quip_signer",
    reason="quip_signer extension not installed (run `uv sync --extra quip`)",
)

from xqsa import quip_signing
from xqsa.quip_signing import (
    EXTRINSIC_VERSION_SIGNED,
    HYBRID_ENVELOPE_LEN,
    HYBRID_PUBLIC_LEN,
    MULTI_ADDRESS_ID,
    ExtrinsicReceipt,
    QuipSigningError,
    build_signed_extrinsic,
    encode_compact_u32,
    encode_compact_u128,
    generate_keystore,
    load_keystore,
    load_or_generate_keystore,
    signer_from_seed,
    submit_and_watch,
)

pytestmark = pytest.mark.quip

# A fixed seed keeps the derived account id / envelope deterministic per run.
SEED = bytes(range(32))
GENESIS = bytes([0xAB]) * 32


# ---------------------------------------------------------------------------
# Fake substrate-interface
# ---------------------------------------------------------------------------


class _FakeScaleBytes:
    """Mimics scalecodec ``ScaleBytes``: the payload lives at ``.data``."""

    def __init__(self, data: bytes) -> None:
        self.data = bytearray(data)


class _FakeCall:
    """Mimics a ``GenericCall`` from ``compose_call`` (``.data`` is ScaleBytes)."""

    def __init__(self, data: bytes) -> None:
        self.data = _FakeScaleBytes(data)


class _FakeExtrinsic:
    def __init__(self, extrinsic_hash: str) -> None:
        self.extrinsic_hash = extrinsic_hash


class FakeIface:
    """A minimal stand-in for the bits of SubstrateInterface we touch."""

    def __init__(
        self,
        *,
        call_bytes: bytes = b"\x09\x00",
        nonce: int = 0,
        genesis: bytes = GENESIS,
        spec_version: int = 1,
        tx_version: int = 1,
    ) -> None:
        self.call_bytes = call_bytes
        self.nonce = nonce
        self.genesis = genesis
        self.spec_version = spec_version
        self.tx_version = tx_version
        self.compose_calls: list[tuple] = []
        # Submission knobs configured per-test.
        self.watch_message: dict | None = None
        self.sent_response: dict = {"result": "0x" + "11" * 32}
        self.block: dict | None = None
        self.events: list = []
        self.unwatched: list = []

    # --- assembly reads ---

    def compose_call(self, call_module, call_function, call_params):
        self.compose_calls.append((call_module, call_function, call_params))
        return _FakeCall(self.call_bytes)

    def get_account_nonce(self, account_address):
        return self.nonce

    def get_block_hash(self, block_id):
        assert block_id == 0
        return "0x" + self.genesis.hex()

    # --- submission ---

    def rpc_request(self, method, params, result_handler=None):
        if method == "state_getRuntimeVersion":
            return {"result": {"specVersion": self.spec_version, "transactionVersion": self.tx_version}}
        if method == "author_submitExtrinsic":
            return self.sent_response
        if method == "author_submitAndWatchExtrinsic":
            assert result_handler is not None
            return result_handler(self.watch_message, 1, "sub-1")
        if method == "author_unwatchExtrinsic":
            self.unwatched.append(params)
            return {"result": True}
        raise AssertionError(f"unexpected rpc_request method: {method}")

    def get_block(self, block_hash, include_author, ignore_decoding_errors):
        return self.block

    def get_events(self, block_hash):
        return self.events


def _decode_wire(wire: bytes) -> tuple[bytes, bytes, bytes]:
    """Split a wire extrinsic into ``(account, envelope, extra_plus_call)``.

    Strips the leading ``Compact<u32>`` length prefix and verifies it matches
    the body length, then peels the v4 signed header.
    """
    first = wire[0]
    mode = first & 0b11
    prefix_len = {0: 1, 1: 2, 2: 4}[mode]
    body = wire[prefix_len:]
    assert encode_compact_u32(len(body)) == wire[:prefix_len]
    assert body[0] == EXTRINSIC_VERSION_SIGNED
    assert body[1] == MULTI_ADDRESS_ID
    account = body[2:34]
    envelope = body[34 : 34 + HYBRID_ENVELOPE_LEN]
    rest = body[34 + HYBRID_ENVELOPE_LEN :]
    return account, envelope, rest


# ---------------------------------------------------------------------------
# Compact encoders
# ---------------------------------------------------------------------------


class TestCompactEncoders:
    def test_u32_single_byte_mode(self) -> None:
        assert encode_compact_u32(0) == b"\x00"
        assert encode_compact_u32(1) == b"\x04"
        assert encode_compact_u32(63) == bytes([63 << 2])

    def test_u32_two_byte_mode_boundary(self) -> None:
        # 0x40 is the first value that needs two bytes.
        assert encode_compact_u32(0x40) == (0x40 << 2 | 0b01).to_bytes(2, "little")
        assert encode_compact_u32(0x3FFF) == (0x3FFF << 2 | 0b01).to_bytes(2, "little")

    def test_u32_four_byte_mode_boundary(self) -> None:
        assert encode_compact_u32(0x4000) == (0x4000 << 2 | 0b10).to_bytes(4, "little")
        assert encode_compact_u32(0x3FFF_FFFF) == (0x3FFF_FFFF << 2 | 0b10).to_bytes(4, "little")

    def test_u32_big_integer_mode(self) -> None:
        # 2**30 <= value < 2**32 uses the SCALE big-int mode: mode byte 0x03
        # ((4 - 4) << 2 | 0b11) followed by four little-endian bytes.
        for value in (0x4000_0000, 0xFFFF_FFFF):
            encoded = encode_compact_u32(value)
            assert encoded == bytes([0x03]) + value.to_bytes(4, "little")
            assert int.from_bytes(encoded[1:], "little") == value  # round-trips

    def test_u32_rejects_negative_and_overflow(self) -> None:
        with pytest.raises(QuipSigningError):
            encode_compact_u32(-1)
        with pytest.raises(QuipSigningError, match="u32 range"):
            encode_compact_u32(1 << 32)

    def test_u128_matches_u32_in_small_range(self) -> None:
        for value in (0, 1, 63, 0x40, 0x3FFF, 0x4000, 0x3FFF_FFFF):
            assert encode_compact_u128(value) == encode_compact_u32(value)

    def test_u128_big_integer_mode(self) -> None:
        # 2^32 needs big-int mode: 4 raw little-endian bytes, mode byte 0b11.
        value = 1 << 32
        raw = value.to_bytes(5, "little")  # smallest byte width holding 2^32
        expected = bytes([((len(raw) - 4) << 2) | 0b11]) + raw
        assert encode_compact_u128(value) == expected

    def test_u128_rejects_negative(self) -> None:
        with pytest.raises(QuipSigningError):
            encode_compact_u128(-1)

    def test_u128_rejects_overflow(self) -> None:
        with pytest.raises(QuipSigningError, match="u128 range"):
            encode_compact_u128(1 << 128)


# ---------------------------------------------------------------------------
# Signed-extension extras order (mocked metadata)
# ---------------------------------------------------------------------------


class TestSignedExtensions:
    def test_extra_layout_for_zero_nonce(self) -> None:
        extra, _ = quip_signing._signed_extensions(nonce=0, spec_version=1, tx_version=1, genesis_bytes=GENESIS)
        # Era::Immortal(0x00) || CheckNonce compact(0)=0x00 || tip compact(0)=0x00
        # || CheckMetadataHash Mode::Disabled(0x00). Every other extension is empty.
        assert extra == b"\x00\x00\x00\x00"

    def test_extra_carries_nonce(self) -> None:
        extra, _ = quip_signing._signed_extensions(nonce=5, spec_version=1, tx_version=1, genesis_bytes=GENESIS)
        # Era || compact(5)=0x14 || tip 0x00 || metadata 0x00.
        assert extra == b"\x00" + encode_compact_u32(5) + b"\x00" + b"\x00"

    def test_additional_layout(self) -> None:
        _, additional = quip_signing._signed_extensions(nonce=0, spec_version=42, tx_version=7, genesis_bytes=GENESIS)
        # spec_version(4 LE) || tx_version(4 LE) || genesis(32) || genesis(32)
        # || CheckMetadataHash Option::None(0x00).
        expected = (42).to_bytes(4, "little") + (7).to_bytes(4, "little") + GENESIS + GENESIS + b"\x00"
        assert additional == expected
        assert len(additional) == 4 + 4 + 32 + 32 + 1

    def test_tip_encoded_in_extra(self) -> None:
        extra, _ = quip_signing._signed_extensions(
            nonce=0, spec_version=1, tx_version=1, genesis_bytes=GENESIS, tip=1000
        )
        assert extra == b"\x00" + b"\x00" + encode_compact_u128(1000) + b"\x00"

    def test_eth_set_origin_sits_between_metadata_hash_and_weight_reclaim(self) -> None:
        # QUI-1257: the live runtime lists 12 extensions, with EthSetOrigin
        # (pallet_revive's SetOrigin) in this slot. test_quip_live.py asserts the
        # whole tuple against chain metadata; this pins the position offline.
        order = quip_signing.SIGNED_EXTENSIONS
        assert order.index("EthSetOrigin") == order.index("CheckMetadataHash") + 1
        assert order.index("WeightReclaim") == order.index("EthSetOrigin") + 1
        assert len(order) == 12

    def test_the_field_table_matches_the_declared_order(self) -> None:
        # SIGNED_EXTENSIONS is the declared order; _extension_fields is what is
        # actually encoded. A name in one and not the other fails at signing time
        # with a bare KeyError, which is exactly the drift the live test above
        # prompts someone to fix.
        fields = quip_signing._extension_fields(nonce=0, spec_version=117, tx_version=7, genesis_bytes=GENESIS)
        assert tuple(fields) == quip_signing.SIGNED_EXTENSIONS

    def test_eth_set_origin_contributes_no_wire_bytes(self) -> None:
        # It is a zero-field composite with an empty additional_signed, so adding
        # it changes no payload -- it is a latent hazard, not a live bug.
        extra, additional = quip_signing._signed_extensions(
            nonce=3, spec_version=117, tx_version=7, genesis_bytes=GENESIS
        )
        assert len(extra) == 4
        assert len(additional) == 4 + 4 + 32 + 32 + 1


# ---------------------------------------------------------------------------
# Keystore
# ---------------------------------------------------------------------------


class TestKeystore:
    def test_generate_then_load_round_trip(self, tmp_path) -> None:
        path = tmp_path / "keystore.json"
        created = generate_keystore(path)
        loaded = load_keystore(path)
        assert created.seed == loaded.seed
        assert loaded.account_id == loaded.signer.account_id
        assert created.signer.account_id == loaded.signer.account_id

    def test_generated_file_is_0600(self, tmp_path) -> None:
        path = tmp_path / "keystore.json"
        generate_keystore(path)
        assert stat.S_IMODE(path.stat().st_mode) == quip_signing.KEYSTORE_FILE_MODE

    def test_generate_refuses_overwrite_by_default(self, tmp_path) -> None:
        path = tmp_path / "keystore.json"
        generate_keystore(path)
        with pytest.raises(QuipSigningError, match="already exists"):
            generate_keystore(path)
        # Explicit overwrite replaces it with a fresh seed.
        first = load_keystore(path).seed
        second = generate_keystore(path, overwrite=True).seed
        assert first != second

    def test_load_or_generate_creates_then_reuses(self, tmp_path) -> None:
        path = tmp_path / "nested" / "keystore.json"
        first = load_or_generate_keystore(path)
        assert path.exists()
        second = load_or_generate_keystore(path)
        assert first.seed == second.seed

    def test_persisted_fields(self, tmp_path) -> None:
        path = tmp_path / "keystore.json"
        ks = generate_keystore(path)
        raw = json.loads(path.read_text())
        assert raw["version"] == quip_signing.KEYSTORE_VERSION
        assert raw["scheme"] == quip_signing.KEYSTORE_SCHEME
        assert raw["encrypted"] is False
        assert bytes.fromhex(raw["master_seed_hex"][2:]) == ks.seed
        assert bytes.fromhex(raw["account_id_hex"][2:]) == ks.signer.account_id
        assert bytes.fromhex(raw["public_key_hex"][2:]) == ks.signer.public_key

    def test_h3_keystore_rejected_with_regeneration_guidance(self, tmp_path) -> None:
        # An H3 keystore must be told it needs regenerating for H4, not accused
        # of tampering by the account-id check further down.
        path = tmp_path / "keystore.json"
        generate_keystore(path)
        raw = json.loads(path.read_text())
        raw["scheme"] = "hybrid"
        path.write_text(json.dumps(raw))
        with pytest.raises(QuipSigningError, match="generate a fresh keystore"):
            load_keystore(path)

    def test_tampered_account_id_rejected(self, tmp_path) -> None:
        path = tmp_path / "keystore.json"
        generate_keystore(path)
        raw = json.loads(path.read_text())
        raw["account_id_hex"] = "0x" + "00" * 32
        path.write_text(json.dumps(raw))
        with pytest.raises(QuipSigningError, match="tampered"):
            load_keystore(path)

    def test_missing_cached_field_rejected(self, tmp_path) -> None:
        path = tmp_path / "keystore.json"
        generate_keystore(path)
        raw = json.loads(path.read_text())
        del raw["public_key_hex"]
        path.write_text(json.dumps(raw))
        with pytest.raises(QuipSigningError, match="public_key_hex"):
            load_keystore(path)

    def test_wrong_scheme_rejected(self, tmp_path) -> None:
        path = tmp_path / "keystore.json"
        generate_keystore(path)
        raw = json.loads(path.read_text())
        raw["scheme"] = "sr25519"
        path.write_text(json.dumps(raw))
        with pytest.raises(QuipSigningError, match="scheme"):
            load_keystore(path)

    def test_encrypted_rejected(self, tmp_path) -> None:
        path = tmp_path / "keystore.json"
        generate_keystore(path)
        raw = json.loads(path.read_text())
        raw["encrypted"] = True
        path.write_text(json.dumps(raw))
        with pytest.raises(QuipSigningError, match="encrypted"):
            load_keystore(path)

    def test_missing_file_rejected(self, tmp_path) -> None:
        with pytest.raises(QuipSigningError, match="not found"):
            load_keystore(tmp_path / "absent.json")


# ---------------------------------------------------------------------------
# signer_from_seed
# ---------------------------------------------------------------------------


class TestSignerFromSeed:
    def test_bytes_and_hex_agree(self) -> None:
        from_bytes = signer_from_seed(SEED)
        from_hex = signer_from_seed("0x" + SEED.hex())
        from_bare = signer_from_seed(SEED.hex())
        assert from_bytes.account_id == from_hex.account_id == from_bare.account_id

    def test_rejects_wrong_length(self) -> None:
        with pytest.raises(QuipSigningError, match="32 bytes"):
            signer_from_seed(bytes(16))

    def test_rejects_bad_hex(self) -> None:
        with pytest.raises(QuipSigningError, match="hex"):
            signer_from_seed("0xnothex")


# ---------------------------------------------------------------------------
# build_signed_extrinsic
# ---------------------------------------------------------------------------


class TestBuildSignedExtrinsic:
    def test_small_payload_signed_raw_and_self_verifies(self) -> None:
        signer = signer_from_seed(SEED)
        iface = FakeIface(call_bytes=bytes([0x09, 0x00, 0x01, 0x02]), nonce=3, spec_version=42, tx_version=7)
        wire, ext_hash = build_signed_extrinsic(iface, signer, "QuantumComputeMempool", "propose_job", {})

        account, envelope, rest = _decode_wire(wire)
        assert account == signer.account_id
        assert len(envelope) == HYBRID_ENVELOPE_LEN

        # The trailing region is extra || call; reconstruct the expected payload.
        extra, additional = quip_signing._signed_extensions(
            nonce=3, spec_version=42, tx_version=7, genesis_bytes=GENESIS
        )
        assert rest == extra + iface.call_bytes

        payload = iface.call_bytes + extra + additional
        assert len(payload) <= quip_signing.SIGNED_PAYLOAD_HASH_THRESHOLD
        # Signed raw (not hashed) since payload is short.
        assert quip_signer.verify_envelope(payload, envelope, signer.account_id)
        assert ext_hash == "0x" + hashlib.blake2b(wire, digest_size=32).hexdigest()

    def test_large_payload_signs_blake2_256_hash(self) -> None:
        signer = signer_from_seed(SEED)
        big_call = bytes(i % 256 for i in range(300))  # forces payload > 256 bytes
        iface = FakeIface(call_bytes=big_call, nonce=0, spec_version=1, tx_version=1)
        wire, _ = build_signed_extrinsic(iface, signer, "QuantumComputeMempool", "propose_job", {})

        _, envelope, _ = _decode_wire(wire)
        extra, additional = quip_signing._signed_extensions(
            nonce=0, spec_version=1, tx_version=1, genesis_bytes=GENESIS
        )
        payload = big_call + extra + additional
        assert len(payload) > quip_signing.SIGNED_PAYLOAD_HASH_THRESHOLD
        digest = hashlib.blake2b(payload, digest_size=32).digest()

        # The envelope must verify against the HASH, not the raw payload.
        assert quip_signer.verify_envelope(digest, envelope, signer.account_id)
        assert not quip_signer.verify_envelope(payload, envelope, signer.account_id)

    # 300 bytes gives a 2-byte length prefix; 17000 crosses into the 4-byte mode.
    @pytest.mark.parametrize("call_len", [300, 17_000])
    def test_disarmed_copy_keeps_length_and_fails_verification(self, call_len: int) -> None:
        signer = signer_from_seed(SEED)
        iface = FakeIface(call_bytes=bytes(i % 256 for i in range(call_len)), nonce=0, spec_version=1, tx_version=1)
        wire, _ = build_signed_extrinsic(iface, signer, "QuantumComputeMempool", "propose_job", {})
        disarmed = quip_signing.disarm_extrinsic(wire)

        prefix_len = {0: 1, 1: 2, 2: 4}[wire[0] & 0b11]
        changed = [i for i, (a, b) in enumerate(zip(wire, disarmed, strict=True)) if a != b]
        assert len(disarmed) == len(wire)
        assert changed == [prefix_len + 2 + 32 + HYBRID_PUBLIC_LEN + 1]
        account, envelope, rest = _decode_wire(disarmed)
        assert (account, rest) == _decode_wire(wire)[::2]
        extra, additional = quip_signing._signed_extensions(
            nonce=0, spec_version=1, tx_version=1, genesis_bytes=GENESIS
        )
        digest = hashlib.blake2b(iface.call_bytes + extra + additional, digest_size=32).digest()
        assert quip_signer.verify_envelope(digest, _decode_wire(wire)[1], signer.account_id)
        assert not quip_signer.verify_envelope(digest, envelope, signer.account_id)
        assert envelope[:HYBRID_PUBLIC_LEN] == _decode_wire(wire)[1][:HYBRID_PUBLIC_LEN]

    def test_disarm_rejects_a_frame_that_is_not_signed(self) -> None:
        with pytest.raises(QuipSigningError, match="cannot disarm"):
            quip_signing.disarm_extrinsic(b"\x04\x00")

    def test_disarm_rejects_a_frame_with_another_version_byte(self) -> None:
        signer = signer_from_seed(SEED)
        wire, _ = build_signed_extrinsic(FakeIface(), signer, "QuantumComputeMempool", "propose_job", {})
        prefix_len = {0: 1, 1: 2, 2: 4}[wire[0] & 0b11]
        tampered = bytearray(wire)
        tampered[prefix_len] = 0x05
        with pytest.raises(QuipSigningError, match="cannot disarm"):
            quip_signing.disarm_extrinsic(bytes(tampered))

    def test_call_composed_with_given_module_and_params(self) -> None:
        signer = signer_from_seed(SEED)
        iface = FakeIface()
        params = {"spec_id": "0xabc", "reward": 1}
        build_signed_extrinsic(iface, signer, "QuantumComputeMempool", "propose_job", params)
        assert iface.compose_calls == [("QuantumComputeMempool", "propose_job", params)]

    def test_self_check_failure_aborts(self, monkeypatch) -> None:
        signer = signer_from_seed(SEED)
        iface = FakeIface()
        monkeypatch.setattr(quip_signing.quip_signer, "verify_envelope", lambda *a, **k: False)
        with pytest.raises(QuipSigningError, match="self-check failed"):
            build_signed_extrinsic(iface, signer, "QuantumComputeMempool", "propose_job", {})


# ---------------------------------------------------------------------------
# submit_and_watch
# ---------------------------------------------------------------------------


def _block_with_extrinsic(ext_hash: str) -> dict:
    return {"extrinsics": [_FakeExtrinsic(ext_hash)]}


def _event(idx: int, event_id: str) -> dict:
    return {
        "phase": {"ApplyExtrinsic": idx},
        "event": {"module_id": "System", "event_id": event_id, "attributes": {"weight": 1}},
    }


def _event_str_phase(idx: int, event_id: str) -> dict:
    """An event as newer substrate-interface decodes it: ``phase`` is the bare
    enum name ("ApplyExtrinsic", index dropped) and the applied-extrinsic index
    is a sibling ``extrinsic_idx`` field."""
    return {
        "phase": "ApplyExtrinsic",
        "extrinsic_idx": idx,
        "event": {"module_id": "System", "event_id": event_id, "attributes": {"weight": 1}},
    }


def _block_with_extrinsics(*ext_hashes: str) -> dict:
    return {"extrinsics": [_FakeExtrinsic(h) for h in ext_hashes]}


class TestSubmitAndWatch:
    def test_rejects_unknown_wait_for(self) -> None:
        with pytest.raises(QuipSigningError, match="wait_for"):
            submit_and_watch(FakeIface(), b"\x00", "0x00", wait_for="bogus")

    def test_sent_returns_without_block(self) -> None:
        iface = FakeIface()
        receipt = submit_and_watch(iface, b"\xde\xad", "0x" + "aa" * 32, wait_for="sent")
        assert isinstance(receipt, ExtrinsicReceipt)
        assert receipt.block_hash is None
        assert receipt.is_finalized is False
        assert receipt.is_success is False  # no block reached -> not yet successful

    def test_sent_rpc_error_raises(self) -> None:
        iface = FakeIface()
        iface.sent_response = {"error": {"code": -1, "message": "bad"}}
        with pytest.raises(QuipSigningError, match="rejected"):
            submit_and_watch(iface, b"\xde\xad", "0x" + "aa" * 32, wait_for="sent")

    def test_inblock_success(self) -> None:
        ext_hash = "0x" + "cd" * 32
        block_hash = "0x" + "ef" * 32
        iface = FakeIface()
        iface.watch_message = {"params": {"result": {"inBlock": block_hash}}}
        iface.block = _block_with_extrinsic(ext_hash)
        iface.events = [_event(0, "ExtrinsicSuccess")]
        receipt = submit_and_watch(iface, b"\x01\x02", ext_hash, wait_for="inblock")
        assert receipt.block_hash == block_hash
        assert receipt.error is None
        assert receipt.is_success is True
        assert iface.unwatched  # the subscription was closed

    def test_inblock_dispatch_failure_recorded(self) -> None:
        ext_hash = "0x" + "cd" * 32
        block_hash = "0x" + "ef" * 32
        iface = FakeIface()
        iface.watch_message = {"params": {"result": {"inBlock": block_hash}}}
        iface.block = _block_with_extrinsic(ext_hash)
        iface.events = [_event(0, "ExtrinsicFailed")]
        receipt = submit_and_watch(iface, b"\x01\x02", ext_hash, wait_for="inblock")
        assert receipt.block_hash == block_hash
        assert receipt.error is not None
        assert "ExtrinsicFailed" in receipt.error
        assert receipt.is_success is False

    def test_inblock_extrinsic_not_found_is_non_success(self) -> None:
        ext_hash = "0x" + "cd" * 32
        block_hash = "0x" + "ef" * 32
        iface = FakeIface()
        iface.watch_message = {"params": {"result": {"inBlock": block_hash}}}
        iface.block = _block_with_extrinsic("0x" + "99" * 32)  # a different extrinsic
        iface.events = []
        receipt = submit_and_watch(iface, b"\x01\x02", ext_hash, wait_for="inblock")
        assert receipt.error is not None
        assert "not found" in receipt.error
        assert receipt.is_success is False

    def test_finalized_success(self) -> None:
        ext_hash = "0x" + "cd" * 32
        block_hash = "0x" + "ef" * 32
        iface = FakeIface()
        iface.watch_message = {"params": {"result": {"finalized": block_hash}}}
        iface.block = _block_with_extrinsic(ext_hash)
        iface.events = [_event(0, "ExtrinsicSuccess")]
        receipt = submit_and_watch(iface, b"\x01\x02", ext_hash, wait_for="finalized")
        assert receipt.is_finalized is True
        assert receipt.is_success is True

    def test_terminal_pool_status_raises(self) -> None:
        iface = FakeIface()
        iface.watch_message = {"params": {"result": "invalid"}}
        with pytest.raises(QuipSigningError, match="transaction pool"):
            submit_and_watch(iface, b"\x01\x02", "0x" + "cd" * 32, wait_for="inblock")

    @pytest.mark.parametrize("status", ["usurped", "retracted", "finalityTimeout"])
    def test_dict_terminal_pool_status_raises(self, status) -> None:
        # These data-carrying statuses arrive as single-key dicts, not bare
        # strings, so they match neither inBlock/finalized nor the string branch;
        # without an explicit check the handler would "keep waiting" and hang.
        iface = FakeIface()
        iface.watch_message = {"params": {"result": {status: "0x" + "ab" * 32}}}
        with pytest.raises(QuipSigningError, match="transaction pool"):
            submit_and_watch(iface, b"\x01\x02", "0x" + "cd" * 32, wait_for="inblock")
        assert iface.unwatched  # the subscription was closed, not left hanging

    def test_inblock_success_string_phase_matches_by_extrinsic_idx(self) -> None:
        # Regression (QUI-569): newer substrate-interface decodes event `phase`
        # as the bare string "ApplyExtrinsic" and carries the applied-extrinsic
        # index in a sibling `extrinsic_idx`. The receipt matcher must key off
        # that field; otherwise the phase-only lookup misses the confirmed
        # ExtrinsicSuccess and a successful submission (e.g. a live devnet
        # propose_job) is misreported as an unclassified failure.
        ext_hash = "0x" + "cd" * 32
        block_hash = "0x" + "ef" * 32
        iface = FakeIface()
        iface.watch_message = {"params": {"result": {"inBlock": block_hash}}}
        # Our extrinsic sits at index 1 (a timestamp inherent at 0), mirroring a
        # real block. The decoy ExtrinsicSuccess for index 0 must NOT be matched.
        iface.block = _block_with_extrinsics("0x" + "00" * 32, ext_hash)
        iface.events = [
            _event_str_phase(0, "ExtrinsicSuccess"),
            _event_str_phase(1, "ExtrinsicSuccess"),
        ]
        receipt = submit_and_watch(iface, b"\x01\x02", ext_hash, wait_for="inblock")
        assert receipt.block_hash == block_hash
        assert receipt.error is None
        assert receipt.is_success is True

    def test_inblock_failure_string_phase_matches_by_extrinsic_idx(self) -> None:
        # The same string-phase representation must still surface a real
        # ExtrinsicFailed at our index rather than silently pass as success.
        ext_hash = "0x" + "cd" * 32
        block_hash = "0x" + "ef" * 32
        iface = FakeIface()
        iface.watch_message = {"params": {"result": {"inBlock": block_hash}}}
        iface.block = _block_with_extrinsics("0x" + "00" * 32, ext_hash)
        iface.events = [
            _event_str_phase(0, "ExtrinsicSuccess"),
            _event_str_phase(1, "ExtrinsicFailed"),
        ]
        receipt = submit_and_watch(iface, b"\x01\x02", ext_hash, wait_for="inblock")
        assert receipt.error is not None
        assert "ExtrinsicFailed" in receipt.error
        assert receipt.is_success is False
