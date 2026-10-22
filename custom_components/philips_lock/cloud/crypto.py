"""Request signing and lock-command encryption.

sign256 is RSASSA-PKCS1-v1.5 over SHA-256 of the compact, key-sorted JSON of
the request, signed with the app key. Command bodies are RSA/PKCS1 encrypted in
117-byte blocks with the server key; the padding is random, so the ciphertext
differs on every call.
"""

import base64
import json
import time
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .const import APP_PRIVATE_KEY_DER_B64, SERVER_PUBLIC_KEY_DER_B64

APP_KEY: rsa.RSAPrivateKey = serialization.load_der_private_key(
    base64.b64decode(APP_PRIVATE_KEY_DER_B64), password=None
)
SERVER_KEY: rsa.RSAPublicKey = serialization.load_der_public_key(
    base64.b64decode(SERVER_PUBLIC_KEY_DER_B64)
)


def _compact_json(payload: dict[str, Any], sort_keys: bool) -> str:
    return json.dumps(
        payload, separators=(",", ":"), ensure_ascii=False, sort_keys=sort_keys
    )


def sign256(payload: dict[str, Any]) -> str:
    """Return the base64 signature of the compact, key-sorted JSON of payload."""
    signature = APP_KEY.sign(
        _compact_json(payload, sort_keys=True).encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode()


def signed_body(params: dict[str, Any]) -> dict[str, Any]:
    """Return params plus reqTime (millisecond epoch as a string) and sign."""
    req_time = str(int(time.time() * 1000))
    return {
        **params,
        "sign": sign256({**params, "reqTime": req_time}),
        "reqTime": req_time,
    }


def encrypt_for_server(plaintext: str) -> str:
    """RSA/PKCS1 encrypt plaintext block by block with the server key, base64."""
    data = plaintext.encode()
    block = SERVER_KEY.key_size // 8 - 11
    ciphertext = b"".join(
        SERVER_KEY.encrypt(data[i : i + block], padding.PKCS1v15())
        for i in range(0, len(data), block)
    )
    return base64.b64encode(ciphertext).decode()


def encrypted_command_body(params: dict[str, Any]) -> dict[str, Any]:
    """Sign params, encrypt the JSON and wrap it as the body of a lock command."""
    plaintext = _compact_json(signed_body(params), sort_keys=False)
    return {"encryptData": encrypt_for_server(plaintext)}
