# Standoff 2 Sticker Market & Management Hub

[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.12-blue.svg?logo=python)](https://www.python.org/)
[![Aiogram 3](https://img.shields.io/badge/Aiogram-3.x-2CA5E0.svg?logo=telegram)](https://docs.aiogram.dev/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg?logo=fastapi)](https://fastapi.tiangolo.com/)
[![SQLAlchemy 2.0](https://img.shields.io/badge/SQLAlchemy-2.0%20(async)-D71F00.svg?logo=sqlalchemy)](https://docs.sqlalchemy.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15+-4169E1.svg?logo=postgresql)](https://www.postgresql.org/)
[![Tests](https://img.shields.io/badge/Tests-65%20Passing-brightgreen.svg)](https://docs.pytest.org/)
[![Code Style](https://img.shields.io/badge/Code%20Style-Black%20%7C%20Ruff-000000.svg)](https://github.com/astral-sh/ruff)

High-performance P2P trading platform for game assets (skins, craft stickers, collections) with an anonymous buyer-seller deal chat, automated channel broadcasts, Telegram Stars monetization, and a secure **FastAPI** web administration dashboard.

---

## 🎯 Architecture & Design Highlights

The project adheres to **Clean Architecture** and **Layered Application Pattern**:

```
standoff2-sticker-market/
├── alembic/                 # Async database schema migrations
├── src/
│   ├── config.py            # Strictly-typed Pydantic settings & validation
│   ├── data/                # In-memory game weapon and skin catalog verification
│   ├── database/            # Async SQLAlchemy engine, scoped sessions & ORM models
│   ├── handlers/            # Aiogram routers (start, catalog, market, deals, admin)
│   ├── keyboards/           # Dynamic inline & reply keyboards with pagination
│   ├── middlewares/         # Database session lifecycle & error-boundary middleware
│   ├── services/            # Business domain services (deals, notifications, scheduler)
│   ├── states/              # Aiogram FSM (Finite State Machine) states
│   ├── utils/               # Text formatters, Telegram Stars invoice helpers
│   └── web/                 # FastAPI dashboard, admin auth, API endpoints
└── tests/                   # Pytest test suite (system, resilience, referral logic)
```

### Key Engineering Practices:
- **Asynchronous Data Layer:** Fully async database I/O via `asyncpg` and `SQLAlchemy 2.0` (`select(...)` constructs, eager loading, explicit session lifecycles).
- **Session Middleware:** Automatic rollback on exceptions, commit on clean exit, and zero leaked connection pools.
- **Resilience & Self-Healing:** Automated migration and recovery mechanisms for media file IDs across Telegram bot migrations.
- **Monetization Engine:** Native Telegram Stars checkout flow for Turbo & Standard announcement promotions.
- **FastAPI Web Hub:** Independent admin panel with session tokens, audit logging, ad moderation, and broadcast management.
- **Strict Typing & Testing:** 65 comprehensive automated tests covering referral loops, deal lifecycle states, channel cleanup, catalog validation, and rate limits.
- **Viral Referral System:** Tiered referral engine rewarding active referrers with free 24-hour TOP listing placements, dynamic bot deep-links, and anti-fraud checks.
- **Channel Broadcast Sync & Cleanup:** Automated synchronization of announcements with Telegram channels, including instant deletion/cleanup upon deals or moderation bans.

---

## 🚀 Key Features

* **P2P Deal Hub:** Anonymous chat proxy routing messages between buyers and sellers without revealing personal Telegram usernames until confirmation.
* **Game Asset Catalog:** Instant validation of skins, weapons, and sticker combinations with multi-filter search.
* **Telegram Stars Checkout:** In-app microtransactions for VIP listings and automatic pin in official showcase channels.
* **Automated Background Scheduler:** Scheduled cleanup of expired listings, cooldown verification, and channel broadcasting via `APScheduler`.
* **Full-Featured Admin Panel:** Web dashboard to moderate listings, inspect active deals, manage bans, and review analytics.

---

## 🛠 Tech Stack

* **Language:** Python 3.11+ / 3.12
* **Bot Framework:** `aiogram 3.x`
* **Web & API Framework:** `FastAPI`, `Uvicorn`, `Jinja2`
* **Database & ORM:** `PostgreSQL`, `SQLAlchemy 2.0`, `asyncpg`, `Alembic`
* **Validation & Settings:** `Pydantic V2`, `pydantic-settings`
* **Task Scheduling:** `APScheduler`
* **Testing:** `pytest`, `pytest-asyncio`, `httpx`, `starlette.testclient`

---

## ⚙️ Quick Start

### 1. Clone Repository & Setup Virtual Environment
```bash
git clone https://github.com/florichdev/standoff2-sticker-market.git
cd standoff2-sticker-market

python -m venv .venv
# Linux / macOS
source .venv/bin/activate
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

### 2. Install Dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Configure Environment Variables
Copy `.env.example` to `.env` and fill in your values:
```bash
cp .env.example .env
```
> ⚠️ **Security Notice:** Never commit `.env` containing real tokens to version control.

### 4. Run Database Migrations
```bash
alembic upgrade head
```

### 5. Launch the Platform
```bash
# Start bot and FastAPI dashboard simultaneously
python -m src.main
```

### 6. Run Test Suite
```bash
pytest -v tests/
```

---

## 📄 License
This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

