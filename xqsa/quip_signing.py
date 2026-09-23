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
Extrinsic assembly for Quip Network hybrid-signed transactions.

Crypto is delegated to the ``quip_signer`` binding (protocol-rs MR !41): this
module owns ONLY the extrinsic-assembly layer -- keystore persistence of the
32-byte master seed, SCALE SignedPayload composition, the v4 wire frame, and
submission. No HKDF / FN-DSA / account-id derivation lives here.

The Quip runtime's ``Signature`` type is ``HybridTxSignature`` (a composite
struct of ``public || signature``, not a ``MultiSignature`` enum), so
``substrate-interface``'s ``create_signed_extrinsic`` cannot build these
extrinsics. We assemble the wire bytes ourselves and submit through the iface's
RPC layer; ``substrate-interface`` is still used for ``compose_call``, chain
state, and the websocket.

Signing contract (settled by ``quip_signer`` 0.3.0, the first release carrying
the H4 suite -- sr25519 + FN-DSA-512 rather than H3's sr25519 + ML-DSA-44): the
H4 domain prefix is applied INTRINSICALLY by ``HybridSigner.sign`` -- do not
pre-apply it. Substrate's ">256-byte ``SignedPayload`` -> sign
``blake2_256(payload)``" rule is the CALLER's responsibility (the binding signs
raw bytes, no hashing, no length check), so :func:`build_signed_extrinsic`
applies it before calling ``sign``.

The extrinsic layout is adapted from ``quip.network/faucet`` and the
``quip-protocol`` miner's ``shared/substrate_client.py``; it carries the H4
suite (sr25519 + FN-DSA-512) as published by ``quip-signer`` 0.3.x, and is
validated by ``test_quip_signing.py`` plus live submission. The
signed-extension order in :data:`SIGNED_EXTENSIONS` is the one
item only a live metadata check can confirm, so ``test_quip_live.py`` asserts it
against the chain's own metadata.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import quip_signer

from xqsa.quip_codec import QuipSigningError, _as_int_or_none, _canonical_hex, _event_ids, _strip_0x

if TYPE_CHECKING:
    from quip_signer import HybridSigner

# SCALE-extrinsic constants (Substrate v4 signed extrinsic).
EXTRINSIC_VERSION_SIGNED = 0x84  # transaction version 4 with the 0x80 "signed" bit set.
MULTI_ADDRESS_ID = 0x00  # ``MultiAddress::Id`` discriminator (32-byte account id follows).

# Substrate signs ``blake2_256(payload)`` instead of the raw payload once the
# encoded ``SignedPayload`` exceeds this many bytes (``using_encoded`` rule).
SIGNED_PAYLOAD_HASH_THRESHOLD = 256

# The runtime's signed extensions, in metadata order. Confirmed live against
# aglais (``specVersion`` 117) and the outgoing testnet (116) on 2026-09-09.
# ``EthSetOrigin`` (``pallet_revive::evm::tx_extension::SetOrigin``) is empty in
# both the ``extra`` and ``additional`` halves, so it costs no wire bytes today,
# but its position matters the moment a runtime gives it a payload.
#
# ``_extension_fields`` carries both halves per extension and
# ``_signed_extensions`` concatenates each of them in this order, and
# ``test_quip_live.py::TestConnectivity::test_signed_extensions_match_chain``
# asserts it against live metadata -- so the next insertion fails a test rather
# than a submission.
SIGNED_EXTENSIONS: tuple[str, ...] = (
    "AuthorizeCall",
    "CheckNonZeroSender",
    "CheckSpecVersion",
    "CheckTxVersion",
    "CheckGenesis",
    "CheckMortality",
    "CheckNonce",
    "CheckWeight",
    "ChargeTransactionPayment",
    "CheckMetadataHash",
    "EthSetOrigin",
    "WeightReclaim",
)

