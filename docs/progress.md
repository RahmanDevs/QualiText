# Audit Progress Pengerjaan - QualiText

File ini mencatat status pengerjaan aktual per fase dan tahap berdasarkan kode sumber terverifikasi terhadap `CLAUDE.md`.

## Fase 0-4 (Baseline & Tahap 1-4)

| Tahap | Cakupan Utama | Status | Catatan Audit Kode |
|---|---|---|---|
| **Fase 0** | Monorepo, auth JWT, RBAC 5 role, audit log, storage S3/local, app shell, i18n (`id`/`en`) | `DONE` | Fondasi API + frontend + ADR-001/002 + `docs/dqi.md` tersedia. |
| **Tahap 1** | Hotfix & security blockers (`OPS-01`, `SEC-01`-`SEC-03`, `BUG-01`-`BUG-05`) | `DONE` | `httpx` di dependencies utama, redaksi PII di `curation_actions`, proteksi IDOR & Quality Gate, XSS escape HTML, `blake2b` dedup, SFT editor, `test_stage1_security_and_hotfixes.py` (6 backend) & `Stage1Hotfixes.test.tsx` (2 frontend). |
| **Tahap 2** | Arsitektur worker Celery/Redis, streaming upload, migrasi `0003`, P0 A-F + UI Explorer | `DONE` | `execute_quality_run_task` + pub/sub SSE, `SpooledTemporaryFile`, bulk insert finding 1000 baris, ekspor Hugging Face `.zip` + manifest, Column Mapping UI, Validation Errors UI/download, histogram token, `PII_PERSON`/`PII_ADDRESS`, `hash_pii`/`drop_row`, `VirtualizedDataTable`, keyboard shortcuts, i18n penuh. |
| **Tahap 3** | Penuntasan v1.0 (P1) & Enterprise Readiness (`G1`, `G4`, `G5`, `G6`, `SEC-04`, `SEC-05`, `C2`, `C3`, `D2`, `E1`, `F6`, `F7`, `F8`) | `DONE` | Token `qt_...` + scope check, OIDC/SAML SSO, rotasi refresh token + reuse detection, rate limit `/auth/login`, production `SECRET_KEY` guard, `is_local_llm_url`, Webhooks anti-SSRF + HMAC, audit log lengkap + CSV/JSON export, `/readyz` + `/metrics` Prometheus + OTel, migrasi `0004`, cache embedding + LSH/FAISS hook + canonical terpanjang + 2D PCA cluster, rubrik LLM-Judge & bias ID/EN, laporan PDF, policy per dimensi + custom bobot/cap DQI, UI live Overview/Dedup Review/Settings/CommandPalette, Helm `templates/`, Nginx `frontend/Dockerfile`, dan CI security/a11y. |
| **Tahap 4** | Pendalaman P2, UU PDP, Streaming Validator & Benchmarks (`A1`, `A6`, `B1`, `C5`, `D4`, `D5`, `E2`, `E3`, `ARCH-01`) | `DONE` | Migrasi `0005` (`retention_days` + `benchmark_sets`), `DatabaseConnector` read-only + `SharePointConnector` OAuth HTTPS anti-SSRF gated admin (`/import-connector`), CRUD/upload `BenchmarkSet` + integrasi `run_service` (`C5`), hak hapus UU PDP `DELETE /datasets/{id}` + `retention` policy & purge (`workspaces`), `validate_content_magic` + `parse_and_validate_stream` + kolom eksplisit `chosen`/`rejected`, upgrade modul `D4`/`D5`/`E2`/`E3` (taxonomy `HARMFUL_CONTENT_RISK` + opt-in local LLM judge), penyelarasan metrik eksplisit `dqi.py`, kalibrasi PII ID, font offline air-gapped tanpa CDN Google Fonts, modal Konektor/Hapus Dataset UI, tab Settings Benchmarks & Retensi UU PDP (`id`/`en`), `seed.py` demo korpus/benchmark sintetis, serta `scripts/benchmark_models.py` & `docs/benchmarks.md` (`test_stage4_p2_and_benchmarks.py` + `Stage4P2Features.test.tsx`). |

## Catatan Lingkungan Produksi (Di Luar Kode Aplikasi Offline)

- Eksekusi langsung cluster PostgreSQL 16 + `pgvector` partisi HASH, MinIO/S3, Redis + Celery multi-worker, Trivy/Playwright CI, dan benchmark skala klaster 100 ribu-5 juta baris atau model `sentence-transformers` + `faiss` dijalankan di infrastruktur/CI berjaringan target.
