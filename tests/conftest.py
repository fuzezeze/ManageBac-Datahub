import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SCHOOL_ID", "school")
os.environ.setdefault("SCHOOL_TIMEZONE", "UTC")
os.environ.setdefault("SCHOOL_TIMEZONE_CONFIRMED", "false")
os.environ.setdefault("CURSOR_SECRET", "test-cursor-secret-value")
os.environ.setdefault("MB_AUTH_MODE", "fixture")
os.environ.setdefault("ADMIN_EMAIL", "admin@example.com")
os.environ.setdefault("ADMIN_PASSWORD", "change-me")
os.environ.setdefault("DATABASE_URL_MIGRATE", "mysql+pymysql://hub_migrate:hub_migrate_dev@127.0.0.1:3306/hub_ops?charset=utf8mb4")
os.environ.setdefault("DATABASE_URL_SYNC", "mysql+pymysql://hub_sync:hub_sync_dev@127.0.0.1:3306/hub_ops?charset=utf8mb4")
os.environ.setdefault("DATABASE_URL_API", "mysql+pymysql://hub_api:hub_api_dev@127.0.0.1:3306/hub_ops?charset=utf8mb4")
os.environ.setdefault("DATABASE_URL_AUTH", "mysql+pymysql://hub_auth:hub_auth_dev@127.0.0.1:3306/hub_ops?charset=utf8mb4")
os.environ.setdefault("DATABASE_URL_ADMIN", "mysql+pymysql://hub_admin:hub_admin_dev@127.0.0.1:3306/hub_ops?charset=utf8mb4")
os.environ.setdefault("DATABASE_URL_ANALYTICS", "mysql+pymysql://hub_analytics:hub_analytics_dev@127.0.0.1:3306/hub_analytics?charset=utf8mb4")

from hub.config import get_settings
from hub.db import engine_for

get_settings.cache_clear()
engine_for.cache_clear()
