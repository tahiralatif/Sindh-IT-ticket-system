# Sindh IT Ticket System — Security Audit Report

**Date:** September 10, 2026  
**URL:** https://sindh-it-ticket.14.jugaar.ai/  
**Project:** /root/sindh-it-ticket-system  
**Stack:** FastAPI + SQLAlchemy (async) + SQLite + Jinja2 + Groq AI

---

## Executive Summary

The Sindh IT Ticket System is a government complaint portal with AI-powered routing. After the previous session fixed several issues (CSRF, secure cookies, file upload validation), this re-audit found **3 remaining issues** that need attention. The codebase is well-structured with solid security foundations.

**Overall Score: 7.5/10**

---

## Issues Found

### MEDIUM (2 issues)

#### M1: Chat widget XSS via innerHTML — chat.js line 103-104
- **Severity:** Medium
- **Category:** Frontend Security
- **File:** `app/static/js/chat.js:103-104`
- **Description:** The `addMessage()` function renders AI responses via `innerHTML` with only basic bold markdown replacement (`**text**` → `<strong>text</strong>`). If an attacker could inject malicious HTML into chat history (stored in DB), it would execute in the browser. Risk is limited because responses come from Groq API, but prompt injection or compromised chat history could exploit this.
- **Fix:** Use `textContent` for user messages, sanitize AI responses before innerHTML, or use a lightweight HTML sanitizer.
- **Status:** NOT FIXED (previous session did not address this)

#### M2: No Content-Security-Policy header
- **Severity:** Medium
- **Category:** Security Headers
- **File:** Nginx config (`/etc/nginx/sites-enabled/sindh-it-ticket`)
- **Description:** The nginx config sets HSTS, X-Frame-Options, X-Content-Type-Options, and Referrer-Policy, but is missing CSP. Without CSP, the XSS in M1 could be exploited more easily. Inline scripts and styles from external CDNs (fonts, Font Awesome) are used.
- **Fix:** Add CSP header to nginx config, allowing specific CDNs.
- **Status:** NOT FIXED (previous session did not address this)

### LOW (2 issues)

#### L1: No password strength validation on registration
- **Severity:** Low
- **Category:** Authentication
- **File:** `app/main.py:238-268`
- **Description:** The registration form accepts any password without length or complexity requirements. A user could register with a single-character password.
- **Fix:** Add minimum password length (8 chars) and complexity check in the register_submit handler.
- **Status:** NOT FIXED

#### L2: Session cookie uses Fernet key derived from only first 32 bytes of SECRET_KEY
- **Severity:** Low
- **Category:** Secrets Management
- **File:** `app/core/security.py:21`
- **Description:** `_fernet_key = base64.urlsafe_b64encode(settings.SECRET_KEY.encode()[:32].ljust(32, b'\0'))` — if SECRET_KEY is shorter than 32 chars, null bytes are padded. Current key is 64 hex chars so this is fine, but the pattern is fragile.
- **Fix:** Use `hashlib.sha256(settings.SECRET_KEY.encode()).digest()` for consistent key derivation.
- **Status:** NOT FIXED

---

## Previously Fixed Issues (Verified ✓)

The following issues were fixed in the previous session and verified:

1. **502 Bad Gateway** — App was not running in PM2. Started `sindh-tickets` process. ✓
2. **CSRF on admin routes** — `admin_toggle_user` and `admin_update_role` now validate CSRF tokens. ✓
3. **Secure cookie flag** — Both `set_cookie("session", ...)` calls include `secure=True`. ✓
4. **File upload extension validation** — Allowed extensions whitelist enforced. ✓
5. **Duplicate create_notification** — Removed duplicate function definition from main.py. ✓

---

## Security Scorecard

| Category | Score | Notes |
|----------|-------|-------|
| Authentication | 8/10 | bcrypt hashing, encrypted sessions, rate limiting on login |
| Authorization | 8/10 | Role-based access (admin/department/citizen), ticket access control |
| Auth Separation | 7/10 | Admin routes properly check `user["role"] == "admin"` |
| Input Validation | 8/10 | Parameterized queries (SQLAlchemy ORM), CSRF on forms |
| Rate Limiting | 8/10 | Login (5/min), register (3/min), submit (10/min), chat (20/min) |
| CORS | 9/10 | No CORS middleware (safe by default — same-origin only) |
| SQL Injection | 9/10 | SQLAlchemy ORM + parameterized text() queries |
| Secrets Management | 7/10 | .env gitignored, but hardcoded admin seed password |
| Error Handling | 8/10 | Try/except blocks, no stack traces to client |
| Logging | 7/10 | Python logging for email/AI errors, no structured logging |
| HTTPS | 9/10 | TLS 1.2/1.3, HSTS, Let's Encrypt cert (expires Oct 12) |
| Security Headers | 7/10 | HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy — missing CSP |
| CSRF Protection | 9/10 | Double-submit cookie pattern on all state-changing routes |
| Frontend Security | 6/10 | innerHTML XSS in chat.js, sessionStorage for non-sensitive data only |
| Code Quality | 8/10 | Clean architecture, proper separation, no TODOs |
| **Overall** | **7.5/10** | **Solid foundation with a few medium issues to address** |

---

## What's Good

1. **Encrypted sessions** — Fernet encryption + HMAC signing, payload not readable by client
2. **Parameterized queries** — SQLAlchemy ORM used throughout, no raw SQL injection risk
3. **CSRF protection** — Double-submit cookie pattern on all POST endpoints
4. **Rate limiting** — Applied to login, registration, ticket submission, chat
5. **File upload validation** — Extension whitelist enforced
6. **Role-based access control** — Admin/department/citizen roles properly enforced
7. **IP extraction** — Properly uses X-Real-IP (nginx, not spoofable) over X-Forwarded-For
8. **Session security** — httponly + secure + samesite=lax cookies
9. **DB file permissions** — 600 (owner-only)
10. **Clean architecture** — Proper separation of concerns (core, api, services, middleware, ai)

---

## Priority Fix Order

1. **M1: Chat XSS** — Sanitize innerHTML in chat.js (quick fix)
2. **M2: CSP header** — Add Content-Security-Policy to nginx config
3. **L1: Password policy** — Add minimum length check in registration
4. **L2: Fernet key derivation** — Use SHA-256 hash for consistent key

---

## Infrastructure Notes

- **SSL Certificate:** Valid until Oct 12, 2026 (auto-renewed via certbot cron)
- **PM2 Process:** `sindh-tickets` running on port 8004
- **Database:** SQLite with 50 tickets, 36 users, 10 departments
- **DB Permissions:** 600 (owner-only) ✓
- **.gitignore:** Properly excludes .env, *.db, uploads/, venv/ ✓
- **Crontab:** No app-specific cron jobs (certbot renewal only)
