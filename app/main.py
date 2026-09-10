"""Sindh IT Ticket System — Main FastAPI Application."""
import os
import re
import json
import uuid
import time
import asyncio
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Form, UploadFile, File, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from slowapi import Limiter
from slowapi.util import get_remote_address as _default_get_remote_address

from app.core.config import settings
from app.core.database import get_db, init_db, async_session
from app.core.models import (
    User, Department, Ticket, TicketHistory, Attachment, Notification,
    ChatHistory, Announcement
)
from app.core.security import (
    hash_password, verify_password, create_session_token,
    decode_session_token, generate_csrf_token, validate_csrf_token,
    validate_password_policy, generate_password_reset_token,
    verify_password_reset_token, invalidate_password_reset_token,
)
from app.middleware.auth import SessionMiddleware, CSRFMiddleware
from app.ai.suggest import suggest_department, keyword_fallback
from app.api.chat import handle_chat, CITIZEN_WELCOME, load_chat_history, clear_chat_history
from app.services.notification_service import (
    notify_admin_new_ticket, notify_citizen_status_change
)

# Rate limiter
def get_client_ip(request: Request) -> str:
    """Extract real client IP. Prioritize X-Real-IP (set by nginx from $remote_addr,
    cannot be spoofed by client) over X-Forwarded-For (client can prepend fake IPs)."""
    # X-Real-IP: set by nginx as proxy_set_header X-Real-IP $remote_addr
    # This is the direct TCP connection IP — cannot be forged by the client
    xri = request.headers.get("x-real-ip")
    if xri:
        return xri.strip()
    # X-Forwarded-For: client CAN prepend spoofed IPs before nginx appends the real one
    # Only use as fallback, and take the LAST entry (nginx-appended) not the first
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # nginx appends real IP at end: "spoofed, proxy, real_client"
        # Take the last non-empty entry which is the one nginx added
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]
    # Direct connection (no proxy)
    if request.client:
        return request.client.host
    return "127.0.0.1"


limiter = Limiter(key_func=get_client_ip)

# ─── Account Lockout Tracking ────────────────────────────────────
# In-memory: {username: {"count": int, "locked_until": float}}
failed_attempts = {}

LOCKOUT_MAX_ATTEMPTS = 5
LOCKOUT_DURATION_SECONDS = 15 * 60  # 15 minutes


def _is_locked_out(username: str) -> bool:
    """Check if an account is currently locked out."""
    info = failed_attempts.get(username)
    if not info:
        return False
    if info["count"] >= LOCKOUT_MAX_ATTEMPTS:
        if time.time() < info["locked_until"]:
            return True
        # Lockout expired — reset
        del failed_attempts[username]
    return False


def _record_failed_attempt(username: str):
    """Record a failed login attempt. Locks out after LOCKOUT_MAX_ATTEMPTS."""
    now = time.time()
    if username not in failed_attempts:
        failed_attempts[username] = {"count": 1, "locked_until": 0}
    else:
        info = failed_attempts[username]
        # If previous lockout expired, reset counter
        if info["count"] >= LOCKOUT_MAX_ATTEMPTS and now >= info["locked_until"]:
            failed_attempts[username] = {"count": 1, "locked_until": 0}
        else:
            info["count"] += 1
            if info["count"] >= LOCKOUT_MAX_ATTEMPTS:
                info["locked_until"] = now + LOCKOUT_DURATION_SECONDS


def _reset_failed_attempts(username: str):
    """Clear failed attempts on successful login."""
    failed_attempts.pop(username, None)


# ─── Email Service Import ────────────────────────────────────────
from app.services.email_service import send_email

DEPARTMENTS = [
    ("Health Department", "HEALTH"),
    ("Education Department", "EDUCATION"),
    ("Transport Department", "TRANSPORT"),
    ("Revenue Department", "REVENUE"),
    ("Police Department", "POLICE"),
    ("Excise & Taxation", "EXCISE"),
    ("Social Welfare Department", "SOCIAL"),
    ("Information Department", "INFO"),
    ("Works & Services", "WORKS"),
    ("Agriculture Department", "AGRI"),
]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# ─── Startup ─────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    async with async_session() as db:
        # Seed departments if empty
        result = await db.execute(select(func.count(Department.id)))
        if result.scalar() == 0:
            for name, code in DEPARTMENTS:
                db.add(Department(name=name, code=code))
            await db.commit()

        # Seed admin user if empty
        result = await db.execute(select(func.count(User.id)))
        if result.scalar() == 0:
            admin = User(
                username="admin",
                password_hash=hash_password("[REDACTED]"),
                full_name="Sindh IT Minister Office",
                role="admin",
            )
            db.add(admin)
            await db.commit()
    yield


# ─── App Setup ───────────────────────────────────────────────────
app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION, lifespan=lifespan)
app.state.limiter = limiter
app.add_middleware(CSRFMiddleware)
app.add_middleware(SessionMiddleware)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

os.makedirs(os.path.join(BASE_DIR, "..", "uploads"), exist_ok=True)


# ─── Helpers ─────────────────────────────────────────────────────
def get_user(request: Request):
    return getattr(request.state, "user", None)


def csrf_token(request: Request) -> str:
    token = request.cookies.get("session", "")
    return generate_csrf_token(token) if token else ""


templates.env.globals["csrf_token"] = csrf_token
templates.env.globals["get_user"] = get_user


