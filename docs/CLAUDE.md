# CLAUDE.md — QualiText: Platform Data Quality untuk NLP & LLM

> **Nama kerja:** QualiText (dapat diganti kapan saja).
> **Bahasa dokumen:** Indonesia. Kode, nama variabel, commit message, dan komentar kode memakai **bahasa Inggris**. Teks antarmuka (UI) memakai **bahasa Indonesia** dengan dukungan i18n untuk bahasa Inggris.
> **Cara membaca file ini:** Bagian 0 adalah aturan kerja untuk Claude. Bagian 1 adalah PRD, Bagian 2 adalah TRD, Bagian 3 adalah panduan desain, Bagian 4 adalah roadmap. Selalu baca Bagian 0 sebelum mengerjakan tugas apa pun.

---

## 0. Aturan Kerja untuk Claude

### 0.1 Prinsip Utama
1. **Ikuti dokumen ini sebagai sumber kebenaran.** Jika ada permintaan yang bertentangan dengan PRD/TRD, tanyakan dulu atau usulkan perubahan dokumen, jangan menyimpang diam-diam.
2. **Kerjakan per fase** sesuai Bagian 4. Jangan membangun fitur fase berikutnya sebelum fase berjalan memenuhi *Definition of Done*.
3. **Privasi dulu.** Dataset pengguna adalah data korporat yang sensitif. Jangan pernah mengirim isi dataset ke layanan eksternal (LLM API publik, telemetri, analytics) tanpa konfigurasi eksplisit dari admin. Default: **semua pemrosesan berjalan lokal / self-hosted**.
4. **Desain modern dan profesional adalah syarat, bukan tambahan.** Semua UI wajib mengikuti Bagian 3.
5. **Sederhana dulu, bisa diskalakan kemudian.** Pilih solusi paling sederhana yang memenuhi kebutuhan saat ini, dengan batas modul yang jelas agar mudah diganti.

### 0.2 Alur Kerja per Tugas
1. Baca bagian PRD/TRD yang relevan.
2. Tulis rencana singkat (file yang disentuh, pendekatan, risiko) sebelum mengubah banyak file.
3. Implementasi kecil dan bertahap, satu perubahan logis per commit.
4. Tulis atau perbarui **test** bersama kodenya.
5. Jalankan lint, type-check, dan test sebelum menyatakan selesai.
6. Perbarui dokumentasi (`docs/`, OpenAPI, dan file ini bila ada keputusan arsitektur baru).

### 0.3 Konvensi Kode

**Backend (Python 3.11+)**
- Framework: FastAPI. Validasi dengan Pydantic v2.
- Format dan lint: `ruff` (lint + format). Type-check: `mypy --strict` untuk `app/core` dan `app/modules`.
- Semua fungsi publik wajib type hint dan docstring singkat.
- Gunakan `async` untuk I/O. Pekerjaan berat CPU/GPU (embedding, LLM-judge, PII scan skala besar) **wajib** lewat worker, bukan di request handler.
- Tiap modul kualitas mengimplementasikan interface yang sama (lihat 2.4). Dilarang menaruh logika bisnis di router.
- Penamaan: `snake_case` fungsi/variabel, `PascalCase` kelas, `UPPER_SNAKE_CASE` konstanta.

**Frontend (TypeScript)**
- React 18 + Vite + TypeScript `strict`.
- Styling: Tailwind CSS + shadcn/ui (Radix). Dilarang menulis warna/ukuran mentah di komponen; gunakan design tokens (Bagian 3).
- State server: TanStack Query. State UI lokal: Zustand bila perlu. Tabel: TanStack Table + virtualisasi.
- Lint/format: ESLint + Prettier. Test: Vitest + Testing Library; E2E: Playwright.
- Komponen: fungsional, satu komponen per file, `PascalCase.tsx`. Hook diawali `use`.

