import hashlib
import json
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx

from hub.config import Settings


class ManageBacError(Exception):
    code = "managebac_error"


class TransientError(ManageBacError):
    code = "transient"


class RateLimited(ManageBacError):
    code = "rate_limited"

    def __init__(self, retry_after_seconds: float, message: str = "rate limited"):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class AuthError(ManageBacError):
    code = "auth"


class ContractError(ManageBacError):
    code = "contract"


@dataclass
class Page:
    endpoint: str
    params: dict
    status: int
    body_text: str
    payload: dict
    record_count: int


PARAM_ALLOWLIST = {
    "page",
    "per_page",
    "modified_since",
    "deleted_since",
    "archived",
    "program_code",
    "active",
}


def redact_params(params: dict) -> dict:
    return {key: value for key, value in params.items() if key in PARAM_ALLOWLIST}


@dataclass
class ManageBacClient:
    settings: Settings
    transport: httpx.BaseTransport | None = None
    resolver: Callable[[str, dict], tuple[int, str]] | None = None
    calls: list[tuple[str, dict]] = field(default_factory=list)
    _token: str = ""
    _token_until: datetime = field(default_factory=lambda: datetime.fromtimestamp(0, tz=timezone.utc))
    _next_request_at: float = 0.0

    def __post_init__(self) -> None:
        if self.settings.mb_auth_mode == "fixture":
            if not self.settings.fixture_allowed():
                raise RuntimeError("fixture mode is disabled in production")
            self._http = None
            return
        if not self.settings.mb_api_base_url:
            raise RuntimeError("MB_API_BASE_URL is required")
        self._http = httpx.Client(
            base_url=self.settings.mb_api_base_url.rstrip("/"),
            timeout=httpx.Timeout(30.0),
            transport=self.transport,
        )

    def close(self) -> None:
        if self._http is not None:
            self._http.close()

    def _pace(self) -> None:
        if self.settings.mb_auth_mode == "fixture":
            return
        interval = 60.0 / max(self.settings.mb_rate_limit_per_minute, 1)
        now = time.monotonic()
        if now < self._next_request_at:
            time.sleep(self._next_request_at - now)
        self._next_request_at = max(time.monotonic(), self._next_request_at) + interval

    def _headers(self) -> dict[str, str]:
        if self.settings.mb_auth_mode == "fixture":
            return {}
        if self.settings.mb_auth_mode == "token":
            if not self.settings.mb_token:
                raise AuthError("MB_TOKEN is empty")
            return {"auth-token": self.settings.mb_token, "Accept": "application/json"}
        return {"Authorization": f"Bearer {self._oauth_token()}", "Accept": "application/json"}

    def _oauth_token(self) -> str:
        now = datetime.now(timezone.utc)
        if self._token and now < self._token_until:
            return self._token
        if not self.settings.mb_client_id or not self.settings.mb_client_secret:
            raise AuthError("OAuth client credentials are empty")
        assert self._http is not None
        response = self._http.post(
            "/oauth/token",
            json={
                "grant_type": "client_credentials",
                "client_id": self.settings.mb_client_id,
                "client_secret": self.settings.mb_client_secret,
            },
        )
        if response.status_code in {401, 403}:
            raise AuthError("ManageBac rejected the OAuth client")
        if response.status_code == 429:
            raise RateLimited(_retry_after(response), "oauth rate limited")
        if response.status_code >= 500:
            raise TransientError(f"oauth token status {response.status_code}")
        if response.status_code != 200:
            raise ContractError(f"oauth token status {response.status_code}")
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise ContractError("oauth response missing access_token")
        expires_in = int(payload.get("expires_in") or 300)
        self._token = str(token)
        self._token_until = now + timedelta(seconds=max(expires_in - 60, 30))
        return self._token

    def get_page(self, path: str, params: dict | None = None, *, collection_key: str | None = None) -> Page:
        query = dict(params or {})
        if "modified_since" in query and "deleted_since" in query:
            raise ContractError("modified_since and deleted_since must not be combined")
        self.calls.append((path, dict(query)))
        self._pace()
        url_path = path if path.startswith("/") else f"{self.settings.mb_api_prefix}/{path.lstrip('/')}"
        if self.resolver is not None:
            status, body = self.resolver(url_path, query)
            headers = {}
        elif self.settings.mb_auth_mode == "fixture":
            status, body = fixture_response(url_path, query)
            headers = {}
        else:
            assert self._http is not None
            response = self._http.get(url_path, params=query, headers=self._headers())
            status = response.status_code
            body = response.text
            headers = {key.lower(): value for key, value in response.headers.items()}
        return self._interpret(url_path, query, status, body, headers, collection_key)

    def _interpret(
        self,
        path: str,
        params: dict,
        status: int,
        body: str,
        headers: dict[str, str],
        collection_key: str | None,
    ) -> Page:
        if status == 429:
            raise RateLimited(_retry_after_values(headers.get("retry-after")))
        if status in {401, 403}:
            raise AuthError(f"ManageBac returned {status} for {path}")
        if status >= 500 or status in {408, 409}:
            raise TransientError(f"ManageBac returned {status} for {path}")
        if status == 404:
            raise ContractError(f"ManageBac returned 404 for {path}")
        if status != 200:
            raise ContractError(f"ManageBac returned {status} for {path}")
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError as exc:
            raise ContractError("ManageBac response was not JSON") from exc
        if not isinstance(payload, dict):
            raise ContractError("ManageBac response JSON must be an object")
        record_count = 0
        if collection_key is not None:
            if collection_key not in payload:
                raise ContractError(f"response missing {collection_key}")
            collection = payload[collection_key]
            if isinstance(collection, list):
                record_count = len(collection)
            elif isinstance(collection, dict):
                record_count = len(collection)
            else:
                raise ContractError(f"{collection_key} has an unexpected shape")
        return Page(path, redact_params(params), status, body, payload, record_count)

    def paginate(self, path: str, params: dict | None, collection_key: str) -> Iterator[Page]:
        page_number = 1
        base = dict(params or {})
        while page_number <= 10000:
            query = {**base, "page": page_number, "per_page": base.get("per_page", 100)}
            page = self.get_page(path, query, collection_key=collection_key)
            yield page
            meta = page.payload.get("meta") or {}
            if not isinstance(meta, dict) or "total_pages" not in meta and "next_page" not in meta:
                raise ContractError(f"{path} list response is missing pagination meta")
            next_page = meta.get("next_page")
            total_pages = meta.get("total_pages") or 1
            if next_page:
                page_number = int(next_page)
            elif page_number < int(total_pages):
                page_number += 1
            else:
                return
        raise ContractError("pagination exceeded 10000 pages")


def _retry_after(response: httpx.Response) -> float:
    return _retry_after_values(response.headers.get("retry-after"))


def _retry_after_values(value: str | None) -> float:
    if not value:
        return 60.0
    try:
        return max(float(value), 1.0)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return 60.0
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max((when - datetime.now(timezone.utc)).total_seconds(), 1.0)


def body_sha256(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


def fixture_response(path: str, params: dict) -> tuple[int, str]:
    from hub.fixtures import resolve_fixture

    payload = resolve_fixture(path, params)
    return 200, json.dumps(payload)
