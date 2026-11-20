"""Encrypt channel credentials at rest (store_channels.credentials) with Fernet.

The key is CHANNEL_ENCRYPTION_KEY from .env (hard rule 9); nothing is stored in plain text.
"""

import json

from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings


class CryptoError(Exception):
    pass


def _fernet(settings: Settings) -> Fernet:
    if settings.channel_encryption_key is None or not settings.channel_encryption_key.get_secret_value():
        raise CryptoError("CHANNEL_ENCRYPTION_KEY is not set")
    try:
        return Fernet(settings.channel_encryption_key.get_secret_value().encode())
    except ValueError:
        raise CryptoError("CHANNEL_ENCRYPTION_KEY is not a valid Fernet key") from None


def encrypt_json(settings: Settings, data: dict) -> str:
    return _fernet(settings).encrypt(json.dumps(data).encode()).decode()


def decrypt_json(settings: Settings, token: str) -> dict:
    try:
        return json.loads(_fernet(settings).decrypt(token.encode()))
    except (InvalidToken, ValueError):
        raise CryptoError("channel credentials can't be decrypted (key changed?)") from None
