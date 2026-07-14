"""Security utilities: password hashing, CSRF tokens, encrypted sessions."""
import secrets
import hashlib
import hmac
import json
import base64
from datetime import datetime, timezone
from typing import Optional

from passlib.context import CryptContext
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from app.core.config import settings

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Session serializer — encrypts payload so user_id/role are NOT visible in plaintext
_session_serializer = URLSafeTimedSerializer(settings.SECRET_KEY)
SESSION_SALT = "sindh-ticket-session"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ─── Encrypted Session Cookies ───────────────────────────────────
# OLD (insecure): base64(user_id + role) → visible plaintext
# NEW: itsdangerous signed+serialized → payload is opaque to client

def create_session_token(user_id: int, role: str) -> str:
    """Create a signed, opaque session token. Payload is NOT readable by client."""
    return _session_serializer.dumps(
        {"user_id": user_id, "role": role},
        salt=SESSION_SALT,
    )


def decode_session_token(token: str) -> Optional[dict]:
    """Decode and verify a session token. Returns None if invalid/expired."""
    try:
        data = _session_serializer.loads(
            token,
            salt=SESSION_SALT,
            max_age=settings.SESSION_MAX_AGE,
        )
        return data
    except (BadSignature, SignatureExpired):
        return None


# ─── CSRF Tokens ─────────────────────────────────────────────────
# Double-submit cookie pattern: token in cookie + hidden form field

def generate_csrf_token(session_token: str) -> str:
    """Generate a CSRF token bound to the session."""
    return hmac.new(
        settings.SECRET_KEY.encode(),
        session_token.encode(),
        hashlib.sha256,
    ).hexdigest()


def validate_csrf_token(session_token: str, submitted_token: str) -> bool:
    """Validate the submitted CSRF token matches the expected value."""
    if not session_token or not submitted_token:
        return False
    expected = generate_csrf_token(session_token)
    return hmac.compare_digest(expected, submitted_token)
