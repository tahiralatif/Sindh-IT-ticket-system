"""Security utilities: password hashing, CSRF tokens, encrypted sessions, password reset."""
import secrets
import hashlib
import hmac
import json
import base64
import re
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

# Password reset serializer (separate salt)
_password_reset_serializer = URLSafeTimedSerializer(settings.SECRET_KEY)
PASSWORD_RESET_SALT = "password-reset"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ─── Password Policy ─────────────────────────────────────────────
def validate_password_policy(password: str) -> Optional[str]:
    """Validate password meets security policy.
    Returns None if valid, or an error message string.
    """
    if len(password) < 8:
        return "Password must be at least 8 characters long"
    if not re.search(r'[A-Z]', password):
        return "Password must contain at least one uppercase letter"
    if not re.search(r'[a-z]', password):
        return "Password must contain at least one lowercase letter"
    if not re.search(r'[0-9]', password):
        return "Password must contain at least one digit"
    return None


# ─── Encrypted Session Cookies ───────────────────────────────────
# OLD (insecure): base64(user_id + role) → visible plaintext
# NEW: Fernet encryption + HMAC signing → payload is opaque to client

def create_session_token(user_id: int, role: str, ip_address: str = "", user_agent: str = "") -> str:
    """Create an encrypted, signed session token. Payload is NOT readable by client."""
    payload = json.dumps({
        "user_id": user_id,
        "role": role,
        "ip": ip_address,
        "ua": user_agent,
    })
    encrypted = _fernet.encrypt(payload.encode()).decode()
    return encrypted


def decode_session_token(token: str) -> Optional[dict]:
    """Decrypt and verify a session token. Returns None if invalid/expired."""
    try:
        decrypted = _fernet.decrypt(token.encode())
        return json.loads(decrypted.decode())
    except Exception:
        return None


# ─── Password Reset Tokens ───────────────────────────────────────
# In-memory store of active password reset tokens
password_reset_tokens = {}


def generate_password_reset_token(user_id: int) -> str:
    """Generate a time-limited password reset token. Expires in 1 hour."""
    token = _password_reset_serializer.dumps({"user_id": user_id}, salt=PASSWORD_RESET_SALT)
    password_reset_tokens[token] = {
        "user_id": user_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return token


def verify_password_reset_token(token: str) -> Optional[int]:
    """Verify a password reset token. Returns user_id if valid, None otherwise.
    Tokens expire after 1 hour (3600 seconds).
    """
    try:
        data = _password_reset_serializer.loads(token, salt=PASSWORD_RESET_SALT, max_age=3600)
        if token not in password_reset_tokens:
            return None
        return data.get("user_id")
    except (BadSignature, SignatureExpired):
        return None


def invalidate_password_reset_token(token: str):
    """Remove a password reset token from the store."""
    password_reset_tokens.pop(token, None)


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