async def get_unread_count(db: AsyncSession, user_id: int) -> int:
    result = await db.execute(
        select(func.count(Notification.id)).where(
            Notification.user_id == user_id,
            Notification.is_read == False,
        )
    )
    return result.scalar() or 0


async def generate_ticket_number(db: AsyncSession) -> str:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    result = await db.execute(
        select(func.count(Ticket.id)).where(
            Ticket.ticket_number.like(f"SIT-{today}-%")
        )
    )
    count = (result.scalar() or 0) + 1
    return f"SIT-{today}-{count:04d}"


async def get_depts(db: AsyncSession):
    result = await db.execute(select(Department).where(Department.is_active == True))
    return result.scalars().all()


# ─── CSRF Validation Helper ──────────────────────────────────────
def require_csrf(request: Request, csrf_token: str = Form(None)):
    """Validate CSRF token for form submissions. Raises 403 if invalid."""
    session_token = request.cookies.get("session", "")
    if session_token and csrf_token:
        if not validate_csrf_token(session_token, csrf_token):
            raise HTTPException(status_code=403, detail="CSRF token invalid")
    elif session_token and not csrf_token:
        raise HTTPException(status_code=403, detail="CSRF token missing")
# ─── Ticket Access Control ────────────────────────────────────────
async def require_ticket_access(request: Request, ticket, db: AsyncSession):
    """Check if the current user can access/modify a ticket.
    - admin: full access
    - department: access to tickets in their department
    - citizen: can only access their own tickets (view only, no modification)
    Raises HTTPException 403 if access denied.
    """
    user = get_user(request)
    if not user:
        raise HTTPException(status_code=302, detail="Redirect to login")
    role = user["role"]
    if role == "admin":
        return  # Admin can do anything
    if role == "department":
        # Department users can access tickets assigned to their department
        dept_user = await db.get(User, user["user_id"])
        if dept_user and ticket.assigned_to_dept == dept_user.department_id:
            return  # Department match
        # Also allow if ticket is unassigned (they might need to claim it)
        if not ticket.assigned_to_dept:
            return
        raise HTTPException(status_code=403, detail="You can only access tickets in your department")
    # Citizen: only view own tickets
    if ticket.submitted_by == user["user_id"]:
        return  # Own ticket — view only
    raise HTTPException(status_code=403, detail="You can only view your own tickets")



# ═══════════════════════════════════════════════════════════════════
# PUBLIC PAGES
# ═══════════════════════════════════════════════════════════════════

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
@limiter.limit(settings.RATE_LIMIT_LOGIN)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    # Check account lockout
    if _is_locked_out(username):
        remaining = int(failed_attempts[username]["locked_until"] - time.time())
        minutes = remaining // 60
        seconds = remaining % 60
        time_str = f"{minutes} minute{'s' if minutes != 1 else ''}" if minutes else f"{seconds} second{'s' if seconds != 1 else ''}"
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": f"Account temporarily locked due to too many failed attempts. Please try again in {time_str}.",
        })

    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(password, user.password_hash):
        _record_failed_attempt(username)
        attempts_left = LOCKOUT_MAX_ATTEMPTS - failed_attempts.get(username, {}).get("count", 0)
        if attempts_left <= 0:
            return templates.TemplateResponse("login.html", {
                "request": request,
                "error": "Account temporarily locked due to too many failed attempts. Please try again in 15 minutes.",
            })
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": f"Invalid username or password. {max(attempts_left - 1, 0)} attempt{'s' if attempts_left - 1 != 1 else ''} remaining before lockout.",
        })

    if not user.is_active:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Account is disabled",
        })

    # Successful login — reset lockout counter
    _reset_failed_attempts(username)

    # Extract client info for session binding
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent", "")

    token = create_session_token(user.id, user.role, ip_address=client_ip, user_agent=user_agent)
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie("session", token, max_age=settings.SESSION_MAX_AGE, httponly=True, secure=True, samesite="lax")
    return response


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})


@app.post("/register")
@limiter.limit("3/minute")
async def register_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(...),
    email: str = Form(""),
    phone: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    # Password policy validation
    pw_error = validate_password_policy(password)
    if pw_error:
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error": pw_error,
        })

    # Check duplicate
    result = await db.execute(select(User).where(User.username == username))
    if result.scalar_one_or_none():
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error": "Username already exists",
        })

    user = User(
        username=username,
        password_hash=hash_password(password),
        full_name=full_name,
        email=email,
        phone=phone,
        role="citizen",
    )
    db.add(user)
    await db.commit()

    token = create_session_token(user.id, user.role)
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie("session", token, max_age=settings.SESSION_MAX_AGE, httponly=True, secure=True, samesite="lax")
    return response


@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("session")
    return response


# ═══════════════════════════════════════════════════════════════════
# FORGOT / RESET PASSWORD
# ═══════════════════════════════════════════════════════════════════

@app.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    return templates.TemplateResponse("forgot_password.html", {"request": request})


