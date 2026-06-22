# Chic A Boo — Backend

Four-service FastAPI monorepo. All external traffic enters through **Gateway**; **UserService**, **Backend**, and **Admin** are internal.

| Service      | Port | Prefix        |
|--------------|------|---------------|
| Gateway      | 8000 | `/`           |
| UserService  | 4001 | `/api/user`   |
| Backend      | 4002 | `/api`        |
| Admin        | 4003 | `/admin`      |

## Quick start

### Run all services (Docker)

```bash
docker compose up --build
```

### Run a single service locally

```bash
cd gateway   # or userservice, backend, admin
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

Use the port from the table above for each service.

### Health checks

- Gateway: `GET http://localhost:8000/health`
- UserService: `GET http://localhost:4001/health`
- Backend: `GET http://localhost:4002/health`
- Admin: `GET http://localhost:4003/health`

## Environment setup

Each service has a `.env` file (gitignored) and committed `.env.example` template.

```bash
# 1. Root shared config
cp .env.example .env

# 2. Per-service (already created for local dev — regenerate from templates if needed)
cp gateway/.env.example gateway/.env
cp userservice/.env.example userservice/.env
cp backend/.env.example backend/.env
cp admin/.env.example admin/.env
cp database/.env.example database/.env

# 3. JWT keys for local RS256 auth
mkdir -p keys
openssl genrsa -out keys/jwt_private.pem 2048
openssl rsa -in keys/jwt_private.pem -pubout -out keys/jwt_public.pem
```

### Connection matrix (local)

| Variable | UserService | Backend | Admin | Gateway |
|----------|-------------|---------|-------|---------|
| `DATABASE_URL` | `postgresql+asyncpg://…@localhost:5433/chicaboo` | same | same | — |
| `REDIS_URL` | `redis://localhost:6379` | same | same | same |
| `USER_SERVICE_URL` | — | `http://localhost:4001` | — | `http://localhost:4001` |
| `BACKEND_URL` | — | — | — | `http://localhost:4002` |
| `ADMIN_URL` | — | — | — | `http://localhost:4003` |

Docker Compose overrides hostnames (`postgres`, `redis`, `userservice`, etc.) automatically.

## Database

PostgreSQL 15 with three schemas: `auth`, `public`, `admin`. See [database/ARCHITECTURE.md](database/ARCHITECTURE.md) for the full production schema review.

```bash
# Start local Postgres + Redis
docker compose up -d postgres redis

# Apply migrations
cd database
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python migrate.py migrate
python migrate.py status
```

For Supabase, set `DATABASE_URL` in `database/.env` to your direct connection string (port 5432) and run migrate.

RLS-protected tables expect `SET LOCAL app.current_user_id = '<uuid>'` per transaction when querying as a customer.

## Structure

```
chicaboo-backend/
├── database/      # SQL migrations and migrate.py
├── gateway/       # Entry point — auth, rate-limit, routing
├── userservice/   # Auth & user identity
├── backend/       # E-commerce core (products, cart, orders)
└── admin/         # Dashboard & admin operations
```

See [structure.md](../structure.md) for full architecture details.