# ``quip_signer`` byte-length invariants (see the binding's parity tests).
MASTER_SEED_LEN = 32
ACCOUNT_ID_LEN = 32
HYBRID_PUBLIC_LEN = 929  # sr25519_pk(32) || falcon512_pk(897)
HYBRID_ENVELOPE_LEN = 1660  # public(929) || signature(731)

# Keystore on-disk format.
KEYSTORE_VERSION = 1
KEYSTORE_SCHEME = "hybrid-falcon512"
KEYSTORE_FILE_MODE = 0o600


# QuipSigningError lives in the dependency-free quip_codec module (imported
# above) so it can be re-exported from ``xqsa`` without importing this module's
# ``quip_signer`` dependency; it is re-exported here for backward compatibility.


# ---------------------------------------------------------------------------
# Keystore: persist the 32-byte master seed; re-derive the signer on load.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Keystore:
    """A loaded keystore: its path, the master seed, and the derived signer.

    The signer is a ``quip_signer.HybridSigner`` rebuilt from ``seed`` -- all
    crypto (key derivation, account id) lives in the binding, not here.
    """

    path: Path
    # repr=False keeps the raw master seed out of ``repr(keystore)`` -- a stray
    # ``logger.debug("%r", ks)``, exception render, or REPL echo would otherwise
    # expose private key material. compare=False so equality is identity-by-path
    # /signer and does not touch the secret either.
    seed: bytes = field(repr=False, compare=False)
    signer: HybridSigner

    @property
    def account_id(self) -> bytes:
        """The 32-byte chain account id of the keystore's signer."""
        return self.signer.account_id


def signer_from_seed(seed: bytes | str) -> HybridSigner:
    """Build a ``quip_signer.HybridSigner`` from a 32-byte master seed.

    ``seed`` may be raw bytes or a hex string (``0x``-prefixed or bare).

    Raises:
        QuipSigningError: if the seed is not exactly 32 bytes.
    """
    seed_bytes = _coerce_seed(seed)
    return quip_signer.HybridSigner.from_seed(seed_bytes)


def generate_keystore(path: Path | str, *, overwrite: bool = False) -> Keystore:
    """Create a keystore with a fresh random 32-byte seed at ``path`` (mode 0600).

    Raises:
        QuipSigningError: if ``path`` already exists and ``overwrite`` is False.
    """
    resolved = Path(path).expanduser()
    if resolved.exists() and not overwrite:
        raise QuipSigningError(f"keystore already exists at {resolved}; pass overwrite=True to replace")
    seed = os.urandom(MASTER_SEED_LEN)
    signer = quip_signer.HybridSigner.from_seed(seed)
    _write_keystore(resolved, seed, signer)
    return Keystore(path=resolved, seed=seed, signer=signer)


