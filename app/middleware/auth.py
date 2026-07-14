"""Middleware: session extraction, CSRF protection."""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse
from app.core.security import decode_session_token, validate_csrf_token

# Paths that don't require authentication
PUBLIC_PATHS = {"/login", "/register", "/track", "/logout", "/static"}
API_PUBLIC = {"/api/suggest-dept", "/api/stats"}

# Paths that don't need CSRF (GET, API, etc.)
CSRF_EXEMPT_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_EXEMPT_PATHS = {"/api/", "/static", "/uploads"}


class SessionMiddleware(BaseHTTPMiddleware):
    """Extract session from cookie and inject user info into request state."""

    async def dispatch(self, request: Request, call_next):
        request.state.user = None
        request.state.session_token = None

        session_cookie = request.cookies.get("session")
        if session_cookie:
            data = decode_session_token(session_cookie)
            if data:
                request.state.user = data
                request.state.session_token = session_cookie

        # Auth check
        path = request.url.path
        is_public = any(path.startswith(p) for p in PUBLIC_PATHS) or path in PUBLIC_PATHS
        is_api_public = any(path.startswith(p) for p in API_PUBLIC)

        if not request.state.user and not is_public and not is_api_public:
            return RedirectResponse(url="/login", status_code=303)

        return await call_next(request)


class CSRFMiddleware(BaseHTTPMiddleware):
    """CSRF protection using double-submit cookie pattern.

    NOTE: We only check the X-CSRF-Token header here to avoid consuming the
    request body. Form-based CSRF token checking is done in route handlers
    or via a dependency, since reading request.form() here would consume
    the body before FastAPI can parse it.
    """

    async def dispatch(self, request: Request, call_next):
        # Skip CSRF for safe methods and exempt paths
        if request.method in CSRF_EXEMPT_METHODS:
            return await call_next(request)

        path = request.url.path
        if any(path.startswith(p) for p in CSRF_EXEMPT_PATHS):
            return await call_next(request)

        # For state-changing requests, check X-CSRF-Token header
        # (form-based CSRF is validated in the template via hidden field
        #  and the route can optionally validate it)
        session_token = request.cookies.get("session", "")
        csrf_token = request.headers.get("X-CSRF-Token", "")

        # If a header token was provided, validate it
        if session_token and csrf_token:
            if not validate_csrf_token(session_token, csrf_token):
                if path.startswith("/api/"):
                    from starlette.responses import JSONResponse
                    return JSONResponse({"detail": "CSRF token invalid"}, status_code=403)
                return RedirectResponse(url=request.url.path, status_code=303)

        return await call_next(request)
