from urllib.parse import quote


class ClashApiError(RuntimeError):
    def __init__(self, status_code, message, reason=None):
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason

    @property
    def is_access_denied(self):
        if self.status_code == 403:
            return True
        return self.reason in {"accessDenied", "accessDenied.invalidIp"}


class ClashClient:
    BASE_URL = "https://api.clashofclans.com/v1"

    def __init__(self, api_token, timeout=20):
        self.api_token = api_token
        self.timeout = timeout

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Accept": "application/json",
        }

    def get_current_war(self, clan_tag):
        import requests

        encoded_clan_tag = quote(clan_tag, safe="")
        url = f"{self.BASE_URL}/clans/{encoded_clan_tag}/currentwar"
        response = requests.get(url, headers=self._headers(), timeout=self.timeout)

        return self._json_or_raise(response)

    def get_clan_members(self, clan_tag):
        import requests

        encoded_clan_tag = quote(clan_tag, safe="")
        url = f"{self.BASE_URL}/clans/{encoded_clan_tag}/members"
        response = requests.get(url, headers=self._headers(), timeout=self.timeout)
        data = self._json_or_raise(response)
        members = data.get("items", [])
        if not isinstance(members, list):
            return []
        return members

    def _json_or_raise(self, response):
        if response.status_code == 200:
            return response.json()

        message = response.text
        reason = None
        try:
            payload = response.json()
        except ValueError:
            payload = None

        if isinstance(payload, dict):
            reason = payload.get("reason")
            message = payload.get("message") or payload.get("reason") or message

        raise ClashApiError(
            response.status_code,
            f"Clash API returned {response.status_code}: {message}",
            reason=reason,
        )
