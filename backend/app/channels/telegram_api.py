"""Minimal Telegram Bot API client, shared by the store's customer bot (channels/telegram.py)
and the platform's owner-notification bot (services/notifications.py).

The bot token is part of every URL, so errors never include the request URL or the
underlying exception text: only the method name and Telegram's own error code/description.
"""

import httpx


class TelegramError(Exception):
    def __init__(self, message: str, error_code: int | None = None):
        super().__init__(message)
        self.error_code = error_code  # Telegram's code, e.g. 401 bad token, 409 webhook conflict


def make_client() -> httpx.AsyncClient:
    """One short-lived client per call. Tests monkeypatch this to inject a MockTransport."""
    return httpx.AsyncClient(timeout=10)


class TelegramAPI:
    def __init__(self, token: str, client: httpx.AsyncClient | None = None):
        self._token = token
        self._client = client

    async def call(self, method: str, payload: dict | None = None) -> object:
        url = f"https://api.telegram.org/bot{self._token}/{method}"
        try:
            if self._client is not None:
                response = await self._client.post(url, json=payload or {}, timeout=10)
            else:
                async with make_client() as client:
                    response = await client.post(url, json=payload or {})
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TelegramError(f"telegram {method} failed: {type(exc).__name__}") from None
        if not data.get("ok"):
            raise TelegramError(f"telegram {method} failed: {data.get('error_code')} {data.get('description', '')[:100]}",
                                data.get("error_code"))
        return data["result"]
