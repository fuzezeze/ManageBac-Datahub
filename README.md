# ManageBac Data Hub

只读同步 ManageBac，把学生、教学结构和考勤放进 MySQL，再通过带审计的 Internal API 给学校其他系统使用。云上目标是阿里云 RDS MySQL，不是 PostgreSQL。

## What is running locally

Four databases on one MySQL 8 instance:

| Database | Contents |
| --- | --- |
| `hub_raw` | Original API response text |
| `hub_core` | Normalized people, classes, memberships, attendance |
| `hub_ops` | Jobs, credentials, audit, quotas |
| `hub_analytics` | Read-only views. A student in two classes is still one row in `v_student_roster` |

Timestamps are stored as UTC `DATETIME`. MySQL has no `timestamptz`. A generated column keeps only one running job per dataset, because MySQL has no partial indexes.

## Local start

```bash
sudo mysql < scripts/init-mysql.sql
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
alembic upgrade head
hub seed
uvicorn hub.web.app:app --port 8000
```

`MB_AUTH_MODE=fixture` loads a recorded-shaped sample so the app runs before the school API token exists. Production refuses fixture mode unless `MB_ALLOW_FIXTURE=true`.

Admin sign-in uses `ADMIN_EMAIL` and `ADMIN_PASSWORD`. The sample password in `.env.example` must be changed before any shared environment.

The admin UI lives in `apps/admin`:

```bash
cd apps/admin && npm install && npm run dev
```

## Still needs the school

These are configuration, not guessed production facts:

- ManageBac base URL (`api.managebac.com` or `api.managebac.cn`) and whether the token is OAuth or `auth-token`
- API prefix, default `/v2p3`
- Confirmed IANA timezone (`SCHOOL_TIMEZONE_CONFIRMED=true` only after the school confirms it)
- Attendance history start, status-code list, and which of homeroom or class attendance is enabled
- Retention days. Empty means the app does not delete
- Admin OIDC issuer. Local password is the stand-in until that exists
- RDS account, region, and the CA bundle in `DATABASE_SSL_CA`

`courses` stays blocked. The public API has subjects and classes, not an independent course resource, and this hub does not invent one.

## RDS MySQL

See `docs/operations.md`. Connect with TLS hostname verification. Keep the database private. Give each process its own MySQL user from `scripts/init-mysql.sql`, then change the passwords.
