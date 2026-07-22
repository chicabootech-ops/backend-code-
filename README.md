# Chic A Boo — Unified Backend API

One FastAPI process for **identity** (`/api/user`), **storefront** (`/api/*`), and **admin** (`/admin`).

| Surface | Prefix | Port |
|---------|--------|------|
| Unified API | `/api`, `/admin` | **8000** |

## Quick start

```bash
# From chicaboo-backend/
cp .env.example .env
# Fill DATABASE_URL, Redis, JWT keys, ADMIN_JWT_SECRET, …

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# JWT keys (local)
mkdir -p keys
openssl genrsa -out keys/jwt_private.pem 2048
openssl rsa -in keys/jwt_private.pem -pubout -out keys/jwt_public.pem

uvicorn app.main:app --reload --port 8000
```

Or from monorepo root: `./start.sh` (starts API + frontends).

### Docker

```bash
docker compose up --build
```

Services: `postgres`, `redis`, `migrate`, `api`.

### Health

- `GET http://localhost:8000/health`
- `GET http://localhost:8000/api/categories`
- `GET http://localhost:8000/api/sections`

## Database

Migrations live in [`database/`](database/). Apply with:

```bash
cd database && python migrate.py migrate
```

## Layout

```
app/
  main.py           # entry
  config.py         # single Settings / .env
  identity/         # user auth & profile
  storefront/       # catalog, cart stubs, payments stubs
  admin_api/        # admin dashboard API
shared/             # PEM helpers
database/           # SQL migrations
```

## Render

Deploy **one** Web Service from this repo:

- Dockerfile: root `Dockerfile`
- Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Env: paste from `.env.example` (production values)
- Point frontends `NEXT_PUBLIC_API_URL` at this service
