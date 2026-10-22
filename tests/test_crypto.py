"""Tests for request signing and command encryption."""

import base64
import json

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from custom_components.philips_lock.cloud import crypto


def test_sign256_verifies_with_the_app_key() -> None:
    """The signature covers compact, key-sorted JSON and verifies with the public half."""
    payload = {"reqTime": "1700000000000", "esn": "LOCK000000001"}
    signature = crypto.sign256(payload)
    message = json.dumps(
        payload, separators=(",", ":"), ensure_ascii=False, sort_keys=True
    ).encode()
    crypto.APP_KEY.public_key().verify(
        base64.b64decode(signature), message, padding.PKCS1v15(), hashes.SHA256()
    )


def test_sign256_ignores_key_order() -> None:
    """Two dicts with the same items sign identically."""
    assert crypto.sign256({"a": 1, "b": 2}) == crypto.sign256({"b": 2, "a": 1})


def test_signed_body_adds_sign_and_req_time() -> None:
    """signed_body keeps the params and adds a string reqTime and the sign."""
    body = crypto.signed_body({"esn": "LOCK000000001"})
    assert set(body) == {"esn", "sign", "reqTime"}
    assert isinstance(body["reqTime"], str)
    assert body["reqTime"].isdigit()


def test_encrypted_command_body_is_block_aligned() -> None:
    """RSA-1024 ciphertext is a whole number of 128-byte blocks."""
    body = crypto.encrypted_command_body(
        {
            "esn": "LOCK000000001",
            "mac": "AA:BB:CC:DD:EE:FF",
            "masterSn": "GW0000000001",
            "userNumberId": 1,
        }
    )
    raw = base64.b64decode(body["encryptData"])
    assert set(body) == {"encryptData"}
    assert raw and len(raw) % 128 == 0
