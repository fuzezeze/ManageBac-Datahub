from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    school_id: str = "school"
    school_timezone: str = "UTC"
    school_timezone_confirmed: bool = False
    cursor_secret: str = Field(min_length=16)

    database_url_migrate: str
    database_url_sync: str
    database_url_api: str
    database_url_auth: str
    database_url_admin: str
    database_url_analytics: str
    database_ssl_ca: str = ""

    mb_auth_mode: str = "fixture"
    mb_api_base_url: str = ""
    mb_api_prefix: str = "/v2p3"
    mb_token: str = ""
    mb_client_id: str = ""
    mb_client_secret: str = ""
    mb_rate_limit_per_minute: int = 180
    mb_allow_fixture: bool = False

    admin_email: str = "admin@example.com"
    admin_password: str = ""
    cookie_secure: bool = False
    trust_proxy: bool = False
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"

    page_size_default: int = 100
    page_size_max: int = 200
    attendance_max_days: int = 31
    attendance_lookback_days: int = 7
    freshness_hours: int = 36
    attendance_freshness_hours: int = 6
    strict_freshness: bool = False

    raw_retention_days: int | None = None
    audit_retention_days: int | None = None

    @field_validator("raw_retention_days", "audit_retention_days", mode="before")
    @classmethod
    def _empty_int(cls, value):
        if value == "" or value is None:
            return None
        return value

    worker_lease_seconds: int = 60
    max_transient_attempts: int = 5
    max_rate_limit_attempts: int = 20

    @field_validator("mb_auth_mode")
    @classmethod
    def _auth_mode(cls, value: str) -> str:
        mode = value.lower()
        if mode not in {"fixture", "token", "oauth"}:
            raise ValueError("MB_AUTH_MODE must be fixture, token, or oauth")
        return mode

    @field_validator("mb_api_prefix")
    @classmethod
    def _prefix(cls, value: str) -> str:
        prefix = value if value.startswith("/") else f"/{value}"
        return prefix.rstrip("/")

    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.school_timezone)

    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    def fixture_allowed(self) -> bool:
        if self.mb_auth_mode != "fixture":
            return True
        return self.app_env != "production" or self.mb_allow_fixture

    def url_for(self, role: str) -> str:
        return {
            "migrate": self.database_url_migrate,
            "sync": self.database_url_sync,
            "api": self.database_url_api,
            "auth": self.database_url_auth,
            "admin": self.database_url_admin,
            "analytics": self.database_url_analytics,
        }[role]


@lru_cache
def get_settings() -> Settings:
    return Settings()
