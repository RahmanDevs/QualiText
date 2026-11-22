# QualiText

Platform Data Quality untuk **NLP & LLM** (Supervised Fine-Tuning, RLHF/preference, dan pipeline RAG).

## Quickstart (Fast Development Lokal)

Prasyarat: `uv` (Python 3.12), `node` (18+), `npm`, `make`.

```bash
make setup    # Install dependency backend (uv) & frontend (npm)
cp .env.example .env
make migrate  # Jalankan migrasi database (Alembic)
make seed     # Isi data contoh sintetis (tanpa PII asli)
make dev      # Jalankan API (FastAPI) + Frontend (Vite) secara lokal
```

## Docker Compose (Untuk Perangkat / Lingkungan Terkontainerisasi)

```bash
docker compose up --build
```
