import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Request, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from config import get_settings
from database import engine
from utils.jinja import create_jinja2_environment

logger = logging.getLogger(__name__)
settings = get_settings()

# Initialize Jinja2Templates environment at module-level
templates = create_jinja2_environment(settings)

# Startup lifespan with database verification and subscription lifecycle management
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. DB connection check
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("Database connection checked and verified successfully.")
    except Exception as e:
        logger.critical("Database connection failed during startup check: %s", str(e))

    # 2. Phase 4: Subscription expiry and pending activation
    try:
        from database import AsyncSessionLocal
        from repositories.subscription_repo import SubscriptionRepository
        from repositories.subscription_plan_repo import SubscriptionPlanRepository
        from services.subscription_service import SubscriptionService
        from services.audit_service import AuditService

        async with AsyncSessionLocal() as session:
            session.info["is_system"] = True
            sub_repo = SubscriptionRepository(session)
            plan_repo = SubscriptionPlanRepository(session)
            audit_svc = AuditService(session)
            sub_svc = SubscriptionService(
                db=session,
                subscription_repo=sub_repo,
                plan_repo=plan_repo,
                card_service=None,
                audit_service=audit_svc,
            )
            expired_count = await sub_svc.expire_overdue_subscriptions()
            activated_count = await sub_svc.activate_pending_subscriptions()

            if expired_count > 0 or activated_count > 0:
                await audit_svc.log(
                    actor_id=None,
                    action="SUBSCRIPTIONS_BULK_EXPIRED",
                    entity_type="system",
                    entity_id=0,
                    before=None,
                    after={
                        "expired_count": expired_count,
                        "activated_count": activated_count,
                    },
                    actor_type="system",
                    garage_id=None,
                    owner_id=None,
                )
                await session.commit()
            logger.info(
                "Subscription startup sync: %d expired, %d activated",
                expired_count,
                activated_count,
            )
    except Exception as e:
        logger.critical("Startup subscription lifecycle failed: %s", e)

    # 3. Edge camera broker: cross-worker event bus + edge WebSocket hub
    from services.event_bus import create_event_bus
    from services.edge_hub import EdgeHub

    bus = create_event_bus(settings.DATABASE_URL, engine)
    hub = EdgeHub(bus, presence_ttl_s=settings.EDGE_PRESENCE_TTL_S)
    app.state.edge_hub = hub
    await bus.start()
    await hub.start()

    yield

    await hub.stop()
    await bus.stop()

app = FastAPI(
    title=settings.APP_NAME,
    docs_url="/docs" if settings.ENVIRONMENT != "production" else None,
    redoc_url="/redoc" if settings.ENVIRONMENT != "production" else None,
    lifespan=lifespan,
)

app.state.templates = templates

# CORS middleware configuration
if settings.ENVIRONMENT == "production" and "*" in settings.CORS_ORIGINS:
    raise RuntimeError("Wildcard CORS origin forbidden in production")

from middlewares.csrf import CSRFMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(CSRFMiddleware)

# Mount static files
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

# Database availability middleware
@app.middleware("http")
async def db_availability_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") or path.startswith("/ui/"):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as e:
            logger.error("Database connection failed during request check: %s", str(e))
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "Database is currently unavailable. Please try again later.",
                    "code": "DATABASE_UNAVAILABLE"
                }
            )
    return await call_next(request)

# Exception handlers
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 303 and exc.headers and "Location" in exc.headers:
        return RedirectResponse(exc.headers["Location"], status_code=303)
    # For UI API requests, return JSON with 401 so frontend knows session expired and can redirect cleanly
    is_api = "/api/" in request.url.path or "application/json" in request.headers.get("accept", "")
    if is_api and exc.status_code in (401, 403):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": "انتهت صلاحية الجلسة، يرجى تسجيل الدخول مجدداً", "code": "SESSION_EXPIRED"},
        )
    if request.url.path.startswith("/ui/admin") and exc.status_code in (401, 403):
        return RedirectResponse(f"/ui/login?next={request.url.path}", status_code=303)
    if request.url.path.startswith("/ui/") and exc.status_code == 401:
        return RedirectResponse("/ui/login", status_code=303)
        
    code = getattr(exc, "code", None)
    if not code and exc.headers:
        code = exc.headers.get("X-Error-Code")
    if not code:
        code = "ERROR"
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "code": code},
    )

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled server exception: %s", str(exc), exc_info=True)
    detail = "Internal server error"
    if settings.ENVIRONMENT == "development":
        detail = str(exc)
    return JSONResponse(
        status_code=500,
        content={"detail": detail, "code": "INTERNAL_ERROR"},
    )

# Import routers after templates definition to prevent circular imports
from routes.auth import router as auth_router
from routes.users import router as users_router
from routes.ui_auth import router as ui_auth_router
from routes.cards import router as cards_router
from routes.sessions import router as sessions_router
from routes.shifts import router as shifts_router
from routes.rates import router as rates_router
from routes.ui_operator import router as ui_operator_router
from routes.subscriptions_api import router as subscriptions_api_router
from routes.admin_api import router as admin_api_router
from routes.ui_admin import router as ui_admin_router, owner_ui_router
from routes.ui_subscriptions import router as ui_subscriptions_router
from routes.edge_ws import router as edge_ws_router
from routes.edge_api import router as edge_api_router
from routes.garage_management_api import router as garage_management_api_router
from routes.platform_admin import router as platform_admin_router
from routes.owner_api import router as owner_api_router

# Include routers
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(ui_auth_router)
app.include_router(cards_router)
app.include_router(sessions_router)
app.include_router(shifts_router)
app.include_router(rates_router)
app.include_router(ui_operator_router)
app.include_router(subscriptions_api_router)
app.include_router(admin_api_router)
app.include_router(ui_admin_router)
app.include_router(owner_ui_router)
app.include_router(ui_subscriptions_router)
app.include_router(edge_ws_router)
app.include_router(edge_api_router)
app.include_router(garage_management_api_router)
app.include_router(platform_admin_router)
app.include_router(owner_api_router)

@app.get("/owner/dashboard")
async def legacy_owner_dashboard_redirect():
    return RedirectResponse("/ui/owner/dashboard", status_code=303)

@app.get("/")
async def root_redirect():
    return RedirectResponse("/ui/login", status_code=303)

@app.get("/proposal", include_in_schema=False)
@app.get("/proposal.html", include_in_schema=False)
async def proposal_page():
    from fastapi.responses import FileResponse
    return FileResponse(os.path.join(BASE_DIR, "proposal.html"), media_type="text/html")

@app.get("/proposal-general", include_in_schema=False)
@app.get("/proposal-general.html", include_in_schema=False)
@app.get("/proposal/general", include_in_schema=False)
@app.get("/general-proposal", include_in_schema=False)
async def proposal_general_page():
    from fastapi.responses import FileResponse
    return FileResponse(os.path.join(BASE_DIR, "proposal-general.html"), media_type="text/html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    from fastapi.responses import FileResponse, Response
    fav_path = os.path.join(BASE_DIR, "static", "favicon.ico")
    if os.path.exists(fav_path):
        return FileResponse(fav_path, media_type="image/x-icon")
    return Response(status_code=204)

@app.get("/apple-touch-icon.png", include_in_schema=False)
async def apple_touch_icon():
    from fastapi.responses import FileResponse, Response
    icon_path = os.path.join(BASE_DIR, "static", "apple-touch-icon.png")
    if os.path.exists(icon_path):
        return FileResponse(icon_path, media_type="image/png")
    return Response(status_code=204)


