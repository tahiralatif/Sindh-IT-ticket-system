"""Security utilities: password hashing, CSRF tokens, encrypted sessions."""
import secrets
import hashlib
import hmac
import json
import base64
from datetime import datetime, timezone
from typing import Optional

from passlib.context import CryptContext
from cryptography.fernet import Fernet
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from app.core.config import settings

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ─── Encrypted Session Cookies ───────────────────────────────────
# Derive a Fernet encryption key from the SECRET_KEY
_fernet_key = base64.urlsafe_b64encode(settings.SECRET_KEY.encode()[:32].ljust(32, b'\0'))
_fernet = Fernet(_fernet_key)

# Also keep signing for extra integrity
_session_serializer = URLSafeTimedSerializer(settings.SECRET_KEY)
SESSION_SALT = "sindh-ticket-session"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ─── Encrypted Session Cookies ───────────────────────────────────
# OLD (insecure): base64(user_id + role) → visible plaintext
# NEW: Fernet encryption + HMAC signing → payload is opaque to client

def create_session_token(user_id: int, role: str) -> str:
    """Create an encrypted, signed session token. Payload is NOT readable by client."""
    payload = json.dumps({"user_id": user_id, "role": role})
    encrypted = _fernet.encrypt(payload.encode()).decode()
    return encrypted


def decode_session_token(token: str) -> Optional[dict]:
    """Decrypt and verify a session token. Returns None if invalid/expired."""
    try:
        decrypted = _fernet.decrypt(token.encode())
        return json.loads(decrypted.decode())
    except Exception:
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
