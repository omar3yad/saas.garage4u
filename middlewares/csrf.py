import logging
import secrets
from urllib.parse import parse_qs
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

CSRF_COOKIE_NAME = "pgms_csrf_token"
CSRF_HEADER_NAMES = ("x-csrf-token", "x-csrftoken")
CSRF_FORM_FIELDS = ("_csrf_token", "csrf_token")

EXEMPT_PATH_PREFIXES = (
    "/api/",
    "/ws/",
    "/static/",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/metrics",
)

EXEMPT_EXACT_PATHS = {
    "/ui/login",
}

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class CSRFMiddleware:
    """Pure ASGI CSRF Protection Middleware for Browser-based UI Routes.
    
    Protects state-mutating requests (POST, PUT, PATCH, DELETE) under /ui/*
    by requiring a matching CSRF token in the form or header against the
    pgms_csrf_token cookie (Double-Submit Cookie pattern).
    
    Exemptions:
    - Stateless REST API routes (/api/*, /api/v1/*)
    - Edge camera agents (/api/v1/edge/*)
    - WebSocket handshakes (/ws/*)
    - Safe HTTP methods (GET, HEAD, OPTIONS)
    - Authentication login endpoint (/ui/login)
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()
        headers = Headers(raw=scope.get("headers", []))

        # Parse cookies from Cookie header
        cookie_header = headers.get("cookie", "")
        cookies = {}
        if cookie_header:
            from http.cookies import SimpleCookie
            c = SimpleCookie()
            try:
                c.load(cookie_header)
                cookies = {k: v.value for k, v in c.items()}
            except Exception:
                pass

        cookie_token = cookies.get(CSRF_COOKIE_NAME)
        csrf_token = cookie_token or secrets.token_hex(32)

        # Store in scope state if state exists or init it
        if "state" not in scope:
            scope["state"] = {}
        scope["state"]["csrf_token"] = csrf_token

        is_ui_path = path.startswith("/ui/") or path == "/ui"
        is_exempt = (
            not is_ui_path
            or any(path.startswith(prefix) for prefix in EXEMPT_PATH_PREFIXES)
            or path in EXEMPT_EXACT_PATHS
        )

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                if is_ui_path and not cookie_token:
                    raw_headers = list(message.get("headers", []))
                    cookie_val = f"{CSRF_COOKIE_NAME}={csrf_token}; Path=/; SameSite=Lax"
                    raw_headers.append((b"set-cookie", cookie_val.encode("latin-1")))
                    message["headers"] = raw_headers
            await send(message)

        if is_exempt or method in SAFE_METHODS:
            await self.app(scope, receive, send_wrapper)
            return

        # Mutating requests on non-exempt /ui/* paths
        submitted_token = None

        # 1. Header check
        for h in CSRF_HEADER_NAMES:
            val = headers.get(h)
            if val:
                submitted_token = val.strip()
                break

        # 2. Body check: Read body and replay it so downstream handlers can parse it
        body_chunks = []
        more_body = True
        while more_body:
            msg = await receive()
            body_chunks.append(msg.get("body", b""))
            more_body = msg.get("more_body", False)

        body = b"".join(body_chunks)

        async def replayed_receive():
            return {"type": "http.request", "body": body, "more_body": False}

        if not submitted_token:
            content_type = headers.get("content-type", "").lower()
            if "application/x-www-form-urlencoded" in content_type:
                try:
                    parsed = parse_qs(body.decode("utf-8", errors="replace"))
                    for f in CSRF_FORM_FIELDS:
                        if f in parsed and parsed[f]:
                            submitted_token = parsed[f][0].strip()
                            break
                except Exception as e:
                    logger.debug("Failed to parse form in CSRFMiddleware: %s", e)
            elif "multipart/form-data" in content_type:
                from starlette.formparsers import MultiPartParser
                try:
                    parser = MultiPartParser(headers, replayed_receive())
                    form = await parser.parse()
                    for f in CSRF_FORM_FIELDS:
                        val = form.get(f)
                        if val and isinstance(val, str):
                            submitted_token = val.strip()
                            break
                except Exception as e:
                    logger.debug("Failed to parse multipart in CSRF: %s", e)

        # Validate token
        is_valid = False
        if submitted_token and cookie_token:
            is_valid = secrets.compare_digest(submitted_token, cookie_token)

        if not is_valid:
            logger.warning(
                "CSRF validation failed for %s %s (submitted: %s, cookie: %s)",
                method,
                path,
                bool(submitted_token),
                bool(cookie_token),
            )
            response = JSONResponse(
                status_code=403,
                content={
                    "detail": "CSRF token missing or invalid",
                    "code": "CSRF_TOKEN_INVALID",
                },
                headers={"X-Error-Code": "CSRF_TOKEN_INVALID"},
            )
            await response(scope, replayed_receive, send)
            return

        await self.app(scope, replayed_receive, send_wrapper)
