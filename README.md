# 🇵🇰 Sindh IT Ticket System

> **Live:** [sindh-it-ticket.14.jugaar.ai](https://sindh-it-ticket.14.jugaar.ai)

Government ticket management system for the Science & IT Department, Government of Sindh.

## Features

- **AI-Powered Department Routing** — Groq LLM-based ticket classification with confidence scores
- **Session Authentication** — Encrypted session cookies (Fernet) with CSRF protection
- **Rate Limiting** — Login attempt throttling (5/minute)
- **Dashboard** — Real-time ticket stats and recent tickets
- **Admin Panel** — User management, ticket assignment, analytics
- **Notifications** — Auto-generated on ticket creation
- **File Uploads** — Attach files to tickets
- **Ticket Tracking** — Public ticket status lookup by ticket number

## Tech Stack

- **Backend:** Python FastAPI + SQLAlchemy + Jinja2
- **Database:** SQLite (async via aiosqlite)
- **Auth:** Fernet-encrypted session cookies + bcrypt password hashing
- **AI:** Groq API (llama-3.1-8b-instant via langchain-groq) with keyword fallback
- **Security:** CSRF double-submit cookies, rate limiting (slowapi), SameSite=Lax

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your GROQ_API_KEY

# Run the server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The app will be available at `http://localhost:8000`

## Default Accounts

| Role | Username | Password |
|------|----------|----------|
| Admin | admin | *(see .env)* |

Citizens can register at `/register`.

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SECRET_KEY` | Secret key for session signing | Random hex |
| `GROQ_API_KEY` | API key for Groq AI department suggestions | (empty = keyword fallback) |
| `DATABASE_URL` | SQLite database URL | `sqlite+aiosqlite:///./sindh_tickets.db` |
| `RATE_LIMIT_LOGIN` | Login rate limit | `5/minute` |

## API Endpoints

- `GET /api/stats` — Dashboard statistics
- `GET /api/ticket/{id}` — Ticket detail (JSON)
- `GET /api/suggest-dept?subject=&description=` — AI department suggestion
- `GET /api/unread-count` — Notification count
- `GET /api/analytics/*` — Analytics data