**Umum**
- Commit mengikuti Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`).
- Jangan commit secret, dataset nyata, atau file `.env`. Sediakan `.env.example`.
- Tidak ada `print`/`console.log` tersisa; gunakan logger terstruktur (JSON).

### 0.4 Perintah Standar (sesuai `Makefile`)
```bash
make setup        # install dependency backend + frontend
make dev          # jalankan stack fast-dev lokal (API FastAPI + Frontend Vite)
make docker-up    # jalankan stack lengkap via Docker Compose
make docker-down  # hentikan stack Docker Compose
make test         # test backend + frontend
make lint         # ruff, mypy, eslint, tsc
make migrate      # jalankan migrasi database (Alembic)
make seed         # isi data contoh (dataset sintetis, tanpa PII asli)
```

### 0.5 Definition of Done (setiap fitur)
- [ ] Memenuhi acceptance criteria di PRD.
- [ ] Test unit/integrasi lulus; cakupan modul baru ≥ 80%.
- [ ] Lint dan type-check bersih.
- [ ] UI sesuai Bagian 3, termasuk state *loading*, *empty*, dan *error*.
- [ ] Aksesibilitas dasar terpenuhi (keyboard, kontras, label).
- [ ] Aksi penting tercatat di audit log.
- [ ] Dokumentasi/OpenAPI diperbarui.

### 0.6 Hal yang Dilarang
- Mengirim isi dataset ke API eksternal tanpa opt-in admin.
- Menyimpan PII mentah di log, pesan error, atau cache.
- Menambah dependency besar tanpa alasan tertulis di PR.
- Menimpa dataset asli. Semua transformasi menghasilkan **versi baru** (data immutable).
- Menggunakan warna, font, atau spasi di luar design tokens.

---

## 1. PRD — Product Requirements Document

### 1.1 Ringkasan Produk
**QualiText** adalah platform web untuk menilai, membersihkan, dan memantau kualitas dataset teks yang dipakai untuk **NLP dan LLM** (Supervised Fine-Tuning, RLHF/preference, dan pipeline RAG). Platform ini memberi skor kualitas terukur (**Data Quality Index / DQI**), menandai baris bermasalah, dan menyediakan alur kurasi *human-in-the-loop* sebelum dataset diekspor siap pakai.

Prinsip yang mendasari: *garbage in, garbage out*. Kualitas data NLP mencakup aspek semantik, struktur teks, toksisitas, privasi, dan keberagaman, bukan hanya nilai kosong dan tipe data seperti data tabular.

### 1.2 Latar Belakang dan Masalah
Tim korporat yang menyiapkan data untuk LLM menghadapi masalah berikut:
- Data mentah berasal dari banyak sumber (tiket CS, dokumen internal, chat log, hasil anotasi vendor) dengan format dan kualitas yang tidak seragam.
- Kebocoran PII (nama, NIK, nomor telepon, email, alamat) ke dalam data latih menimbulkan risiko hukum dan reputasi (termasuk kepatuhan UU PDP).
- Duplikat dan data hampir-identik membuang biaya pelatihan dan memicu *overfitting*.
- Tidak ada ukuran kualitas yang baku, sehingga keputusan "dataset ini layak latih atau tidak" bersifat subjektif.
- Proses pembersihan tersebar di skrip ad-hoc tanpa jejak audit dan sulit direproduksi.

### 1.3 Tujuan dan Metrik Keberhasilan

| Tujuan | Metrik | Target (6 bulan setelah rilis v1.0) |
|---|---|---|
| Mempercepat penilaian dataset | Waktu dari unggah hingga laporan kualitas pertama untuk 100 ribu baris | ≤ 10 menit |
| Mengurangi kebocoran PII | Persentase PII terdeteksi pada set uji berlabel (recall) | ≥ 95% untuk entitas utama |
| Mengurangi redundansi | Duplikat semantik terdeteksi (precision pada sampel audit) | ≥ 90% |
| Adopsi | Jumlah dataset dinilai per bulan per organisasi | ≥ 20 |
| Kepuasan | CSAT/NPS pengguna inti | NPS ≥ 40 |
| Reproduktibilitas | Run penilaian yang bisa diulang dengan hasil identik (seed + versi konfigurasi) | 100% |

**Non-goal (v1.0):** pelatihan atau fine-tuning model itu sendiri, pelabelan data dari nol (annotation tool penuh), dan penanganan data non-teks (gambar, audio).

### 1.4 Target Pengguna dan Persona

**Pengguna utama: profesional Data Quality di lingkungan korporat.**

| Persona | Peran | Kebutuhan utama |
|---|---|---|
| **Dina, Data Quality Analyst** | Menjalankan penilaian dan membuat laporan | Skor jelas, drill-down per baris, ekspor laporan untuk manajemen |
| **Raka, Data Steward / Curator** | Meninjau baris bermasalah dan memutuskan perbaikan | Antarmuka review cepat, aksi massal, riwayat keputusan |
| **Sari, Data Governance / Compliance Lead** | Memastikan kepatuhan privasi dan kebijakan | Laporan PII, audit log, kebijakan ambang batas yang bisa ditegakkan |
| **Budi, ML/Data Engineer** | Mengonsumsi dataset bersih untuk pelatihan | API/ekspor otomatis, format Hugging Face/JSONL, versi dataset |
| **Admin Platform** | Mengelola pengguna dan infrastruktur | SSO, RBAC, konfigurasi self-hosted, monitoring |

### 1.5 Ruang Lingkup Fitur (Modul)

Prioritas: **P0** = wajib MVP, **P1** = wajib v1.0, **P2** = pasca v1.0.

#### Modul A — Ingesti & Validasi Struktur
| ID | Fitur | Prioritas |
|---|---|---|
| A1 | Unggah/impor JSONL, CSV, Parquet, Markdown, PDF, dan teks mentah (drag-and-drop, hingga 5 GB lewat unggah *chunked*) | P0 |
| A2 | Dukungan format *chat template* (`messages` dengan peran `system/user/assistant`) dan pasangan `prompt/response` | P0 |
| A3 | *Schema & syntax checker*: JSON valid, kolom wajib ada, pasangan prompt-response lengkap, nilai kosong/rusak | P0 |
| A4 | Pemetaan kolom (*column mapping*) untuk skema kustom | P0 |
| A5 | *Token length analytics* (histogram, persentil, risiko *truncation*) dengan tokenizer yang dapat dipilih (tiktoken / Hugging Face) | P0 |
| A6 | Konektor sumber data (S3/MinIO, database, SharePoint) | P2 |

#### Modul B — Pembersihan & Sanitasi
| ID | Fitur | Prioritas |
|---|---|---|
| B1 | **Deteksi dan masking PII** (nama, telepon, email, alamat, NIK, NPWP, nomor rekening) dengan recognizer khusus Indonesia (NIK 16 digit, nomor +62, NPWP) | P0 |
| B2 | Mode PII: *detect-only*, *mask* (`[NAMA]`), *hash*, *drop row* | P0 |
| B3 | Pembersihan artefak: tag HTML, markdown rusak, *zero-width space*, *mojibake*, normalisasi Unicode | P0 |
| B4 | Deteksi bahasa per baris dan pelaporan konsistensi korpus (termasuk campur Indonesia–Inggris) | P1 |
| B5 | Filter kata kasar, ujaran kebencian, toksisitas (leksikon Indonesia/Inggris + model klasifikasi) | P1 |
| B6 | Pratinjau perubahan (*before/after diff*) sebelum diterapkan | P0 |
| B7 | Analisis kualitas linguistik: *perplexity* (character LM + hook KenLM/HF), *gibberish*/OCR noise, rasio kata valid vs *out-of-vocabulary (OOV)* | P2 |

#### Modul C — Semantik & Keberagaman
| ID | Fitur | Prioritas |
|---|---|---|
| C1 | Deduplikasi eksak (hash) dan hampir-eksak (MinHash/LSH) | P0 |
| C2 | **Deduplikasi semantik** (embedding + cosine similarity, ambang batas dapat diatur) dengan pengelompokan duplikat dan pilihan baris yang dipertahankan | P1 |
| C3 | Clustering topik dan visualisasi sebaran (proyeksi 2D) untuk menilai keberagaman | P1 |
| C4 | Analisis *n-gram overlap* dan repetisi frasa | P1 |
| C5 | Deteksi kontaminasi terhadap benchmark/evaluation set yang diunggah | P2 |

#### Modul D — Penilaian Kualitas Instruksi & SFT
| ID | Fitur | Prioritas |
|---|---|---|
| D1 | *Instruction complexity scoring* (ekstraksi sederhana, penalaran multi-langkah, penulisan kreatif, dll.) | P1 |
| D2 | *Response alignment & helpfulness* dengan **LLM-as-a-judge lokal** (model open-source, dapat dikonfigurasi) dan rubrik yang dapat diedit | P1 |
| D3 | *Formatting compliance*: blok kode tertutup, JSON dalam response valid, ekspektasi format sesuai instruksi | P1 |
| D4 | Dukungan data preferensi (chosen/rejected) untuk RLHF/DPO: cek konsistensi pasangan | P2 |
| D5 | Penilaian kualitas *chunk* RAG (panjang, koherensi, kelengkapan konteks), *semantic chunking* & *metadata enrichment* otomatis | P2 |
| D6 | Verifikasi VLM & struktural halaman PDF terhadap teks/tabel hasil ekstraksi serta generator pasangan QA/SFT otomatis | P2 |

#### Modul E — Keamanan, Bias, Halusinasi
| ID | Fitur | Prioritas |
|---|---|---|
| E1 | *Stereotype & bias scanner* (gender, ras/etnis, agama, sosio-ekonomi) dengan laporan distribusi | P1 |
| E2 | *Hallucination risk tagging* (heuristik + LLM-judge; tampilkan sebagai **risiko**, bukan kepastian) | P2 |
| E3 | Deteksi *prompt injection* dan konten berbahaya dalam data latih | P2 |

#### Modul F — Dashboard, Kurasi, Ekspor
| ID | Fitur | Prioritas |
|---|---|---|
| F1 | **Data Quality Index (DQI)** 0–100 per dataset dan per dimensi, beserta rincian kontribusi | P0 |
| F2 | **Interactive Data Explorer**: grid virtualisasi, filter berdasarkan skor/flag/dimensi, pencarian, penyortiran | P0 |
| F3 | Kurasi *human-in-the-loop*: tandai (*flag*), setujui, tolak, sunting baris; aksi massal; riwayat per baris | P0 |
| F4 | **Versi dataset** (immutable) dan perbandingan antar versi | P1 |
| F5 | **Export pipeline**: JSONL, CSV, Parquet, format Hugging Face Dataset, `rag_vector` JSONL, `sft_chat` ChatML JSONL; opsi ekspor hanya baris yang lolos ambang batas | P0 |
| F6 | **Laporan kualitas** dapat diunduh (PDF/HTML) untuk audit dan manajemen | P1 |
| F7 | **Kebijakan kualitas** (*quality gate*): ambang batas DQI/dimensi yang harus dipenuhi sebelum ekspor | P1 |
| F8 | Pemantauan tren DQI antar versi/waktu | P2 |

#### Modul G — Platform & Enterprise
| ID | Fitur | Prioritas |
|---|---|---|
| G1 | Autentikasi (email+password untuk MVP) dan **SSO (OIDC/SAML)** | P0 / P1 |
| G2 | **RBAC**: Admin, Governance, Curator, Analyst, Viewer | P0 |
| G3 | Workspace/proyek terpisah per tim | P0 |
| G4 | **Audit log** tak dapat diubah untuk aksi sensitif (unggah, lihat PII, ekspor, ubah kebijakan) | P0 |
| G5 | API publik terdokumentasi (REST + token API) dan webhook selesai-proses | P1 |
| G6 | Penyebaran self-hosted (Docker Compose, kemudian Helm/Kubernetes) | P0 / P1 |

### 1.6 User Stories dan Acceptance Criteria (inti)

**US-1 — Unggah dan validasi**
*Sebagai Dina, saya ingin mengunggah dataset JSONL dan langsung melihat ringkasan validitasnya.*
- Given file JSONL valid, saat diunggah, maka sistem menampilkan jumlah baris, jumlah baris tidak valid, dan distribusi token dalam ≤ 60 detik untuk 100 ribu baris.
- Given baris tidak valid, maka sistem menampilkan nomor baris, jenis kesalahan, dan memungkinkan unduh daftar kesalahan.
- Given file melebihi batas, maka sistem menolak dengan pesan yang jelas dan tindakan yang disarankan.

**US-2 — Skor kualitas**
*Sebagai Dina, saya ingin satu skor DQI yang bisa dijelaskan.*
- Dataset yang selesai dinilai menampilkan DQI 0–100, skor per dimensi, dan kontribusi tiap dimensi terhadap skor akhir.
- Mengklik dimensi membuka daftar baris yang menurunkan skor tersebut.
- Dua run dengan dataset, konfigurasi, dan seed yang sama menghasilkan DQI identik.

**US-3 — PII**
*Sebagai Sari, saya ingin memastikan tidak ada PII lolos ke data latih.*
- Sistem mendeteksi dan menandai entitas PII beserta posisinya dan tingkat keyakinan.
- PII mentah **tidak** tampil di log; di UI, tampil tersamar kecuali pengguna memiliki izin `pii:view` (dan aksinya tercatat di audit log).
- Ekspor dengan kebijakan "blokir bila PII tersisa" menolak ekspor dan menampilkan alasannya.

**US-4 — Kurasi**
*Sebagai Raka, saya ingin meninjau dan memperbaiki baris bermasalah dengan cepat.*
- Pengguna dapat memfilter, memilih banyak baris, lalu menerapkan aksi massal (setujui/tolak/tandai).
- Penyuntingan baris menyimpan versi baru dan mencatat siapa mengubah apa dan kapan.
- Pintasan keyboard tersedia untuk navigasi dan aksi umum.

**US-5 — Deduplikasi**
*Sebagai Raka, saya ingin melihat kelompok duplikat dan memilih baris terbaik.*
- Sistem menampilkan kelompok duplikat beserta skor kemiripan dan menyarankan baris yang dipertahankan.
- Ambang batas kemiripan dapat diubah dan hasil pratinjau diperbarui tanpa memproses ulang seluruh dataset.

**US-6 — Ekspor**
*Sebagai Budi, saya ingin mengekspor dataset bersih siap latih.*
- Ekspor tersedia dalam JSONL, CSV, Parquet, dan Hugging Face Dataset format.
- Ekspor mencantumkan metadata (versi dataset, versi konfigurasi, DQI, tanggal) dalam berkas manifest.
- Ekspor dapat dipicu lewat API dengan token.

### 1.7 Kebutuhan Non-Fungsional
| Kategori | Persyaratan |
|---|---|
| **Kinerja** | Penilaian 100 ribu baris ≤ 10 menit pada spesifikasi referensi (lihat 2.10); UI tetap responsif (interaksi ≤ 200 ms) pada tabel jutaan baris lewat virtualisasi dan paginasi sisi server |
| **Skalabilitas** | Worker dapat diskalakan horizontal; dataset hingga 5 juta baris pada v1.0 |
| **Keamanan** | Enkripsi *in transit* (TLS) dan *at rest*; RBAC; audit log; manajemen secret; patuh OWASP ASVS L2 sebagai acuan |
| **Privasi** | Pemrosesan lokal secara default; dukungan retensi dan penghapusan data; selaras UU PDP |
| **Ketersediaan** | Target 99,5% untuk penyebaran produksi; job yang gagal dapat dilanjutkan/di-*retry* |
| **Reproduktibilitas** | Setiap run menyimpan versi dataset, konfigurasi, versi model, dan seed |
| **Aksesibilitas** | WCAG 2.1 AA |
| **Internasionalisasi** | UI Indonesia (default) dan Inggris; mendukung teks Unicode penuh |
| **Observabilitas** | Log terstruktur, metrik (Prometheus), tracing (OpenTelemetry) |

### 1.8 Risiko dan Mitigasi
| Risiko | Dampak | Mitigasi |
|---|---|---|
| Deteksi PII Bahasa Indonesia kurang akurat | Kebocoran data | Recognizer khusus lokal, set uji berlabel, mode *human review*, laporkan recall/precision secara terbuka |
| LLM-as-a-judge tidak konsisten/bias | Skor menyesatkan | Rubrik eksplisit, temperature rendah, sampel audit manusia, tampilkan kepercayaan skor |
| Biaya komputasi embedding/LLM tinggi | Lambat dan mahal | Pemrosesan *batch*, cache embedding, sampling untuk dataset besar, opsi GPU |
| False positive tinggi memicu kelelahan peninjau | Adopsi rendah | Ambang batas yang dapat dikonfigurasi, pengurutan berdasarkan keyakinan, umpan balik untuk tuning |
| Skor DQI dianggap "kotak hitam" | Kurang dipercaya | Rincian kontribusi, penjelasan per baris, dokumentasi rumus |

---

## 2. TRD — Technical Requirements Document

### 2.1 Arsitektur Tingkat Tinggi

```
┌──────────────────────────────────────────────────────────────────┐
│  Frontend (React + TS + Tailwind + shadcn/ui)                    │
└───────────────▲──────────────────────────────────────────────────┘
                │ HTTPS (REST/JSON, SSE untuk progres job)
