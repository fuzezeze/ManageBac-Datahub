# Decisions

## Database is MySQL

The school will run this on Aliyun RDS MySQL. The first design used PostgreSQL schemas. The storage layout is now four databases on one MySQL 8 instance: `hub_raw`, `hub_core`, `hub_ops`, and `hub_analytics`.

Consequences:

- JSON columns replace JSONB. The raw response body stays `TEXT` so the original bytes are not re-serialized.
- Timestamps are UTC `DATETIME(6)` values. The connection sets `time_zone = '+00:00'`. Business dates such as attendance days stay `DATE` and are not shifted through UTC.
- A running sync is unique per school and dataset through a stored generated column, `running_scope`, which is NULL unless `status = 'running'`. MySQL unique indexes allow many NULLs, so queued jobs do not collide.
- Attendance's natural key uses `IFNULL(class_mb_id, '')` and `IFNULL(year_group_mb_id, '')` for the same reason: MySQL would otherwise treat two NULL class ids as different.
- Views are `SQL SECURITY DEFINER`. `hub_analytics` can read the views without reading base tables. `hub_api` and `hub_admin` are not granted `SELECT` on `attendance_records.note`.

## Course records are not synthesized

Public ManageBac API v2 exposes subjects and classes. There is no confirmed independent Course resource. The `courses` dataset is blocked and `GET /api/v1/courses` returns `DATASET_BLOCKED` rather than an empty success.

## Advisors are not class teachers

Year-group advisor payloads are stored in `hub_core.year_group_advisors`. They are not copied into `teacher_classes`.

## Fixture mode is not school data

`MB_AUTH_MODE=fixture` replays shapes from the public v2p3 docs so development and tests do not need a token. It is refused when `APP_ENV=production` unless explicitly allowed.
