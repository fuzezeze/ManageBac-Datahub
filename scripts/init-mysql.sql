-- Run as a MySQL administrator before Alembic.
-- Local: sudo mysql < scripts/init-mysql.sql
-- Aliyun RDS: run as the privileged account, then change the passwords.

CREATE DATABASE IF NOT EXISTS hub_raw CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS hub_core CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS hub_ops CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS hub_analytics CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'hub_migrate'@'%' IDENTIFIED BY 'hub_migrate_dev';
CREATE USER IF NOT EXISTS 'hub_sync'@'%' IDENTIFIED BY 'hub_sync_dev';
CREATE USER IF NOT EXISTS 'hub_api'@'%' IDENTIFIED BY 'hub_api_dev';
CREATE USER IF NOT EXISTS 'hub_auth'@'%' IDENTIFIED BY 'hub_auth_dev';
CREATE USER IF NOT EXISTS 'hub_admin'@'%' IDENTIFIED BY 'hub_admin_dev';
CREATE USER IF NOT EXISTS 'hub_analytics'@'%' IDENTIFIED BY 'hub_analytics_dev';

GRANT ALL PRIVILEGES ON hub_raw.* TO 'hub_migrate'@'%' WITH GRANT OPTION;
GRANT ALL PRIVILEGES ON hub_core.* TO 'hub_migrate'@'%' WITH GRANT OPTION;
GRANT ALL PRIVILEGES ON hub_ops.* TO 'hub_migrate'@'%' WITH GRANT OPTION;
GRANT ALL PRIVILEGES ON hub_analytics.* TO 'hub_migrate'@'%' WITH GRANT OPTION;

FLUSH PRIVILEGES;