┌───────────────┴──────────────┐      ┌────────────────────────────┐
│  API Service (FastAPI)       │─────▶│  PostgreSQL (metadata,     │
│  auth · RBAC · orchestrasi   │      │  hasil, audit log)         │
└───────┬──────────────────────┘      └────────────────────────────┘
        │ enqueue job                  ┌────────────────────────────┐
        ▼                              │  Object Storage (S3/MinIO) │
┌──────────────────────────────┐◀─────▶│  dataset mentah, versi,    │
│  Redis (queue + cache)       │       │  ekspor, laporan           │
└───────┬──────────────────────┘       └────────────────────────────┘
        ▼
┌──────────────────────────────┐      ┌────────────────────────────┐
│  Worker Pool (Celery)        │─────▶│  Vector Index (pgvector /  │
│  modul kualitas (A–E)        │      │  FAISS) untuk dedup/cluster│
│  CPU workers · GPU workers   │      └────────────────────────────┘
└──────────────────────────────┘
        │ lokal
        ▼
  Model server lokal (embedding, LLM-judge, klasifikasi toksisitas)
```

**Prinsip arsitektur:** modular monolith untuk API (bukan microservice di awal), worker terpisah untuk beban berat, penyimpanan dataset sebagai file kolumnar (Parquet) di object storage, metadata dan hasil agregat di PostgreSQL.

### 2.2 Tech Stack

| Lapisan | Pilihan | Catatan |
|---|---|---|
| Backend API | **Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2 (async), Alembic** | Ekosistem NLP terkuat di Python |
| Job queue | **Celery + Redis** | Alternatif: Dramatiq/ARQ bila perlu lebih ringan |
| Database | **PostgreSQL 16** (+ `pgvector` untuk MVP) | Pertimbangkan Qdrant/FAISS terpisah bila dataset > jutaan vektor |
| Object storage | **S3-compatible (MinIO untuk self-hosted)** | Dataset, versi, ekspor, laporan |
| Pemrosesan data | **Polars / PyArrow** (utama), `pandas` hanya bila perlu | Polars untuk kinerja dan memori |
| NLP | `spaCy`, `Hugging Face Datasets/Tokenizers`, `tiktoken`, `langdetect`/`fastText lid` | |
| PII | **Microsoft Presidio** + recognizer kustom Indonesia (NIK, NPWP, +62, rekening) | |
| Embedding | `sentence-transformers` (model multilingual, mis. keluarga `multilingual-e5` / `paraphrase-multilingual`) | Model dapat dikonfigurasi; verifikasi dukungan Bahasa Indonesia sebelum dipilih |
| Near-duplicate | `datasketch` (MinHash/LSH) | |
| Vector search | `FAISS` / `pgvector` | |
| LLM-as-a-judge | Model open-source lokal lewat **vLLM** atau **Ollama** (antarmuka kompatibel OpenAI) | Tidak ada data keluar jaringan secara default |
| Frontend | **React 18, Vite, TypeScript, Tailwind CSS, shadcn/ui, TanStack Query/Table, Recharts, Zustand** | Streamlit **tidak** dipakai karena target pengguna korporat butuh UI kelas produksi |
| Auth | OIDC/SAML (Keycloak / provider korporat), JWT berumur pendek + refresh | |
| Observabilitas | OpenTelemetry, Prometheus, Grafana, Sentry (self-hosted) | |
| Infrastruktur | Docker, Docker Compose (v1), Helm/Kubernetes (v1.x) | |
| CI/CD | GitHub Actions / GitLab CI | |

> Versi pustaka tidak dikunci di dokumen ini. Tentukan dan kunci versi pada Fase 0 (`pyproject.toml` + lockfile, `package.json` + lockfile) setelah memeriksa versi stabil terbaru.

### 2.3 Struktur Repositori (monorepo)

```
qualitext/
├── CLAUDE.md
├── README.md
├── Makefile
├── docker-compose.yml
├── .env.example
├── docs/                      # ADR, diagram, panduan operasional
│   └── adr/
├── backend/
│   ├── pyproject.toml
│   ├── alembic/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/               # router tipis (v1/…)
│   │   ├── core/              # config, security, logging, deps, errors
│   │   ├── db/                # model SQLAlchemy, session
│   │   ├── schemas/           # Pydantic request/response
│   │   ├── services/          # logika aplikasi (dataset, run, export, audit)
│   │   ├── modules/           # modul kualitas (plugin)
│   │   │   ├── base.py        # interface QualityModule
│   │   │   ├── ingestion/     # Modul A
│   │   │   ├── cleaning/      # Modul B (pii, artifacts, language, toxicity)
│   │   │   ├── semantic/      # Modul C (dedup, clustering, ngram)
│   │   │   ├── instruction/   # Modul D (complexity, judge, formatting)
│   │   │   └── safety/        # Modul E (bias, hallucination)
│   │   ├── scoring/           # perhitungan DQI
│   │   ├── workers/           # task Celery
│   │   └── storage/           # abstraksi S3/MinIO
│   └── tests/
├── frontend/
│   ├── package.json
│   ├── src/
│   │   ├── app/               # routing, providers
│   │   ├── components/
│   │   │   ├── ui/            # primitif shadcn/ui
│   │   │   └── shared/        # komponen domain (ScoreCard, DataGrid, …)
│   │   ├── features/          # datasets, runs, explorer, curation, policies, admin
│   │   ├── hooks/
│   │   ├── lib/               # api client, utils, i18n
│   │   ├── styles/            # tokens.css, globals.css
│   │   └── locales/           # id.json, en.json
│   └── tests/
└── deploy/                    # helm chart, contoh konfigurasi produksi
```

### 2.4 Desain Modul Kualitas (Plugin)

Setiap modul mengimplementasikan interface yang sama agar mudah ditambah, diuji, dan dinonaktifkan.

```python
# backend/app/modules/base.py  (kontrak, bukan implementasi final)
from typing import Protocol
import polars as pl
from pydantic import BaseModel

