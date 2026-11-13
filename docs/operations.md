# Operations

## Processes

Run three processes from the same image:

- `uvicorn hub.web.app:app` serves the internal API and the admin API
- `hub worker` claims sync jobs
- `hub scheduler` inserts the next slot for each dataset

`/health` does not touch MySQL or ManageBac. `/ready` only runs `SELECT 1`.

## RDS MySQL

Use one private RDS MySQL 8 instance. Create the four databases and the six users with `scripts/init-mysql.sql`, then replace every development password.

Set `DATABASE_SSL_CA` to the Aliyun RDS CA bundle. The engine then connects with `check_hostname` enabled. Do not open the database to the public internet.

Each URL must use the matching user:

| Process | User | Default database |
| --- | --- | --- |
| Alembic | `hub_migrate` | `hub_ops` |
| Worker and scheduler | `hub_sync` | `hub_ops` |
| Internal API | `hub_api` plus `hub_auth` for credential lookup | `hub_ops` |
| Admin API | `hub_admin` | `hub_ops` |
| Analytics reads | `hub_analytics` | `hub_analytics` |

`hub_migrate` needs `GRANT OPTION` so the migration can grant the other users. After the first migration, that option can be removed if a DBA applies grants by hand.

## Backups

Use RDS automated backup and point-in-time recovery. Restoring an old snapshot can revive a queued job and reset quota counters. After a restore, mark running jobs as failed and review quota windows before serving traffic.

## Retention

`RAW_RETENTION_DAYS` and `AUDIT_RETENTION_DAYS` stay empty until the school sets them. Empty means nothing is deleted.