@app.post("/forgot-password")
@limiter.limit("3/minute")
async def forgot_password_submit(
    request: Request,
    email: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Generate a password reset token and send email (if configured)."""
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    # Always show the same message to prevent email enumeration
    success_msg = "If an account with that email exists, a password reset link has been sent."
    if user and user.email:
        token = generate_password_reset_token(user.id)
        reset_url = f"{request.base_url.scheme}://{request.base_url.netloc}/reset-password/{token}"

        # Send reset email (gracefully skips if API not configured)
        await send_email(
            to_email=user.email,
            subject="Reset Your Password — Sindh IT Ticket System",
            template_name="password_reset.html",
            context={
                "user_name": user.full_name,
                "reset_url": reset_url,
                "system_name": settings.APP_NAME,
            },
        )

    return templates.TemplateResponse("forgot_password.html", {
        "request": request,
        "success": success_msg,
    })


@app.get("/reset-password/{token}", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str):
    user_id = verify_password_reset_token(token)
    if user_id is None:
        return templates.TemplateResponse("reset_password.html", {
            "request": request,
            "error": "This password reset link is invalid or has expired. Please request a new one.",
            "token_invalid": True,
        })
    return templates.TemplateResponse("reset_password.html", {
        "request": request,
        "token": token,
    })


@app.post("/reset-password/{token}")
@limiter.limit("5/minute")
async def reset_password_submit(
    request: Request,
    token: str,
    password: str = Form(...),
    confirm_password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user_id = verify_password_reset_token(token)
    if user_id is None:
        return templates.TemplateResponse("reset_password.html", {
            "request": request,
            "error": "This password reset link is invalid or has expired. Please request a new one.",
            "token_invalid": True,
        })

    if password != confirm_password:
        return templates.TemplateResponse("reset_password.html", {
            "request": request,
            "token": token,
            "error": "Passwords do not match.",
        })

    # Password policy validation
    pw_error = validate_password_policy(password)
    if pw_error:
        return templates.TemplateResponse("reset_password.html", {
            "request": request,
            "token": token,
            "error": pw_error,
        })

    # Update password
    user = await db.get(User, user_id)
    if not user:
        return templates.TemplateResponse("reset_password.html", {
            "request": request,
            "error": "User not found.",
            "token_invalid": True,
        })

    user.password_hash = hash_password(password)
    await db.commit()

    # Invalidate the token
    invalidate_password_reset_token(token)

    return templates.TemplateResponse("login.html", {
        "request": request,
        "success": "Your password has been reset successfully. You can now sign in.",
    })


@app.get("/track", response_class=HTMLResponse)
async def track_page(request: Request):
    return templates.TemplateResponse("track.html", {"request": request, "result": None})


@app.post("/track", response_class=HTMLResponse)
async def track_search(
    request: Request,
    ticket_number: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Ticket).where(Ticket.ticket_number == ticket_number.strip())
    )
    ticket = result.scalar_one_or_none()
    history = []
    if ticket:
        h = await db.execute(
            select(TicketHistory).where(TicketHistory.ticket_id == ticket.id).order_by(TicketHistory.created_at)
        )
        history = h.scalars().all()
    return templates.TemplateResponse("track.html", {
        "request": request,
        "result": ticket,
        "history": history,
    })


# ═══════════════════════════════════════════════════════════════════
# AUTHENTICATED PAGES
# ═══════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def landing_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Public landing page — no auth required."""
    # Get public stats
    total = (await db.execute(select(func.count(Ticket.id)))).scalar() or 0
    resolved = (await db.execute(select(func.count(Ticket.id)).where(Ticket.status == "resolved"))).scalar() or 0
    in_progress = (await db.execute(select(func.count(Ticket.id)).where(Ticket.status.in_(["assigned", "in_progress"])))).scalar() or 0
    cities = (await db.execute(select(func.count(func.distinct(Ticket.city))).where(Ticket.city.isnot(None), Ticket.city != ""))).scalar() or 0

    # Get active announcements
    ann_result = await db.execute(
        select(Announcement)
        .where(Announcement.is_active == True)
        .order_by(Announcement.is_pinned.desc(), Announcement.created_at.desc())
        .limit(20)
    )
    announcements = ann_result.scalars().all()

    return templates.TemplateResponse("landing.html", {
        "request": request,
        "total": total,
        "resolved": resolved,
        "in_progress": in_progress,
        "cities": cities,
        "announcements": announcements,
    })


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    # Stats
    total = (await db.execute(select(func.count(Ticket.id)))).scalar() or 0
    submitted = (await db.execute(select(func.count(Ticket.id)).where(Ticket.status == "submitted"))).scalar() or 0
    assigned = (await db.execute(select(func.count(Ticket.id)).where(Ticket.status == "assigned"))).scalar() or 0
    in_progress = (await db.execute(select(func.count(Ticket.id)).where(Ticket.status == "in_progress"))).scalar() or 0
    resolved = (await db.execute(select(func.count(Ticket.id)).where(Ticket.status == "resolved"))).scalar() or 0
    total_users = (await db.execute(select(func.count(User.id)))).scalar() or 0
    total_depts = (await db.execute(select(func.count(Department.id)).where(Department.is_active == True))).scalar() or 0
    unread = await get_unread_count(db, user["user_id"])

    # Recent tickets
    result = await db.execute(
        select(Ticket, User.full_name)
        .join(User, Ticket.submitted_by == User.id, isouter=True)
        .order_by(Ticket.created_at.desc())
        .limit(10)
    )
    tickets_with_users = []
    for ticket, submitter_name in result.all():
        dept_name = "Unassigned"
        if ticket.assigned_to_dept:
            dept = await db.get(Department, ticket.assigned_to_dept)
            if dept:
                dept_name = dept.name
        tickets_with_users.append((ticket, submitter_name or "Unknown", dept_name))

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "user_name": user.get("role", "admin"),
        "user_role": user["role"],
        "unread_count": unread,
        "is_admin": user["role"] == "admin",
        "total": total,
        "submitted": submitted,
        "assigned": assigned,
        "in_progress": in_progress,
        "resolved": resolved,
        "total_users": total_users,
        "total_depts": total_depts,
        "tickets": tickets_with_users,
    })