class ModuleConfig(BaseModel):
    """Konfigurasi spesifik modul, divalidasi Pydantic dan diversikan."""

class RowFinding(BaseModel):
    row_id: str
    module: str            # mis. "pii", "dedup_semantic"
    code: str              # mis. "PII_NIK", "DUP_SEMANTIC"
    severity: str          # "info" | "warning" | "critical"
    score: float | None    # 0..1 bila relevan
    span: tuple[int, int] | None
    detail: dict           # tanpa PII mentah

class ModuleResult(BaseModel):
    module: str
    dimension: str         # dimensi DQI yang dipengaruhi
    metrics: dict[str, float]
    findings_count: int

class QualityModule(Protocol):
    name: str
    dimension: str
    version: str

    def run(self, data: pl.LazyFrame, config: ModuleConfig, seed: int) -> tuple[ModuleResult, pl.DataFrame]:
        """Mengembalikan ringkasan modul dan tabel temuan per baris."""
```

Aturan modul:
- **Deterministik** untuk `(data, config, seed, versi model)` yang sama.
- **Streaming/batch**: tidak memuat seluruh dataset ke memori bila tidak perlu.
- **Tidak menulis PII mentah** ke `detail`; simpan tipe entitas dan posisi saja.
- Memiliki unit test dengan dataset sintetis dan kasus tepi.

### 2.5 Model Data (PostgreSQL, ringkas)

| Tabel | Kolom utama |
|---|---|
| `organizations`, `workspaces` | id, nama, pengaturan |
| `users`, `memberships` | id, email, peran per workspace |
| `datasets` | id, workspace_id, nama, deskripsi, tipe (sft / preference / rag / raw), `retention_days`, dibuat_oleh |
| `dataset_versions` | id, dataset_id, nomor_versi, parent_version_id, uri_storage (Parquet), jumlah_baris, checksum, status |
| `column_mappings` | version_id, peta kolom → peran (prompt/response/messages/…) |
| `quality_runs` | id, version_id, config_snapshot (JSONB), seed, status, mulai/selesai, dqi_total |
| `run_module_results` | run_id, module, dimension, version_modul, metrics (JSONB), findings_count |
| `row_findings` | run_id, row_id, module, code, severity, score, span, detail (JSONB) — dipartisi HASH (16 partisi) per `run_id` pada PostgreSQL (migrasi `0003`) |
| `curation_actions` | id, version_id, row_id, aksi (approve/reject/flag/edit), nilai_lama/baru (terenkripsi bila PII), user_id, waktu |
| `quality_policies` | id, workspace_id, aturan ambang batas (JSONB), aktif |
| `exports` | id, version_id, format, filter, uri, manifest (JSONB), user_id |
| `audit_logs` | id, waktu, user_id, aksi, objek, ip, metadata — **append-only** |
| `api_tokens` | id, user_id, hash, scope, kedaluwarsa |
| `refresh_token_sessions` | id, user_id, `token_jti`, `family_id`, `expires_at`, `is_revoked`, `replaced_by_jti` (rotasi refresh token + deteksi reuse) |
| `webhook_subscriptions`, `webhook_deliveries` | id, workspace_id, nama, url, `signing_secret`, events, `is_active` & log pengiriman HMAC-SHA256 |
| `benchmark_sets` | id, workspace_id, nama, `entry_count`, `prompts_json`, `storage_uri`, `is_active` (deteksi kontaminasi C5) |

Catatan:
- Data baris dataset disimpan di **Parquet** pada object storage, bukan di PostgreSQL. Tabel `row_findings` menyimpan temuan, bukan teks asli.
- Edit kurasi menghasilkan **versi dataset baru** (copy-on-write pada level partisi), tidak menimpa versi sebelumnya.

### 2.6 Alur Pemrosesan (Pipeline)

1. **Unggah** → file disimpan ke storage (chunked upload, checksum) → catatan `dataset_versions` berstatus `uploaded`.
2. **Ingesti (A)** → deteksi format, *column mapping*, validasi skema → konversi ke Parquet bernormalisasi → status `validated`.
3. **Run kualitas** → pengguna memilih modul dan konfigurasi → job dibuat → worker menjalankan modul berurutan/paralel sesuai dependensi (lihat di bawah) → temuan ditulis per modul.
4. **Agregasi** → `scoring/` menghitung skor per dimensi dan DQI total → status `completed`.
5. **Kurasi (F3)** → tindakan pengguna → bila disimpan, membuat versi baru.
6. **Quality gate** → kebijakan dievaluasi sebelum ekspor.
7. **Ekspor** → file + `manifest.json` ditulis ke storage → tautan unduh bertanda tangan berumur pendek.

Urutan yang direkomendasikan: `ingestion → cleaning (artifact, PII, language, toxicity) → dedup (exact → near → semantic) → instruction → safety → scoring`. Modul tanpa dependensi dapat berjalan paralel.

Progres job dikirim ke UI lewat **Server-Sent Events**; job bersifat idempoten dan dapat di-*resume*.

### 2.7 Data Quality Index (DQI)

DQI = jumlah terbobot skor dimensi, dinormalisasi ke 0–100. **Bobot default (dapat dikonfigurasi per workspace dan disimpan di `config_snapshot`):**

| Dimensi | Bobot default | Contoh metrik pembentuk |
|---|---|---|
| Validitas Struktur | 15% | % baris valid skema, % pasangan lengkap, % format patuh |
| Kebersihan & Privasi | 25% | PII per 1.000 baris, % baris dengan artefak, konsistensi bahasa |
| Keunikan & Keberagaman | 20% | rasio duplikat eksak/near/semantik, entropi cluster, tingkat repetisi n-gram |
| Kualitas Instruksi–Respons | 25% | skor judge rata-rata, sebaran kompleksitas, kepatuhan format |
| Keamanan & Bias | 15% | % baris toksik, indeks bias, risiko halusinasi |

Aturan:
- Skor tiap dimensi = rata-rata terbobot metrik yang dinormalisasi 0–1 (rumus per metrik didokumentasikan di `docs/dqi.md`).
- **Penalti kritis:** temuan berseverity `critical` (mis. PII belum dimasking) membatasi DQI maksimum (mis. *cap* 60) agar skor tidak menutupi risiko serius. Nilai *cap* dapat diatur oleh Governance.
- Dimensi yang modulnya tidak dijalankan **dikecualikan** dan bobotnya didistribusikan ulang; UI menandai skor sebagai "parsial".
- Tampilkan selalu rincian kontribusi, bukan hanya angka akhir.
- Pemetaan label: 90–100 **Siap Latih**, 75–89 **Baik**, 60–74 **Perlu Perbaikan**, <60 **Berisiko**.

### 2.8 Desain API (REST, `/api/v1`)

Konvensi: JSON, `snake_case`, paginasi berbasis kursor, kode error terstandar (`{ "error": { "code", "message", "details" } }`), idempotency key untuk operasi pembuatan, OpenAPI otomatis dari FastAPI.

| Endpoint (ringkas) | Fungsi |
|---|---|
| `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`, `GET /auth/me`, `GET /auth/sso/providers`, `/auth/oidc/*`, `/auth/saml/*` | Autentikasi, rotasi refresh token, dan Enterprise SSO (OIDC/SAML 2.0) |
| `GET /workspaces/{id}/overview`, `GET/PUT /workspaces/{id}/retention`, `POST .../retention/purge` | Ringkasan KPI & kebijakan retensi UU PDP |
| `POST /workspaces/{id}/datasets`, `DELETE /datasets/{id}` | Buat dataset & hak hapus UU PDP |
| `POST /datasets/{id}/versions/uploads` (+ `…/complete`), `POST .../versions/import-connector`, `GET /versions/{id}/validation-errors` | Unggah chunked, impor konektor admin, laporan error validasi |
| `GET/PUT /versions/{id}/mapping` | Pemetaan kolom |
| `POST /versions/{id}/runs` | Mulai run kualitas (modul + config) |
| `GET /runs/{id}`, `GET /runs/{id}/events` (SSE) | Status dan progres |
| `GET /runs/{id}/summary`, `GET /runs/{id}/report.html`, `GET /runs/{id}/report.pdf`, `GET /datasets/{id}/dqi-trend` | DQI, skor dimensi, metrik, laporan kualitas & tren DQI |
| `GET /runs/{id}/findings` | Temuan per baris (filter, sort, kursor) |
| `GET /versions/{id}/rows` | Baris dataset (server-side paging/filter) |
| `GET /versions/{id}/dedup-preview`, `GET /versions/{id}/topic-clusters`, `GET /versions/{id}/compare/{other_id}` | Pratinjau dedup semantik, klaster 2D, dan perbandingan versi |
| `POST /curation/diff-preview`, `POST /curation/ai-repair-preview`, `POST /versions/{id}/curation` | Pratinjau diff transformasi, perbaikan teks lokal & aksi kurasi (batch) → versi baru |
| `GET/PUT /workspaces/{id}/policy` | Kebijakan kualitas |
| `POST /versions/{id}/exports`, `GET /exports/{id}` | Ekspor (`jsonl`, `csv`, `parquet`, `huggingface`, `rag_vector`, `sft_chat`) dan unduhan |
| `GET /modules`, `POST /versions/{id}/vlm-verify`, `POST /versions/{id}/semantic-chunk`, `POST /versions/{id}/generate-qa` | Katalog modul kualitas, verifikasi VLM PDF, *semantic chunking* RAG + metadata, dan generator QA/SFT |
| `GET /audit-logs` | Audit log (izin Governance/Admin) |
| `POST /api-tokens`, `GET/POST/DELETE /workspaces/{id}/webhooks*`, `GET/POST/DELETE /workspaces/{id}/benchmarks*` | Token API integrasi, manajemen webhook HMAC & set benchmark |
| `GET /healthz`, `GET /readyz`, `GET /metrics` | Probe liveness/readiness & metrik Prometheus |

### 2.9 Keamanan

- **Autentikasi:** OIDC/SAML untuk korporat; JWT akses singkat + refresh rotasi; token API ber-*scope* dan di-hash di DB.
- **Otorisasi:** RBAC per workspace. Izin granular, contoh: `dataset:read`, `dataset:write`, `run:execute`, `pii:view`, `export:create`, `policy:manage`, `audit:read`.
- **Perlindungan data:** TLS di semua jalur; enkripsi *at rest* di storage dan database; secret lewat environment/secret manager; unduhan memakai URL bertanda tangan berumur pendek.
- **PII:** tampil tersamar secara default; membuka PII memerlukan izin `pii:view` dan tercatat di audit log; PII tidak masuk log aplikasi.
- **Audit log:** append-only, mencakup siapa/apa/kapan/dari mana.
- **Validasi input:** batas ukuran file, validasi tipe MIME dan isi, pemindaian sandbox untuk parsing format biner; hindari *deserialization* tak aman.
- **Rate limiting** dan perlindungan brute force pada endpoint auth.
- **Dependensi:** pemindaian kerentanan (pip-audit, npm audit, Trivy) di CI.
- **Retensi:** kebijakan retensi dan penghapusan dataset yang dapat dikonfigurasi (mendukung hak penghapusan data).

### 2.10 Kinerja dan Skalabilitas
- Gunakan **Polars lazy** dan format Parquet dengan *row group* yang sesuai untuk pemrosesan kolumnar.
- Embedding dihitung **batch** dan di-*cache* berdasarkan hash teks + versi model.
- Deduplikasi besar: MinHash/LSH untuk penyaringan awal, lalu embedding hanya untuk kandidat/blok.
- LLM-judge: batching, antrean prioritas, opsi **sampling** (nilai sebagian baris) untuk dataset besar dan estimasi waktu yang ditampilkan di UI.
- Tabel UI: paginasi dan filter **sisi server**, virtualisasi baris; agregat dihitung dan disimpan saat run, bukan saat render.
- Spesifikasi referensi untuk target kinerja: 8 vCPU, 32 GB RAM, satu GPU kelas menengah untuk embedding/judge. Ukur dan perbarui angka ini pada Fase 1 dengan *benchmark* nyata.

### 2.11 Pengujian
| Level | Alat | Cakupan |
|---|---|---|
| Unit | pytest, Vitest | Fungsi modul, scoring, komponen UI |
| Integrasi | pytest + Testcontainers | API + DB + storage + worker |
| Kontrak API | Schemathesis / OpenAPI diff | Kompatibilitas skema |
| E2E | Playwright | Alur unggah → run → kurasi → ekspor |
| Kualitas model | Set uji berlabel (PII Indonesia, toksisitas, dedup) | Precision/recall dilaporkan per rilis |
| Keamanan | SAST, dependency scan, uji RBAC | Setiap PR |
| Aksesibilitas | axe-core di Playwright | Halaman utama |

Data uji **wajib sintetis**; dilarang memasukkan data korporat nyata ke repositori.

### 2.12 Deployment dan Operasional
- **Dev:** `make dev` untuk mode *fast-dev* lokal (SQLite async + local storage + Celery *eager*) dan `docker compose up` untuk stack penuh (`api`, `worker`, `redis`, `postgres` + `pgvector`, `minio`, `frontend`, opsional model server).
- **Produksi v1:** Docker Compose (hardened) atau Kubernetes (Helm) dengan autoscaling worker; model server terpisah (GPU node).
- **Konfigurasi:** 12-factor via environment; semua opsi eksternal (mis. LLM API eksternal) **nonaktif secara default**.
- **Backup:** snapshot PostgreSQL terjadwal dan replikasi/versioning storage.
- **Monitoring:** dashboard Grafana (latensi API, antrean, durasi run, tingkat gagal), alert untuk job macet.
- **Migrasi:** Alembic, bersifat *forward-compatible*; tidak ada perubahan skema tanpa migrasi. Pada PostgreSQL, `ensure_postgres_version_column_width` (`backend/app/db/session.py` + `backend/alembic/env.py`) otomatis memperlebar `alembic_version.version_num` menjadi `VARCHAR(128)` agar revisi >32 karakter (`0003`–`0005`) tidak terpotong, dan migrasi `0003` me-rename constraint `row_findings_pkey` sebelum membuat tabel partisi HASH baru.
- **Penanganan Error & CORS:** handler error `500` (`backend/app/core/errors.py`) mencatat exception secara terstruktur tanpa PII dan mempertahankan header CORS untuk `CORS_ORIGINS` (`http://localhost:5173`, `http://127.0.0.1:5173`).

### 2.13 Keputusan Arsitektur (Diperbarui dari Keputusan Terbuka Fase 0)
- **Model embedding multibahasa:** Default `intfloat/multilingual-e5-small` (hook `sentence-transformers`, dengan fallback deterministik `local_hash` + SimHash LSH untuk dev/CI offline; benchmark sintetis pada `docs/benchmarks.md` mencatat Precision `1.0000` dan Recall `1.0000`).
- **Model LLM-judge lokal & GPU:** Default `ALLOW_EXTERNAL_LLM=false` dan `LLM_PROVIDER=placeholder`; mode aktif kompatibel OpenAI diarahkan ke loopback lokal (`http://localhost:11434/v1` Ollama/vLLM, divalidasi oleh `is_local_llm_url`). Kebutuhan GPU klaster mengikuti spesifikasi referensi 2.10.
- **Vector store produksi:** `pgvector` di PostgreSQL 16 (atau `faiss` lokal/hybrid) melalui `VECTOR_INDEX_BACKEND=auto|bruteforce|lsh|faiss|pgvector`; Qdrant dievaluasi terpisah bila skala melebihi jutaan vektor.
- **Penyedia SSO target:** Protokol terbuka **OIDC** (JWKS/HS256 — kompatibel Keycloak, Microsoft Entra ID/Azure AD, Okta) dan **SAML 2.0** (SP metadata + ACS HMAC + proteksi anti-XXE), nonaktif secara default (`OIDC_ENABLED=false`, `SAML_ENABLED=false`).
- **Kebijakan retensi default:** Dikonfigurasi per workspace (`default_retention_days` lewat `/workspaces/{id}/retention`, default nonaktif/`None` hingga diaktifkan admin) dan per dataset (`datasets.retention_days`, contoh seed `90` hari), didukung hak hapus UU PDP (`DELETE /datasets/{id}`) serta pembersihan manual (`POST .../retention/purge`).

---

## 3. Panduan Desain — Modern & Profesional

### 3.1 Arah Visual
**Karakter:** tenang, presisi, tepercaya. Bergaya *enterprise SaaS* modern (mengacu pada kejelasan produk seperti Linear, Stripe Dashboard, dan Datadog), bukan dekoratif. Data adalah fokus utama; antarmuka menyingkir.

Prinsip:
1. **Kejelasan di atas hiasan.** Hierarki visual jelas, ruang putih cukup, tanpa elemen dekoratif yang tidak berfungsi.
2. **Densitas yang dapat diatur.** Analis bekerja dengan tabel padat; sediakan mode *comfortable* dan *compact*.
3. **Warna bermakna.** Warna dipakai untuk status dan aksi, bukan dekorasi.
4. **Konsisten.** Satu sistem token, satu pustaka komponen.
5. **Dapat dijelaskan.** Setiap angka (terutama DQI) bisa ditelusuri ke asalnya.

### 3.2 Design Tokens

Definisikan di `frontend/src/styles/tokens.css` dan dipetakan ke Tailwind. **Dilarang memakai nilai mentah di komponen.**

**Warna (nilai awal; boleh disesuaikan dengan brand korporat)**

| Token | Light | Dark | Kegunaan |
|---|---|---|---|
| `--bg-canvas` | `#F8FAFC` | `#0B1220` | Latar halaman |
| `--bg-surface` | `#FFFFFF` | `#111A2E` | Kartu, panel |
| `--bg-subtle` | `#F1F5F9` | `#172238` | Header tabel, hover |
| `--border-default` | `#E2E8F0` | `#25324B` | Garis dan batas |
| `--text-primary` | `#0F172A` | `#E6EBF5` | Teks utama |
| `--text-secondary` | `#475569` | `#9AA7C0` | Teks pendukung |
| `--text-muted` | `#64748B` | `#7A88A5` | Label, placeholder |
| `--brand-600` | `#3D5AFE` | `#6F86FF` | Aksi utama, tautan aktif |
| `--brand-50` | `#EEF1FF` | `#1A2547` | Latar aksen halus |
| `--success-600` | `#16A34A` | `#4ADE80` | Lolos / Siap Latih |
| `--warning-600` | `#D97706` | `#FBBF24` | Perlu perbaikan |
| `--danger-600` | `#DC2626` | `#F87171` | Kritis / Berisiko |
| `--info-600` | `#0284C7` | `#38BDF8` | Informasi |

Aturan kontras: teks biasa ≥ 4,5:1, teks besar dan komponen UI ≥ 3:1. Status **tidak boleh hanya mengandalkan warna**; sertakan ikon dan label teks.

**Skala DQI (konsisten di seluruh UI)**
`90–100` hijau (Siap Latih) · `75–89` biru (Baik) · `60–74` oranye (Perlu Perbaikan) · `<60` merah (Berisiko).

**Tipografi**
- UI: **Inter** (fallback: system-ui). Kode/ID/teks dataset mentah: **JetBrains Mono**. Font menggunakan stack lokal/*system* agar kompatibel *air-gapped* tanpa ketergantungan CDN Google Fonts eksternal.
- Skala: 12 / 13 / 14 (body default) / 16 / 20 / 24 / 30 / 36 px.
- Bobot: 400 (body), 500 (label/tombol), 600 (judul), 700 (angka KPI).
- Line-height: 1,5 untuk teks, 1,25 untuk judul. Gunakan *tabular numbers* untuk kolom angka.

**Spasi dan bentuk**
- Grid dasar **4 px**; skala: 4, 8, 12, 16, 24, 32, 48, 64.
- Radius: 6 px (input/tombol), 10 px (kartu), 14 px (modal).
- Elevasi: lebih mengandalkan **border 1 px** daripada bayangan; bayangan halus hanya untuk popover/modal.
- Lebar konten maksimum 1440 px; sidebar 248 px (ciut 64 px).

**Gerak**
- Durasi 120–200 ms, easing `ease-out`. Hormati `prefers-reduced-motion`. Tidak ada animasi yang menunda tugas.

### 3.3 Layout dan Navigasi
- **App shell:** sidebar kiri (navigasi utama), top bar (pemilih workspace, pencarian global `⌘/Ctrl+K`, notifikasi, profil), area konten.
- **Navigasi utama:** Ringkasan · Dataset · Run Kualitas · Explorer & Kurasi · Tinjau Duplikat · Kebijakan · Laporan · Audit Log · Pengaturan.
- **Breadcrumb** pada halaman bertingkat (Workspace › Dataset › Versi › Run).
- Mode terang dan gelap; ikuti preferensi sistem, dapat diganti manual.
- Responsif: dioptimalkan desktop (≥ 1280 px), tetap dapat dipakai pada tablet; mobile hanya baca (*read-only*) untuk ringkasan.

### 3.4 Halaman Utama dan Pola
1. **Ringkasan Workspace** — KPI (jumlah dataset, rata-rata DQI, PII kritis terbuka, run berjalan), tren DQI, daftar dataset yang perlu perhatian.
2. **Daftar Dataset** — tabel dengan DQI (badge), status, versi terakhir, pemilik; pencarian dan filter.
3. **Detail Dataset/Versi** — tab: *Ringkasan · Skema & Token · Run · Versi · Ekspor*.
4. **Wizard Run Kualitas** — langkah: pilih versi → pilih modul → atur konfigurasi (dengan preset) → tinjau estimasi waktu → jalankan.
5. **Laporan Run** — kartu DQI besar + radar/bar skor dimensi, rincian kontribusi, temuan teratas, tautan *drill-down*.
6. **Explorer & Kurasi** — grid virtualisasi, panel filter kiri, panel detail baris kanan (teks, temuan tersorot, diff sebelum/sesudah), bilah aksi massal.
7. **Dedup Review** — tampilan kelompok duplikat berdampingan dengan skor kemiripan.
8. **Kebijakan Kualitas** — editor ambang batas dengan pratinjau dampak.
9. **Audit Log** — tabel dapat difilter dan diekspor.

### 3.5 Komponen Wajib (dari shadcn/ui, disesuaikan token)
`Button` (primary/secondary/ghost/destructive) · `Input/Select/Combobox/Checkbox/Switch` · `Tabs` · `Dialog/Sheet/Popover/Tooltip` · `Toast` · `Badge` (status + skor DQI) · `Card` · `DataTable` (sort, filter, pilih baris, kolom dapat diatur, virtualisasi) · `ScoreGauge` / `ScoreCard` (DQI) · `DimensionBreakdown` (bar/radar) · `Histogram/Chart` (Recharts, palet token) · `Stepper` (wizard) · `DiffViewer` (sebelum/sesudah) · `HighlightedText` (sorot PII/temuan dengan tooltip) · `EmptyState` · `Skeleton` · `Breadcrumb` · `CommandPalette`.

Spesifik domain:
- **`HighlightedText`:** menyorot rentang temuan memakai warna status + garis bawah/ikon (tidak hanya warna); PII tersamar secara default dengan tombol *reveal* (butuh izin).
- **`DataTable`:** baris tinggi 40 px (*comfortable*) / 32 px (*compact*); header *sticky*; kolom angka rata kanan dan bertipe *tabular*.
- **`ScoreCard`:** angka DQI besar, label status, delta terhadap versi sebelumnya, dan tautan "Lihat rincian".

### 3.5.1 State UI (wajib untuk setiap tampilan data)
- **Loading:** skeleton sesuai bentuk konten, bukan spinner kosong (spinner hanya untuk aksi singkat).
- **Empty:** ilustrasi/ikon sederhana, penjelasan, dan satu aksi utama.
- **Error:** pesan jelas, penyebab bila diketahui, dan aksi *retry*; jangan tampilkan *stack trace*.
- **Job berjalan:** progres, tahap saat ini, estimasi waktu, tombol batalkan.
- **Parsial:** tandai skor/hasil parsial dengan jelas (mis. modul tidak dijalankan).

### 3.6 Penulisan UI (Bahasa Indonesia)
- Nada profesional, ringkas, aktif. Hindari jargon tanpa penjelasan; sediakan tooltip untuk istilah teknis (mis. "Deduplikasi semantik").
- Tombol berupa kata kerja: "Jalankan Penilaian", "Ekspor Dataset", "Setujui".
- Pesan error menjelaskan masalah dan solusi: "Berkas tidak dapat dibaca karena baris 1.204 bukan JSON valid. Perbaiki baris tersebut lalu unggah ulang."
- Format angka dan tanggal sesuai lokal (`id-ID`): `1.250.000 baris`, `18 Nov 2026`.
- Semua teks lewat file locale; tidak ada string keras di komponen.

### 3.7 Aksesibilitas (WCAG 2.1 AA)
- Seluruh fungsi dapat dioperasikan dengan **keyboard**; fokus terlihat jelas.
- Label dan `aria-*` yang benar; tabel memakai semantik yang tepat.
- Kontras sesuai 3.2; tidak bergantung pada warna semata.
- Dukungan pembaca layar untuk perubahan status job (`aria-live`).
- Pintasan keyboard di Explorer (mis. `J/K` navigasi baris, `A` setujui, `R` tolak, `F` tandai) dengan bantuan `?`.

### 3.8 Checklist Desain Sebelum Merge
- [ ] Hanya memakai design tokens dan komponen standar.
- [ ] Mode terang dan gelap tampil benar.
- [ ] State loading, empty, error tersedia.
- [ ] Kontras dan fokus keyboard lolos.
- [ ] Teks lewat i18n (id dan en).
- [ ] Tabel besar memakai virtualisasi/paginasi server.
- [ ] Tidak ada PII mentah tampil tanpa izin.

---

## 4. Roadmap dan Fase Pengerjaan

> Estimasi bersifat indikatif; sesuaikan dengan ukuran tim. Setiap fase harus memenuhi Definition of Done (0.5) sebelum lanjut.
>
> **Status Terkini:** Fase 0–3 (cakupan aplikasi P0, P1, dan P2) berstatus **`SELESAI`** pada tingkat kode aplikasi (rincian verifikasi di `docs/progress.md` dan `REPORT.md`), sedangkan validasi beban klaster produksi live, aktivasi model neural GPU, dan peningkatan lanjutan Tahap 5+ dicatat pada `REPORT.md` Bagian 6.

### Fase 0 — Fondasi (±1–2 minggu)
- Inisialisasi monorepo, `Makefile`, Docker Compose (api, worker, postgres, redis, minio, frontend).
- CI (lint, type-check, test, dependency scan). Kunci versi dependensi.
- Auth dasar + RBAC kerangka + audit log kerangka.
- App shell frontend, design tokens, komponen dasar, mode terang/gelap, i18n.
- Evaluasi awal: model embedding multibahasa dan LLM-judge pada sampel Bahasa Indonesia (isi 2.13).
- **Selesai bila:** `make dev` menjalankan seluruh stack dan pengguna dapat login lalu melihat app shell.

### Fase 1 — MVP Inti (±4–6 minggu) — Prioritas P0
- Modul A (A1–A5): unggah, validasi, pemetaan kolom, analitik token.
- Modul B (B1–B3, B6): PII (termasuk recognizer Indonesia), pembersihan artefak, pratinjau diff.
- C1: deduplikasi eksak dan near-duplicate.
- F1: DQI versi awal (dimensi yang tersedia, ditandai parsial), F2–F3: Explorer dan kurasi dasar, F5: ekspor JSONL/CSV/Parquet.
- G2–G4: RBAC, workspace, audit log.
- **Selesai bila:** alur unggah → run → tinjau → kurasi → ekspor berjalan end-to-end pada dataset sintetis 100 ribu baris dengan target kinerja pada 1.7 dan 2.10.

### Fase 2 — v1.0 (±6–8 minggu) — Prioritas P1
- Modul C2–C4 (dedup semantik, clustering, n-gram), B4–B5 (bahasa, toksisitas).
- Modul D1–D3 (kompleksitas, LLM-judge lokal, kepatuhan format), E1 (bias).
- F4, F6, F7: versi dataset, laporan PDF/HTML, quality gate.
- G1 (SSO), G5 (API publik + webhook), G6 (Helm).
- Hardening keamanan, uji beban, dokumentasi operasional.
- **Selesai bila:** semua P0+P1 lulus acceptance criteria dan pengujian keamanan/aksesibilitas.

### Fase 3 — Pasca v1.0 — Prioritas P2
- Konektor sumber data (A6), deteksi kontaminasi benchmark (C5), data preferensi (D4), kualitas chunk RAG (D5), risiko halusinasi dan prompt injection (E2–E3), tren DQI (F8).

---

## 5. Glosarium Singkat
- **DQI** — Data Quality Index, skor kesehatan dataset 0–100.
- **SFT** — Supervised Fine-Tuning. **RLHF/DPO** — pelatihan berbasis preferensi.
- **RAG** — Retrieval-Augmented Generation.
- **PII** — Personally Identifiable Information.
- **LLM-as-a-judge** — memakai LLM untuk menilai kualitas respons.
- **Human-in-the-loop** — peninjauan dan keputusan manusia dalam alur otomatis.
- **Quality gate** — aturan ambang batas yang harus dipenuhi sebelum ekspor.
- **Semantic dedup** — penghapusan duplikat berdasarkan kemiripan makna.
