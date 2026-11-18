# ADR-001: Arsitektur Modular Monolith & Fast Development Lokal

- **Status:** Accepted
- **Tanggal:** 2026-11-18
- **Konteks:** `CLAUDE.md` Bagian 2.1 & 4 (Fase 0).

## Keputusan
1. **Backend:** FastAPI (Python 3.12 via `uv`) sebagai *modular monolith* dengan kontrak plugin `QualityModule` (`backend/app/modules/base.py`).
2. **Mode Fast-Dev Lokal vs Kontainer:**
   - `docker-compose.yml` disediakan untuk integrasi di perangkat lain (`postgres` + `pgvector`, `redis`, `minio`, `api`, `worker`, `frontend`).
   - Untuk pengembangan cepat tanpa Docker, konfigurasi mendukung fallback lokal: SQLite async (`aiosqlite`), penyimpanan berkas lokal (`./.local_storage`), dan Celery *eager mode* (`CELERY_TASK_ALWAYS_EAGER=true`).
3. **Strategi Partisi `row_findings`:** Pada Fase 1, `row_findings` dimulai sebagai tabel berindeks `(run_id, severity, module)`, lalu ditingkatkan ke *PostgreSQL declarative partitioning* (`PARTITION BY LIST/HASH (run_id)`) untuk skala jutaan baris.
