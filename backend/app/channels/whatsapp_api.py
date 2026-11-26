"""Minimal WhatsApp Cloud API (Meta Graph API) client: the official API only (CLAUDE.md hard rule 13).

The access token goes in the Authorization header. Errors carry Meta's code and a short
message, never the token or the request headers.
"""

import httpx

GRAPH = "https://graph.facebook.com"


class WhatsAppError(Exception):
    def __init__(self, message: str, code: int | None = None, status: int | None = None):
        super().__init__(message)
        self.code = code        # Meta error code, e.g. 190 bad token, 131047 outside the 24-hour window
        self.status = status    # HTTP status


def make_client() -> httpx.AsyncClient:
    """One short-lived client per call. Tests monkeypatch this to inject a MockTransport."""
    return httpx.AsyncClient(timeout=10)


class WhatsAppAPI:
    def __init__(self, access_token: str, version: str):
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self._base = f"{GRAPH}/{version}"

    async def _request(self, method: str, path: str, *, json: dict | None = None, params: dict | None = None) -> dict:
        try:
            async with make_client() as client:
                response = await client.request(method, f"{self._base}/{path}", json=json, params=params,
                                                headers=self._headers)
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise WhatsAppError(f"whatsapp {path.rsplit('/', 1)[-1]} failed: {type(exc).__name__}") from None
        if response.status_code >= 400 or "error" in data:
            err = data.get("error") or {}
            raise WhatsAppError(f"whatsapp {path.rsplit('/', 1)[-1]} failed: {err.get('code')} {str(err.get('message', ''))[:120]}",
                                err.get("code"), response.status_code)
        return data

    async def phone_number(self, phone_number_id: str) -> dict:
        """The connected number's display info; also proves the token can use this number."""
        return await self._request("GET", phone_number_id, params={"fields": "display_phone_number,verified_name"})

    async def send(self, phone_number_id: str, payload: dict) -> dict:
        return await self._request("POST", f"{phone_number_id}/messages",
                                   json={"messaging_product": "whatsapp", **payload})
