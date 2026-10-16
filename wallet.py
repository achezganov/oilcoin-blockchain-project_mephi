"""Ed25519 wallets; key encryption at rest belongs to the account layer."""

import hashlib
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def is_valid_address(address: object) -> bool:
    return isinstance(address, str) and re.fullmatch(r"OIL_[0-9a-f]{32}", address) is not None


def _hex_bytes(value: str, length: int) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{%d}" % (length * 2), value):
        raise ValueError("Key or signature has an invalid hexadecimal encoding.")
    return bytes.fromhex(value)


def address_from_public_key(public_key: str) -> str:
    return "OIL_" + hashlib.sha256(_hex_bytes(public_key, 32)).hexdigest()[:32]


def public_key_from_private(private_key: str) -> str:
    key = Ed25519PrivateKey.from_private_bytes(_hex_bytes(private_key, 32))
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def generate_wallet_keys() -> dict[str, str]:
    key = Ed25519PrivateKey.generate()
    private_key = key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    ).hex()
    public_key = public_key_from_private(private_key)
    return {"address": address_from_public_key(public_key), "public_key": public_key, "private_key": private_key}


def generate_wallet() -> str:
    """Return an address only (watch-only); use generate_wallet_keys to spend."""
    return generate_wallet_keys()["address"]


def sign_message(private_key: str, payload: bytes) -> str:
    return Ed25519PrivateKey.from_private_bytes(_hex_bytes(private_key, 32)).sign(payload).hex()


def verify_signature(public_key: str, signature: str, payload: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(_hex_bytes(public_key, 32)).verify(_hex_bytes(signature, 64), payload)
        return True
    except (InvalidSignature, TypeError, ValueError):
        return False
