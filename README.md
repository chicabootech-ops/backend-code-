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

## Structure

```
chicaboo-backend/
├── gateway/       # Entry point — auth, rate-limit, routing
├── userservice/   # Auth & user identity
├── backend/       # E-commerce core (products, cart, orders)
└── admin/         # Dashboard & admin operations
```

See [structure.md](../structure.md) for full architecture details.