@app.get("/submit", response_class=HTMLResponse)
async def submit_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_user(request)
    unread = await get_unread_count(db, user["user_id"]) if user else 0
    return templates.TemplateResponse("submit.html", {
        "request": request,
        "user": user,
        "user_name": user.get("role", "admin"),
        "user_role": user["role"],
        "unread_count": unread,
        "is_admin": user["role"] == "admin",
    })


@app.post("/submit")
@limiter.limit("10/minute")
async def submit_ticket(
    request: Request,
    subject: str = Form(...),
    description: str = Form(...),
    category: str = Form("general"),
    priority: str = Form("medium"),
    service_name: str = Form(""),
    city: str = Form(""),
    csrf_token: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    require_csrf(request, csrf_token)
    user = get_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    ticket_number = await generate_ticket_number(db)

    # AI department suggestion
    ai_dept = None
    ai_confidence = None
    ai_result = await suggest_department(subject, description)
    if ai_result:
        ai_dept = ai_result["dept"]
        ai_confidence = ai_result["confidence"]
    else:
        # Fallback to keyword matching
        kw_results = keyword_fallback(subject, description)
        if kw_results:
            ai_dept = kw_results[0]["dept"]
            ai_confidence = kw_results[0]["confidence"]

    ticket = Ticket(
        ticket_number=ticket_number,
        subject=subject,
        description=description,
        category=category,
        priority=priority,
        status="submitted",
        submitted_by=user["user_id"],
        ai_suggested_dept=ai_dept,
        ai_confidence=ai_confidence,
        service_name=service_name if service_name else None,
        city=city if city else None,
    )
    db.add(ticket)
    await db.flush()

    # Add history entry
    history = TicketHistory(
        ticket_id=ticket.id,
        new_status="submitted",
        changed_by=user["user_id"],
        note="Ticket submitted",
    )
    db.add(history)

    # Notify admin (in-app + email to minister)
    submitter_user = await db.get(User, user["user_id"])
    submitter_name = submitter_user.full_name if submitter_user else f"User #{user['user_id']}"
    await notify_admin_new_ticket(db, ticket, submitter_name)

    await db.commit()
    return RedirectResponse(url=f"/ticket/{ticket.id}", status_code=303)


@app.get("/ticket/{ticket_id}", response_class=HTMLResponse)
async def ticket_detail(request: Request, ticket_id: int, db: AsyncSession = Depends(get_db)):
    user = get_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    ticket = await db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    # Submitter name
    submitter = await db.get(User, ticket.submitted_by)
    submitter_name = submitter.full_name if submitter else "Unknown"

    # Department name
    dept_name = "Unassigned"
    if ticket.assigned_to_dept:
        dept = await db.get(Department, ticket.assigned_to_dept)
        if dept:
            dept_name = dept.name

    # History
    h = await db.execute(
        select(TicketHistory).where(TicketHistory.ticket_id == ticket.id).order_by(TicketHistory.created_at)
    )
    history = h.scalars().all()

    # Attachments
    att = await db.execute(
        select(Attachment).where(Attachment.ticket_id == ticket.id)
    )
    attachments = att.scalars().all()

    # Departments for dropdown
    depts = await get_depts(db)

    unread = await get_unread_count(db, user["user_id"])

    return templates.TemplateResponse("ticket_detail.html", {
        "request": request,
        "user": user,
        "user_name": user.get("role", "admin"),
        "user_role": user["role"],
        "unread_count": unread,
        "is_admin": user["role"] == "admin",
        "ticket": ticket,
        "submitter_name": submitter_name,
        "dept_name": dept_name,
        "history": history,
        "attachments": attachments,
        "departments": depts,
    })


@app.post("/ticket/{ticket_id}/update")
async def ticket_update(
    request: Request,
    ticket_id: int,
    status: str = Form(None),
    department_id: int = Form(None),
    assigned_user: int = Form(None),
    priority: str = Form(None),
    note: str = Form(""),
    csrf_token: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    require_csrf(request, csrf_token)
    user = get_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    ticket = await db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    # Role-based access: admin/department can modify, citizen cannot
    if user["role"] == "citizen":
        raise HTTPException(status_code=403, detail="Citizens cannot modify tickets. View only.")
    await require_ticket_access(request, ticket, db)

    old_status = ticket.status

    if status:
        ticket.status = status
        if status == "resolved":
            ticket.resolved_at = datetime.now(timezone.utc)
    if department_id:
        ticket.assigned_to_dept = department_id
    if assigned_user:
        ticket.assigned_to_user = assigned_user
    if priority:
        ticket.priority = priority

    history = TicketHistory(
        ticket_id=ticket.id,
        old_status=old_status,
        new_status=ticket.status or old_status,
        changed_by=user["user_id"],
        note=note or f"Updated by {user['role']}",
    )
    db.add(history)

    # Notify citizen if status changed
    if status and status != old_status:
        admin_user_obj = await db.get(User, user["user_id"])
        admin_name = admin_user_obj.full_name if admin_user_obj else f"Admin #{user['user_id']}"
        await notify_citizen_status_change(
            db, ticket, old_status, status,
            admin_name,
            note,
        )

    await db.commit()

    return RedirectResponse(url=f"/ticket/{ticket.id}", status_code=303)


@app.post("/ticket/{ticket_id}/upload")
@limiter.limit("5/minute")
async def upload_file(
    request: Request,
    ticket_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    user = get_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    ticket = await db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    # Role-based access: admin/department can upload to any accessible ticket,
    # citizen can only upload to their own tickets
    await require_ticket_access(request, ticket, db)

    # Validate file extension
    allowed_extensions = {'.pdf', '.doc', '.docx', '.jpg', '.jpeg', '.png', '.gif', '.txt', '.csv', '.xlsx'}
    ext = os.path.splitext(file.filename or '')[1].lower()
    if ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail=f"File type '{ext}' not allowed. Allowed: {', '.join(sorted(allowed_extensions))}")
    stored_name = f"{uuid.uuid4().hex}_{file.filename}"
    upload_dir = os.path.join(os.path.dirname(BASE_DIR), "uploads", str(ticket_id))
    os.makedirs(upload_dir, exist_ok=True)

    content = await file.read()
    with open(os.path.join(upload_dir, stored_name), "wb") as f:
        f.write(content)

    attachment = Attachment(
        ticket_id=ticket.id,
        original_name=file.filename,
        stored_name=stored_name,
        uploaded_by=user["user_id"],
    )
    db.add(attachment)
    await db.commit()

    return RedirectResponse(url=f"/ticket/{ticket.id}", status_code=303)


@app.get("/uploads/{ticket_id}/{stored_name}")
async def download_file(request: Request, ticket_id: int, stored_name: str, db: AsyncSession = Depends(get_db)):
    user = get_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    ticket = await db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    await require_ticket_access(request, ticket, db)

    file_path = os.path.join(os.path.dirname(BASE_DIR), "uploads", str(ticket_id), stored_name)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path, filename=stored_name.split("_", 1)[-1] if "_" in stored_name else stored_name)


# ═══════════════════════════════════════════════════════════════════
# ADMIN PAGES
# ═══════════════════════════════════════════════════════════════════

@app.get("/admin/tickets", response_class=HTMLResponse)
async def admin_tickets(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    q = request.query_params.get("q", "")
    status_filter = request.query_params.get("status", "")
    dept_filter = request.query_params.get("department", "")
    service_filter = request.query_params.get("service_name", "")
    city_filter = request.query_params.get("city", "")

    query = select(Ticket, User.full_name).join(User, Ticket.submitted_by == User.id, isouter=True)
    if q:
        query = query.where(Ticket.subject.contains(q) | Ticket.ticket_number.contains(q))
    if status_filter:
        query = query.where(Ticket.status == status_filter)
    if dept_filter:
        # Accept either department ID (number) or name (string)
        if dept_filter.isdigit():
            query = query.where(Ticket.assigned_to_dept == int(dept_filter))
        else:
            # Look up department ID by name
            dept_result = await db.execute(
                select(Department.id).where(Department.name.contains(dept_filter))
            )
            dept_ids = [r[0] for r in dept_result.all()]
            if dept_ids:
                query = query.where(Ticket.assigned_to_dept.in_(dept_ids))
            else:
                # No matching department — return no results
                query = query.where(Ticket.id == -1)
    if service_filter:
        query = query.where(Ticket.service_name.contains(service_filter))
    if city_filter:
        query = query.where(Ticket.city.contains(city_filter))

    result = await db.execute(query.order_by(Ticket.created_at.desc()))
    tickets_with_users = []
    for ticket, submitter_name in result.all():
        dept_name = "Unassigned"
        if ticket.assigned_to_dept:
            dept = await db.get(Department, ticket.assigned_to_dept)
            if dept:
                dept_name = dept.name
        tickets_with_users.append((ticket, submitter_name or "Unknown", dept_name))

    depts = await get_depts(db)
    unread = await get_unread_count(db, user["user_id"])

    # Get distinct cities and service names for filter dropdowns
    cities_result = await db.execute(
        select(Ticket.city).where(Ticket.city.isnot(None), Ticket.city != "").distinct()
    )
    cities = sorted([r[0] for r in cities_result.all() if r[0]])
    services_result = await db.execute(
        select(Ticket.service_name).where(Ticket.service_name.isnot(None), Ticket.service_name != "").distinct()
    )
    services = sorted([r[0] for r in services_result.all() if r[0]])

    return templates.TemplateResponse("admin_tickets.html", {
        "request": request,
        "user": user,
        "user_name": user.get("role", "admin"),
        "user_role": user["role"],
        "unread_count": unread,
        "is_admin": user["role"] == "admin",
        "tickets": tickets_with_users,
        "departments": depts,
        "q": q,
        "status_filter": status_filter,
        "dept_filter": dept_filter,
        "service_filter": service_filter,
        "city_filter": city_filter,
        "cities": cities,
        "services": services,
    })


@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    result = await db.execute(select(User).order_by(User.created_at.desc()))
    users = result.scalars().all()
    depts = await get_depts(db)
    unread = await get_unread_count(db, user["user_id"])

    return templates.TemplateResponse("admin_users.html", {
        "request": request,
        "user": user,
        "user_name": user.get("role", "admin"),
        "user_role": user["role"],
        "unread_count": unread,
        "is_admin": user["role"] == "admin",
        "users": users,
        "departments": depts,
    })


@app.post("/admin/users/create")
async def admin_create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(...),
    email: str = Form(""),
    phone: str = Form(""),
    role: str = Form("citizen"),
    department_id: int = Form(None),
    csrf_token: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    require_csrf(request, csrf_token)
    user = get_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse(url="/login", status_code=303)

    new_user = User(
        username=username,
        password_hash=hash_password(password),
        full_name=full_name,
        email=email,
        phone=phone,
        role=role,
        department_id=department_id,
    )
    db.add(new_user)
    await db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@app.post("/admin/users/{user_id}/toggle")
async def admin_toggle_user(
    request: Request,
    user_id: int,
    csrf_token: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    require_csrf(request, csrf_token)
    user = get_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse(url="/login", status_code=303)

    target = await db.get(User, user_id)
    if target:
        target.is_active = not target.is_active
        await db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@app.post("/admin/users/{user_id}/update-role")
async def admin_update_role(
    request: Request,
    user_id: int,
    role: str = Form(...),
    department_id: int = Form(None),
    csrf_token: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    require_csrf(request, csrf_token)
    user = get_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse(url="/login", status_code=303)

    target = await db.get(User, user_id)
    if target:
        target.role = role
        target.department_id = department_id
        await db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@app.get("/admin/analytics", response_class=HTMLResponse)
async def admin_analytics(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    unread = await get_unread_count(db, user["user_id"])
    return templates.TemplateResponse("analytics.html", {
        "request": request,
        "user": user,
        "user_name": user.get("role", "admin"),
        "user_role": user["role"],
        "unread_count": unread,
        "is_admin": user["role"] == "admin",
    })


@app.get("/dept/tickets", response_class=HTMLResponse)
async def dept_tickets(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    return templates.TemplateResponse("dept_tickets.html", {
        "request": request,
        "user": user,
    })


# ═══════════════════════════════════════════════════════════════════
# NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════════

@app.get("/notifications", response_class=HTMLResponse)
async def notifications_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    result = await db.execute(
        select(Notification)
        .where(Notification.user_id == user["user_id"])
        .order_by(Notification.created_at.desc())
    )
    notifications = result.scalars().all()
    unread = await get_unread_count(db, user["user_id"])

    return templates.TemplateResponse("notifications.html", {
        "request": request,
        "user": user,
        "user_name": user.get("role", "admin"),
        "user_role": user["role"],
        "unread_count": unread,
        "is_admin": user["role"] == "admin",
        "notifications": notifications,
    })


@app.post("/notifications/mark-read/{notif_id}")
async def mark_read(request: Request, notif_id: int, db: AsyncSession = Depends(get_db)):
    user = get_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    notif = await db.get(Notification, notif_id)
    if notif and notif.user_id == user["user_id"]:
        notif.is_read = True
        await db.commit()
    return RedirectResponse(url="/notifications", status_code=303)


@app.post("/notifications/mark-all-read")
async def mark_all_read(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    result = await db.execute(
        select(Notification).where(
            Notification.user_id == user["user_id"],
            Notification.is_read == False,
        )
    )
    for notif in result.scalars().all():
        notif.is_read = True
    await db.commit()
    return RedirectResponse(url="/notifications", status_code=303)


# ═══════════════════════════════════════════════════════════════════
# API ENDPOINTS (JSON)
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/notifications/unread")
async def api_unread_count(request: Request, db: AsyncSession = Depends(get_db)):
    """Return unread notification count for AJAX polling."""
    user = get_user(request)
    if not user:
        return JSONResponse({"count": 0})
    count = await get_unread_count(db, user["user_id"])
    return JSONResponse({"count": count})


@app.get("/api/notifications/recent")
async def api_recent_notifications(request: Request, db: AsyncSession = Depends(get_db)):
    """Return last 5 notifications for dropdown."""
    user = get_user(request)
    if not user:
        return JSONResponse({"notifications": []})
    result = await db.execute(
        select(Notification)
        .where(Notification.user_id == user["user_id"])
        .order_by(Notification.created_at.desc())
        .limit(5)
    )
    notifs = result.scalars().all()
    return JSONResponse({
        "notifications": [
            {
                "id": n.id,
                "title": n.title,
                "message": n.message,
                "ticket_id": n.ticket_id,
                "is_read": n.is_read,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in notifs
        ]
    })


@app.get("/api/stats")
async def api_stats(db: AsyncSession = Depends(get_db)):
    total = (await db.execute(select(func.count(Ticket.id)))).scalar() or 0
    submitted = (await db.execute(select(func.count(Ticket.id)).where(Ticket.status == "submitted"))).scalar() or 0
    assigned = (await db.execute(select(func.count(Ticket.id)).where(Ticket.status == "assigned"))).scalar() or 0
    in_progress = (await db.execute(select(func.count(Ticket.id)).where(Ticket.status == "in_progress"))).scalar() or 0
    resolved = (await db.execute(select(func.count(Ticket.id)).where(Ticket.status == "resolved"))).scalar() or 0
    closed = (await db.execute(select(func.count(Ticket.id)).where(Ticket.status == "closed"))).scalar() or 0
    total_users = (await db.execute(select(func.count(User.id)))).scalar() or 0
    total_depts = (await db.execute(select(func.count(Department.id)).where(Department.is_active == True))).scalar() or 0

    return {
        "total": total, "submitted": submitted, "assigned": assigned,
        "in_progress": in_progress, "resolved": resolved, "closed": closed,
        "total_users": total_users, "total_depts": total_depts,
    }


@app.get("/api/ticket/{ticket_id}")
async def api_ticket(ticket_id: int, db: AsyncSession = Depends(get_db)):
    ticket = await db.get(Ticket, ticket_id)
    if not ticket:
        return JSONResponse({"error": "Not found"}, status_code=404)

    h = await db.execute(
        select(TicketHistory).where(TicketHistory.ticket_id == ticket.id).order_by(TicketHistory.created_at)
    )
    history = []
    for entry in h.scalars().all():
        changer = await db.get(User, entry.changed_by)
        history.append({
            "id": entry.id,
            "ticket_id": entry.ticket_id,
            "old_status": entry.old_status,
            "new_status": entry.new_status,
            "changed_by": entry.changed_by,
            "note": entry.note,
            "created_at": entry.created_at.isoformat(),
            "changer_name": changer.full_name if changer else "Unknown",
        })

    return {
        "ticket": {
            "id": ticket.id,
            "ticket_number": ticket.ticket_number,
            "subject": ticket.subject,
            "description": ticket.description,
            "category": ticket.category,
            "priority": ticket.priority,
            "status": ticket.status,
            "submitted_by": ticket.submitted_by,
            "assigned_to_dept": ticket.assigned_to_dept,
            "assigned_to_user": ticket.assigned_to_user,
            "created_at": ticket.created_at.isoformat(),
            "updated_at": ticket.updated_at.isoformat() if ticket.updated_at else ticket.created_at.isoformat(),
            "resolved_at": ticket.resolved_at.isoformat() if ticket.resolved_at else None,
        },
        "history": history,
    }


@app.get("/api/unread-count")
async def api_unread_count(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_user(request)
    if not user:
        return {"count": 0}
    count = await get_unread_count(db, user["user_id"])
    return {"count": count}


@app.get("/api/suggest-dept")
async def api_suggest_dept(subject: str = "", description: str = ""):
    """AI-powered department suggestion with fallback to keyword matching."""
    if not subject and not description:
        return {"suggestions": []}

    # Try AI first
    ai_result = await suggest_department(subject, description)
    if ai_result:
        return {"suggestions": [ai_result]}

    # Fallback to keywords
    kw_results = keyword_fallback(subject, description)
    return {"suggestions": kw_results}


@app.get("/api/analytics/status")
async def api_analytics_status(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Ticket.status, func.count(Ticket.id)).group_by(Ticket.status)
    )
    return {r[0]: r[1] for r in result.all()}


@app.get("/api/analytics/categories")
async def api_analytics_categories(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Ticket.category, func.count(Ticket.id)).group_by(Ticket.category)
    )
    return {r[0] or "general": r[1] for r in result.all()}


@app.get("/api/analytics/departments")
async def api_analytics_departments(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Department.name, func.count(Ticket.id))
        .join(Ticket, Department.id == Ticket.assigned_to_dept, isouter=True)
        .group_by(Department.id)
    )
    return {r[0] or "Unassigned": r[1] for r in result.all()}


@app.get("/api/analytics/priority")
async def api_analytics_priority(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Ticket.priority, func.count(Ticket.id)).group_by(Ticket.priority)
    )
    return {r[0]: r[1] for r in result.all()}


@app.get("/api/analytics/over-time")
async def api_analytics_over_time(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(
            func.date(Ticket.created_at).label("date"),
            func.count(Ticket.id),
        ).group_by(func.date(Ticket.created_at)).order_by(func.date(Ticket.created_at))
    )
    return {str(r[0]): r[1] for r in result.all()}


@app.get("/api/analytics/resolution")
async def api_analytics_resolution(db: AsyncSession = Depends(get_db)):
    total = (await db.execute(select(func.count(Ticket.id)))).scalar() or 0
    resolved = (await db.execute(select(func.count(Ticket.id)).where(Ticket.status.in_(["resolved", "closed"])))).scalar() or 0
    # Calculate avg/min/max resolution time for resolved tickets
    result = await db.execute(
        select(
            func.julianday(Ticket.resolved_at) - func.julianday(Ticket.created_at)
        ).where(Ticket.resolved_at.isnot(None))
    )
    days_list = [r[0] for r in result.all() if r[0] is not None]
    avg_days = round(sum(days_list) / len(days_list), 1) if days_list else 0
    min_days = round(min(days_list), 1) if days_list else 0
    max_days = round(max(days_list), 1) if days_list else 0
    return {
        "total": total,
        "resolved": resolved,
        "rate": round(resolved / total * 100, 1) if total > 0 else 0,
        "avg_days": avg_days,
        "min_days": min_days,
        "max_days": max_days,
    }


@app.get("/api/analytics/service")
async def api_analytics_service(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Ticket.service_name, func.count(Ticket.id))
        .where(Ticket.service_name.isnot(None), Ticket.service_name != "")
        .group_by(Ticket.service_name)
    )
    return {r[0]: r[1] for r in result.all()}


@app.get("/api/analytics/city")
async def api_analytics_city(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Ticket.city, func.count(Ticket.id))
        .where(Ticket.city.isnot(None), Ticket.city != "")
        .group_by(Ticket.city)
    )
    return {r[0]: r[1] for r in result.all()}


# ═══════════════════════════════════════════════════════════════════
#  PUBLIC APIs (no auth required)
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/public/stats")
async def public_stats(db: AsyncSession = Depends(get_db)):
    """Public statistics for the landing page."""
    total = (await db.execute(select(func.count(Ticket.id)))).scalar() or 0
    resolved = (await db.execute(select(func.count(Ticket.id)).where(Ticket.status == "resolved"))).scalar() or 0
    in_progress = (await db.execute(select(func.count(Ticket.id)).where(Ticket.status.in_(["assigned", "in_progress"])))).scalar() or 0
    cities = (await db.execute(select(func.count(func.distinct(Ticket.city))).where(Ticket.city.isnot(None), Ticket.city != ""))).scalar() or 0
    departments = (await db.execute(select(func.count(Department.id)).where(Department.is_active == True))).scalar() or 0
    return JSONResponse({
        "total_tickets": total,
        "resolved_tickets": resolved,
        "in_progress_tickets": in_progress,
        "cities_covered": cities,
        "active_departments": departments,
    })


@app.get("/api/public/announcements")
async def public_announcements(db: AsyncSession = Depends(get_db)):
    """Active announcements for the public landing page."""
    result = await db.execute(
        select(Announcement)
        .where(Announcement.is_active == True)
        .order_by(Announcement.is_pinned.desc(), Announcement.created_at.desc())
        .limit(20)
    )
    items = []
    for a in result.scalars().all():
        items.append({
            "id": a.id,
            "title": a.title,
            "body": a.body,
            "image_url": a.image_url or "",
            "priority": a.priority,
            "is_pinned": a.is_pinned,
            "created_at": a.created_at.isoformat() if a.created_at else "",
        })
    return JSONResponse(items)


# ═══════════════════════════════════════════════════════════════════
#  ADMIN ANNOUNCEMENT CRUD
# ═══════════════════════════════════════════════════════════════════

@app.get("/admin/announcements", response_class=HTMLResponse)
async def admin_announcements_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse(url="/login", status_code=303)
    result = await db.execute(
        select(Announcement).order_by(Announcement.created_at.desc())
    )
    announcements_list = result.scalars().all()
    unread = await get_unread_count(db, user["user_id"])
    return templates.TemplateResponse("admin_announcements.html", {
        "request": request, "user": user,
        "announcements": announcements_list,
        "unread_count": unread, "is_admin": True,
    })


@app.post("/admin/announcements/create")
async def admin_create_announcement(
    request: Request,
    title: str = Form(...),
    body: str = Form(...),
    priority: str = Form("normal"),
    is_pinned: bool = Form(False),
    image_url: str = Form(""),
    csrf_token: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    require_csrf(request, csrf_token)
    user = get_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse(url="/login", status_code=303)
    ann = Announcement(
        title=title, body=body, priority=priority,
        is_pinned=is_pinned, image_url=image_url,
    )
    db.add(ann)
    await db.commit()
    return RedirectResponse(url="/admin/announcements", status_code=303)


@app.post("/admin/announcements/{ann_id}/toggle")
async def admin_toggle_announcement(
    request: Request, ann_id: int,
    csrf_token: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    require_csrf(request, csrf_token)
    user = get_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse(url="/login", status_code=303)
    ann = await db.get(Announcement, ann_id)
    if ann:
        ann.is_active = not ann.is_active
        await db.commit()
    return RedirectResponse(url="/admin/announcements", status_code=303)


@app.post("/admin/announcements/{ann_id}/pin")
async def admin_pin_announcement(
    request: Request, ann_id: int,
    csrf_token: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    require_csrf(request, csrf_token)
    user = get_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse(url="/login", status_code=303)
    ann = await db.get(Announcement, ann_id)
    if ann:
        ann.is_pinned = not ann.is_pinned
        await db.commit()
    return RedirectResponse(url="/admin/announcements", status_code=303)


@app.post("/admin/announcements/{ann_id}/delete")
async def admin_delete_announcement(
    request: Request, ann_id: int,
    csrf_token: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    require_csrf(request, csrf_token)
    user = get_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse(url="/login", status_code=303)
    ann = await db.get(Announcement, ann_id)
    if ann:
        await db.delete(ann)
        await db.commit()
    return RedirectResponse(url="/admin/announcements", status_code=303)


# ─── Chatbot API ──────────────────────────────────────────────────

@app.get("/api/chat/welcome")
async def chat_welcome(request: Request, db: AsyncSession = Depends(get_db)):
    """Return role-appropriate welcome message + chat history."""
    user = request.state.user
    if not user:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)
    
    # Load chat history from database
    history = await load_chat_history(db, user["user_id"], limit=50)
    
    if user["role"] in ("admin", "department"):
        return {
            "reply": "Hello! I'm your admin assistant. I can query the ticket database for you. Try asking:\n• How many tickets are pending?\n• What's the breakdown by department?\n• Show me today's submissions\n• Any overdue tickets?",
            "history": history
        }
    else:
        return {
            "reply": CITIZEN_WELCOME,
            "history": history
        }


@app.get("/api/chat/history")
async def chat_history_endpoint(request: Request, db: AsyncSession = Depends(get_db)):
    """Load chat history for current user."""
    user = request.state.user
    if not user:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)
    history = await load_chat_history(db, user["user_id"], limit=100)
    return {"history": history}


@app.post("/api/chat/clear")
async def chat_clear_endpoint(request: Request, db: AsyncSession = Depends(get_db)):
    """Clear chat history for current user."""
    user = request.state.user
    if not user:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)
    await clear_chat_history(db, user["user_id"])
    return {"reply": "Chat history cleared."}


@app.post("/api/chat")
@limiter.limit("20/minute")
async def chat_endpoint(
    request: Request,
    message: str = Form(...),
    history_json: str = Form("[]"),
    db: AsyncSession = Depends(get_db),
):
    user = request.state.user
    if not user:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    try:
        history = json.loads(history_json)
    except Exception:
        history = []

    result = await handle_chat(
        user_id=user["user_id"],
        role=user["role"],
        message=message,
        history=history,
        db=db,
    )
    return result
