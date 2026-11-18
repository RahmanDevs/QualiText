# Audit Progress Pengerjaan — QualiText

File ini mencatat status pengerjaan per batch untuk keperluan audit dan pelacakan sesuai `CLAUDE.md` Bagian 4.

## Fase 0 — Fondasi

| Batch | Deskripsi | Status | Catatan Audit |
|---|---|---|---|
| **Batch 1** | Monorepo, `.python-version` (3.12), `Makefile`, `docker-compose.yml`, `.env.example`, CI | `DONE` | `uv` terkunci di Python 3.12.14; `docker-compose.yml` + Dockerfile siap integrasi multi-device |
| **Batch 2** | Backend core (FastAPI, config, logging, errors, DB, Alembic, kontrak `QualityModule`) | `DONE` | Komentar arahan ditambahkan pada `app/modules/base.py` (streaming/batch) & `app/db/models.py` + migrasi `0001` (`row_findings` partitioning) |
| **Batch 3** | Auth dasar, RBAC, Audit Log, Storage/Worker kerangka, Seed, & Tests | `DONE` | `ruff`, `mypy --strict` (8 files), `alembic upgrade head`, `seed`, dan `pytest` (4 passed) lulus |
| **Batch 4** | Frontend App Shell, `tokens.css`, Light/Dark mode, i18n (`id`/`en`), komponen UI | `DONE` | `eslint`, `tsc --noEmit`, dan `vitest --run` (1 passed) lulus |
| **Batch 5** | Dokumentasi ADR (Bagian 2.13), `docs/dqi.md`, & verifikasi DoD Fase 0 | `DONE` | ADR-001, ADR-002 (open embedding + placeholder LLM), dan `docs/dqi.md` tersedia |

---

## Fase 1 — MVP Inti (Prioritas P0)

| Batch | Deskripsi | Status | Catatan Audit |
|---|---|---|---|
| **Batch 1** | DB Models Fase 1, Migrasi `0002`, Upload Chunked, Ingesti & Validasi (A1–A4) | `DONE` | Migrasi `0002_fase1_mvp_tables` diterapkan; parser JSONL/CSV/Parquet/TXT + mapping aktif |
| **Batch 2** | Quality Modules (A5 Token, B1–B3/B6 PII ID + Artefak + Diff, C1 Dedup) & DQI (F1) | `DONE` | Semua modul lolos `mypy --strict`; DQI parsial + critical cap 60 terverifikasi |
| **Batch 3** | API Explorer, Kurasi Immutable (F3), Quality Gate, & Ekspor (F5) + SSE | `DONE` | E2E `pytest` (`test_fase1_pipeline.py`) lulus penuh termasuk PII masking & immutable v2 |
| **Batch 4** | Frontend MVP (Datasets, Upload/Mapping, Run/DQI Report, Explorer/Kurasi, Export) | `DONE` | `eslint`, `tsc --noEmit`, dan `vitest --run` lulus |

## Fase 2 — v1.0 (Prioritas P1)

| Batch | Deskripsi | Status | Catatan Audit |
|---|---|---|---|
| **Batch 1** | Modul B4 (Bahasa), B5 (Toksisitas), C2–C4 (Dedup Semantik, Clustering 2D, N-gram) | `DONE` | Semua modul lolos `mypy --strict`; embedding deterministik offline siap |
| **Batch 2** | Modul D1–D3 (Kompleksitas, LLM-Judge Placeholder/OpenAI, Format) & E1 (Bias) + Full DQI | `DONE` | 5 dimensi penuh (`is_partial=False`) terhitung |
| **Batch 3** | F4 (Compare Versi), F6 (Laporan HTML), G1 (OIDC), G5 (API Token & Webhook), G6 (Helm) + UI & Tests | `DONE` | `pytest` (6 passed) & `vitest` (2 passed) lulus |

## Fase 3 — Pasca v1.0 (Prioritas P2)

| Batch | Deskripsi | Status | Catatan Audit |
|---|---|---|---|
| **Batch 1** | Konektor Sumber Data (A6), Kontaminasi Benchmark (C5), RLHF Preference (D4), RAG Chunk Quality (D5) | `DONE` | Semua modul lolos `mypy --strict`; `CONNECTOR_REGISTRY` & detector kontaminasi aktif |
| **Batch 2** | Risiko Halusinasi & Prompt Injection (E2–E3), Tren DQI (F8), & Verifikasi Penuh | `DONE` | `pytest` (8 passed) & `vitest` (2 passed) lulus |