def load_keystore(path: Path | str) -> Keystore:
    """Open an existing keystore and re-derive its signer from the stored seed.

    The cached ``account_id_hex`` / ``public_key_hex`` fields are re-derived from
    the seed and compared, so a keystore tampered to advertise a different
    identity than its seed yields is rejected here rather than at submission.

    Raises:
        QuipSigningError: if the file is missing, malformed, encrypted, of an
            unsupported version/scheme, or its cached identity does not match
            the seed.
    """
    resolved = Path(path).expanduser()
    if not resolved.exists():
        raise QuipSigningError(f"keystore not found: {resolved}")
    _warn_if_world_readable(resolved)
    try:
        raw = json.loads(resolved.read_text())
    except (OSError, ValueError) as exc:
        raise QuipSigningError(f"keystore at {resolved} is not readable JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise QuipSigningError(f"keystore at {resolved} must be a JSON object")
    if raw.get("version") != KEYSTORE_VERSION:
        raise QuipSigningError(f"keystore version {raw.get('version')!r} not supported; expected {KEYSTORE_VERSION}")
    scheme = raw.get("scheme")
    if scheme != KEYSTORE_SCHEME:
        if scheme == "hybrid":
            raise QuipSigningError(
                f"keystore at {resolved} holds an H3 (sr25519 + ML-DSA-44) account; the chain now runs H4 "
                f"(sr25519 + FN-DSA-512). The 32-byte master seed in the file is still valid, but H4 derives a "
                f"different account id from it, so generate a fresh keystore and fund the new account."
            )
        raise QuipSigningError(f"expected scheme={KEYSTORE_SCHEME!r}, got {scheme!r}")
    if raw.get("encrypted"):
        raise QuipSigningError("passphrase-encrypted keystores are not supported yet")

    seed_hex = raw.get("master_seed_hex")
    if not isinstance(seed_hex, str) or not seed_hex.strip():
        raise QuipSigningError("keystore is missing the 'master_seed_hex' field")
    seed = _coerce_seed(seed_hex)
    signer = quip_signer.HybridSigner.from_seed(seed)

    _verify_cached_field(raw, "account_id_hex", signer.account_id, resolved)
    _verify_cached_field(raw, "public_key_hex", signer.public_key, resolved)
    return Keystore(path=resolved, seed=seed, signer=signer)


def load_or_generate_keystore(path: Path | str) -> Keystore:
    """Open the keystore at ``path`` if present, otherwise generate a fresh one."""
    resolved = Path(path).expanduser()
    if resolved.exists():
        return load_keystore(resolved)
    return generate_keystore(resolved)


# ---------------------------------------------------------------------------
# Extrinsic assembly.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExtrinsicReceipt:
    """Outcome of submitting a hybrid-signed extrinsic.

    ``error`` is non-None when the chain emitted ``System.ExtrinsicFailed`` for
    this extrinsic (or when the post-inclusion event fetch could not confirm
    success); the dispatch nonetheless reached a block. Inclusion alone is not
    success on Substrate, hence the explicit event check.
    """

    extrinsic_hash: str
    block_hash: str | None
    is_finalized: bool
    error: str | None = None

    @property
    def is_success(self) -> bool:
        """True iff the extrinsic reached a block with no dispatch error."""
        return self.block_hash is not None and self.error is None


def build_signed_extrinsic(
    iface: Any,
    signer: HybridSigner,
    call_module: str,
    call_function: str,
    call_params: dict,
) -> tuple[bytes, str]:
    """Assemble a hybrid-signed v4 extrinsic, returning ``(wire_bytes, ext_hash)``.

    ``iface`` is a connected ``substrate-interface`` ``SubstrateInterface`` used
    only for ``compose_call`` and read-only chain state (nonce, genesis hash,
    runtime version). ``signer`` is a ``quip_signer.HybridSigner``.

    The signed payload is ``call || extra || additional`` (signed-extension
    extras in metadata order; the extensions carrying no data encode to 0
    bytes). It is ``blake2_256``-hashed before signing when it exceeds
    :data:`SIGNED_PAYLOAD_HASH_THRESHOLD` bytes -- the caller's half of the
    contract. ``signer.sign`` returns the full SCALE ``HybridTxSignature``
    envelope (``public || signature``), which is spliced into the wire frame
    after ``MultiAddress::Id``. The envelope is self-checked with
    ``quip_signer.verify_envelope`` before returning, so a wrong payload or
    identity is caught locally rather than as an opaque on-chain rejection.

    Raises:
        QuipSigningError: if the envelope fails its local verification.
    """
    # Imported here rather than at module scope to keep this module free of a
    # top-level substrate-interface import: the fake-iface unit tests stub
    # ``substrateinterface`` in ``sys.modules``, and the real package is only
    # needed at assembly time, when a live ``iface`` is already in hand.
    from substrateinterface.utils.hasher import blake2_256

    call_bytes = _compose_call_bytes(iface, call_module, call_function, call_params)

    account = signer.account_id
    # Enforce the documented wire invariants before splicing identity material
    # into the extrinsic: a wrong-length account id or hybrid public key would
    # otherwise corrupt the frame silently.
    if len(account) != ACCOUNT_ID_LEN:
        raise QuipSigningError(f"signer account id is {len(account)} bytes; expected {ACCOUNT_ID_LEN}")
    if len(signer.public_key) != HYBRID_PUBLIC_LEN:
        raise QuipSigningError(f"signer public key is {len(signer.public_key)} bytes; expected {HYBRID_PUBLIC_LEN}")
    nonce = int(iface.get_account_nonce(account_address="0x" + account.hex()))
    genesis_bytes = bytes.fromhex(_strip_0x(iface.get_block_hash(block_id=0)))
    runtime_version = iface.rpc_request("state_getRuntimeVersion", [])["result"]
    spec_version = int(runtime_version["specVersion"])
    tx_version = int(runtime_version["transactionVersion"])

    extra, additional = _signed_extensions(
        nonce=nonce,
        spec_version=spec_version,
        tx_version=tx_version,
        genesis_bytes=genesis_bytes,
    )

    payload = call_bytes + extra + additional
    payload_to_sign = blake2_256(payload) if len(payload) > SIGNED_PAYLOAD_HASH_THRESHOLD else payload

    envelope = signer.sign(payload_to_sign)
    if len(envelope) != HYBRID_ENVELOPE_LEN:
        raise QuipSigningError(f"quip_signer returned a {len(envelope)}-byte envelope; expected {HYBRID_ENVELOPE_LEN}")
    if not quip_signer.verify_envelope(payload_to_sign, envelope, account):
        raise QuipSigningError("self-check failed: the freshly signed envelope did not verify; aborting submission")

    body = bytes([EXTRINSIC_VERSION_SIGNED]) + bytes([MULTI_ADDRESS_ID]) + account + envelope + extra + call_bytes
    wire_bytes = encode_compact_u32(len(body)) + body
    ext_hash = "0x" + blake2_256(wire_bytes).hex()
    return wire_bytes, ext_hash


def submit_and_watch(
    iface: Any,
    wire_bytes: bytes,
    ext_hash: str,
    wait_for: str = "inblock",
) -> ExtrinsicReceipt:
    """Submit ``wire_bytes`` and wait for the requested inclusion stage.

    ``wait_for`` is one of ``"sent"`` (fire-and-forget), ``"inblock"`` (default;
    wait for a block), or ``"finalized"``. For the inclusion stages, once the
    extrinsic reaches a block its events are fetched and any matching
    ``System.ExtrinsicFailed`` is surfaced on the receipt's ``error`` field --
    inclusion is not success on Substrate.

    This is the synchronous counterpart of the miner's async submitter: xqsa is
    sync, so the ``author_submitAndWatchExtrinsic`` subscription is driven
    directly.

    Raises:
        QuipSigningError: if the node rejects the extrinsic outright or reports
            a terminal transaction-pool status (``dropped`` / ``invalid`` /
            ``usurped`` / ``retracted`` / ``finalityTimeout``).
    """
    if wait_for not in _WAIT_STAGES:
        raise QuipSigningError(f"wait_for must be one of {sorted(_WAIT_STAGES)}, got {wait_for!r}")

    ext_hex = "0x" + wire_bytes.hex()

    if wait_for == "sent":
        response = iface.rpc_request("author_submitExtrinsic", [ext_hex])
        if isinstance(response, dict) and "error" in response:
            raise QuipSigningError(f"author_submitExtrinsic rejected the extrinsic: {response['error']}")
        return ExtrinsicReceipt(extrinsic_hash=ext_hash, block_hash=None, is_finalized=False, error=None)

    want_finalized = wait_for == "finalized"

    def _result_handler(message: dict, update_nr: int, subscription_id: str) -> dict | None:  # noqa: ARG001
        params = message.get("params") or {}
        result = params.get("result")
        if isinstance(result, dict):
            lowered = {key.lower(): value for key, value in result.items()}
            if want_finalized and "finalized" in lowered:
                iface.rpc_request("author_unwatchExtrinsic", [subscription_id])
                return {"block_hash": lowered["finalized"], "finalized": True}
            if not want_finalized and "inblock" in lowered:
                iface.rpc_request("author_unwatchExtrinsic", [subscription_id])
                return {"block_hash": lowered["inblock"], "finalized": False}
            # The data-carrying terminal statuses (usurped/retracted/finalityTimeout)
            # arrive as single-key dicts, so they never match the str branch below;
            # without this check they fall through to ``return None`` and -- since
            # this subscription has no timeout of its own -- hang forever.
            terminal = lowered.keys() & _TERMINAL_POOL_FAILURES
            if terminal:
                iface.rpc_request("author_unwatchExtrinsic", [subscription_id])
                status = sorted(terminal)[0]
                raise QuipSigningError(f"transaction pool rejected the extrinsic: {status} ({lowered[status]})")
        elif isinstance(result, str) and result.lower() in _TERMINAL_POOL_FAILURES:
            iface.rpc_request("author_unwatchExtrinsic", [subscription_id])
            raise QuipSigningError(f"transaction pool rejected the extrinsic: {result}")
        return None  # non-terminal status -- keep waiting.

    response = iface.rpc_request("author_submitAndWatchExtrinsic", [ext_hex], result_handler=_result_handler)
    block_hash = response.get("block_hash") if isinstance(response, dict) else None
    error = None
    if block_hash:
        error = _fetch_dispatch_error(iface, block_hash=block_hash, ext_hash=ext_hash)
    return ExtrinsicReceipt(
        extrinsic_hash=ext_hash,
        block_hash=block_hash,
        is_finalized=bool(isinstance(response, dict) and response.get("finalized", False)),
        error=error,
    )


# ---------------------------------------------------------------------------
# SCALE compact integer encoders (mirror parity-scale-codec ``Compact``).
# ---------------------------------------------------------------------------


def _compact_small(value: int) -> bytes | None:
    """SCALE compact encoding for the single/two/four-byte modes.

    Returns the encoded bytes for ``0 <= value < 2**30`` (the modes the u32 and
    u128 encoders share), or ``None`` when ``value`` needs the big-integer mode.
    """
    if value < 0x40:
        return bytes([value << 2])
    if value < 0x4000:
        return ((value << 2) | 0b01).to_bytes(2, "little")
    if value < 0x4000_0000:
        return ((value << 2) | 0b10).to_bytes(4, "little")
    return None


def disarm_extrinsic(wire: bytes) -> bytes:
    """Return a copy of a :func:`build_signed_extrinsic` wire frame whose signature fails.

    Flips one byte inside the envelope's signature half, so the copy has the
    same length and layout -- and so the same ``payment_queryInfo`` fee --
    but can never be dispatched. The byte chosen (the second of the signature
    half) is random-looking material in either component's encoding, so the
    copy still decodes; only verification fails. Use it to price an extrinsic
    without handing the node something it could broadcast.

    Raises:
        QuipSigningError: if ``wire`` is not a signed frame of the expected length.
    """
    prefix_len = next(
        (n for n in (1, 2, 4, 5) if len(wire) > n and encode_compact_u32(len(wire) - n) == wire[:n]),
        None,
    )
    header_len = 2 + ACCOUNT_ID_LEN  # version byte, MultiAddress::Id tag, account
    if prefix_len is None or len(wire) < prefix_len + header_len + HYBRID_ENVELOPE_LEN:
        raise QuipSigningError("not a signed extrinsic frame; cannot disarm it")
    offset = prefix_len + header_len + HYBRID_PUBLIC_LEN + 1
    disarmed = bytearray(wire)
    disarmed[offset] ^= 0xFF
    return bytes(disarmed)


def encode_compact_u32(value: int) -> bytes:
    """SCALE compact encoding of a non-negative ``u32``.

    Raises:
        QuipSigningError: if ``value`` is negative or does not fit a ``u32``.
    """
    if value < 0:
        raise QuipSigningError(f"compact u32 must be non-negative, got {value}")
    encoded = _compact_small(value)
    if encoded is not None:
        return encoded
    if value >= 1 << 32:
        raise QuipSigningError(f"compact u32 value {value} exceeds the u32 range")
    # SCALE big-integer mode for 2**30 <= value < 2**32: the mode byte is
    # ((bytes - 4) << 2) | 0b11 = 0x03 for four bytes, then the value little-endian.
    return bytes([0x03]) + value.to_bytes(4, "little")


def encode_compact_u128(value: int) -> bytes:
    """SCALE compact encoding of a non-negative ``u128`` (e.g. the ``tip`` field).

    Raises:
        QuipSigningError: if ``value`` is negative or does not fit a ``u128``.
    """
    if value < 0:
        raise QuipSigningError(f"compact value must be non-negative, got {value}")
    encoded = _compact_small(value)
    if encoded is not None:
        return encoded
    if value >= 1 << 128:
        raise QuipSigningError(f"compact u128 value {value} exceeds the u128 range")
    raw = value.to_bytes((value.bit_length() + 7) // 8, "little")
    return bytes([((len(raw) - 4) << 2) | 0b11]) + raw


# ---------------------------------------------------------------------------
# Internals.
# ---------------------------------------------------------------------------

_WAIT_STAGES = frozenset({"sent", "inblock", "finalized"})

# Terminal ``author_submitAndWatchExtrinsic`` statuses that never finalize.
# Lower-cased to match substrate-interface's status strings case-insensitively.
# ``dropped``/``invalid`` arrive as bare strings; ``usurped``/``retracted``/
# ``finalitytimeout`` arrive as single-key dicts (``{"usurped": "0x..."}``), so
# ``_result_handler`` matches this set against both the string and the dict keys.
_TERMINAL_POOL_FAILURES = frozenset({"dropped", "invalid", "usurped", "retracted", "finalitytimeout"})


def _extension_fields(
    *,
    nonce: int,
    spec_version: int,
    tx_version: int,
    genesis_bytes: bytes,
    tip: int = 0,
) -> dict[str, tuple[bytes, bytes]]:
    """Return each signed extension's ``(extra, additional)`` contribution.

    One entry per extension, keyed by the identifier the runtime metadata uses,
    in :data:`SIGNED_EXTENSIONS` order. Most extensions contribute nothing to
    either half (``AuthorizeCall``, ``CheckNonZeroSender``, ``CheckWeight``,
    ``EthSetOrigin``, ``WeightReclaim``).

    One table rather than two parallel ones: the halves are what the runtime
    defines together, and splitting them let a name be added to one and missed
    in the other. ``test_quip_signing.py`` asserts this table's keys against
    :data:`SIGNED_EXTENSIONS`, and ``test_quip_live.py`` asserts the empty
    entries really encode nothing on-chain.
    """
    return {
        "AuthorizeCall": (b"", b""),
        "CheckNonZeroSender": (b"", b""),
        "CheckSpecVersion": (b"", spec_version.to_bytes(4, "little")),
        "CheckTxVersion": (b"", tx_version.to_bytes(4, "little")),
        "CheckGenesis": (b"", genesis_bytes),
        "CheckMortality": (b"\x00", genesis_bytes),  # Era::Immortal -> genesis hash
        "CheckNonce": (encode_compact_u32(nonce), b""),
        "CheckWeight": (b"", b""),
        "ChargeTransactionPayment": (encode_compact_u128(tip), b""),
        "CheckMetadataHash": (b"\x00", b"\x00"),  # Mode::Disabled / Option::None
        "EthSetOrigin": (b"", b""),
        "WeightReclaim": (b"", b""),
    }


def _signed_extensions(
    *,
    nonce: int,
    spec_version: int,
    tx_version: int,
    genesis_bytes: bytes,
    tip: int = 0,
) -> tuple[bytes, bytes]:
    """Return the ``(extra, additional)`` signed-extension blobs in metadata order.

    Both halves are concatenated from :func:`_extension_fields` in
    :data:`SIGNED_EXTENSIONS` order, so the declared order, the two encoded
    orders, and the set of extensions cannot drift apart.
    """
    fields = _extension_fields(
        nonce=nonce,
        spec_version=spec_version,
        tx_version=tx_version,
        genesis_bytes=genesis_bytes,
        tip=tip,
    )
    return (
        b"".join(fields[name][0] for name in SIGNED_EXTENSIONS),
        b"".join(fields[name][1] for name in SIGNED_EXTENSIONS),
    )


def _compose_call_bytes(iface: Any, call_module: str, call_function: str, call_params: dict) -> bytes:
    """SCALE-encode a call via ``iface.compose_call`` and return the raw bytes.

    ``compose_call`` returns a ``GenericCall`` whose ``.data`` is a ``ScaleBytes``
    (itself wrapping a ``.data`` bytearray); some builds hand back raw bytes or a
    hex string. Normalize all of those to ``bytes``.
    """
    call = iface.compose_call(call_module=call_module, call_function=call_function, call_params=call_params)
    raw = call.data.data if hasattr(call.data, "data") else call.data
    if isinstance(raw, str):
        return bytes.fromhex(_strip_0x(raw))
    return bytes(raw)


def _fetch_dispatch_error(iface: Any, *, block_hash: str, ext_hash: str) -> str | None:
    """Return a description if the chain rejected dispatch, else ``None``.

    ``author_submitAndWatchExtrinsic`` reports only that the extrinsic hit a
    block; the runtime may still have emitted ``System.ExtrinsicFailed`` for it.
    Locate our extrinsic by hash, then scan the block's events for that index.
    Returns a non-empty string both when dispatch failed and when the extrinsic
    could not be located at all (an unverifiable inclusion must not read as
    success); ``None`` only on confirmed success.
    """
    try:
        block = iface.get_block(block_hash=block_hash, include_author=False, ignore_decoding_errors=True)
    except Exception as exc:  # noqa: BLE001 -- surface as a non-success receipt, never crash the caller.
        return f"unclassified: get_block failed for {block_hash}: {exc}"
    if not block:
        return f"unclassified: get_block returned no block for {block_hash}"

    target = _strip_0x(ext_hash).lower()
    ext_idx: int | None = None
    for idx, ext in enumerate(block.get("extrinsics") or []):
        candidate = getattr(ext, "extrinsic_hash", None)
        if candidate is None and isinstance(ext, dict):
            candidate = ext.get("extrinsic_hash")
        if _canonical_hex(candidate) == target:
            ext_idx = idx
            break
    if ext_idx is None:
        return f"unclassified: extrinsic {target[:16]} not found in block {_strip_0x(block_hash)[:16]}"

    for event in iface.get_events(block_hash=block_hash) or []:
        value = event.value if hasattr(event, "value") else event
        if not isinstance(value, dict):
            continue
        # Resolve the applied-extrinsic index for this event. Newer
        # substrate-interface builds decode ``phase`` as the bare enum name
        # ("ApplyExtrinsic", index dropped) and expose the index as a sibling
        # ``extrinsic_idx``; older builds carry it inside the phase dict
        # ({"ApplyExtrinsic": idx}). Prefer the explicit field, then fall back
        # to the phase dict -- otherwise a confirmed ExtrinsicSuccess is never
        # matched and a successful submission is misread as a failed dispatch.
        event_idx = value.get("extrinsic_idx")
        if event_idx is None:
            event_idx = _phase_extrinsic_index(value.get("phase"))
        if _as_int_or_none(event_idx) != ext_idx:
            continue
        inner = value.get("event") or value
        module_id, event_id = _event_ids(inner)
        if module_id == "System" and event_id == "ExtrinsicFailed":
            attrs = inner.get("attributes") or inner.get("fields") or {}
            return f"System.ExtrinsicFailed: {attrs!r}"
        if module_id == "System" and event_id == "ExtrinsicSuccess":
            return None
    # Extrinsic located, but no phase-matched ExtrinsicSuccess/Failed was seen
    # (event-key/phase decode drift, which this module defends against
    # elsewhere). A genuine success always emits ExtrinsicSuccess, so its
    # absence is unconfirmed -- not success. Fall through to a non-success
    # string rather than the misleading ``return None``.
    return (
        f"unclassified: no System.ExtrinsicSuccess/Failed matched phase {ext_idx} in block {_strip_0x(block_hash)[:16]}"
    )


def _phase_extrinsic_index(phase: Any) -> int | None:
    """Extract the extrinsic index from a SCALE-decoded event ``phase`` field."""
    if isinstance(phase, dict):
        applied = phase.get("ApplyExtrinsic")
        if applied is None:
            return None
        if isinstance(applied, dict):
            index = applied.get("extrinsic_idx")
            if index is None:
                index = applied.get("index")
            return _as_int_or_none(index)
        return _as_int_or_none(applied)
    if isinstance(phase, (int, str)):
        return _as_int_or_none(phase)
    return None


def _coerce_seed(seed: bytes | str) -> bytes:
    """Normalize a seed (bytes or hex string) to exactly 32 bytes."""
    if isinstance(seed, str):
        try:
            seed_bytes = bytes.fromhex(_strip_0x(seed))
        except ValueError as exc:
            raise QuipSigningError(f"seed is not valid hex: {exc}") from exc
    else:
        seed_bytes = bytes(seed)
    if len(seed_bytes) != MASTER_SEED_LEN:
        raise QuipSigningError(f"master seed must be {MASTER_SEED_LEN} bytes, got {len(seed_bytes)}")
    return seed_bytes


def _verify_cached_field(raw: dict, field: str, expected: bytes, path: Path) -> None:
    """Assert a cached keystore hex field matches the seed-derived ``expected``."""
    cached = raw.get(field)
    if not isinstance(cached, str) or not cached.strip():
        raise QuipSigningError(f"keystore at {path} is missing the {field!r} field")
    if _strip_0x(cached).lower() != expected.hex():
        raise QuipSigningError(
            f"keystore at {path} is tampered: {field!r} does not match the identity derived from master_seed_hex"
        )


def _write_keystore(path: Path, seed: bytes, signer: HybridSigner) -> None:
    """Atomically write a 0600 keystore for ``seed`` (cached identity included)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": KEYSTORE_VERSION,
        "scheme": KEYSTORE_SCHEME,
        "encrypted": False,
        "master_seed_hex": "0x" + seed.hex(),
        "account_id_hex": "0x" + signer.account_id.hex(),
        "public_key_hex": "0x" + signer.public_key.hex(),
    }
    # Create the temp file with O_EXCL at mode 0600 so the seed is never briefly
    # world-readable between write and chmod.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, KEYSTORE_FILE_MODE)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(payload, indent=2) + "\n")
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.chmod(tmp, KEYSTORE_FILE_MODE)  # defeat umask widening.
    tmp.replace(path)


def _warn_if_world_readable(path: Path) -> None:
    """Emit a warning if ``path`` is group/world accessible (seed exposure)."""
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        import warnings

        warnings.warn(
            f"keystore {path} has group/world-accessible permissions "
            f"({stat.S_IMODE(mode):o}); tighten to {KEYSTORE_FILE_MODE:o}",
            stacklevel=2,
        )


__all__ = [
    "ExtrinsicReceipt",
    "Keystore",
    "QuipSigningError",
    "build_signed_extrinsic",
    "encode_compact_u128",
    "encode_compact_u32",
    "generate_keystore",
    "load_keystore",
    "load_or_generate_keystore",
    "signer_from_seed",
    "submit_and_watch",
]
