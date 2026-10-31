"""WebSocket endpoint the garage edge PC keeps open (it sits behind NAT and
cannot be reached directly, so it dials out to us)."""
import asyncio
import hashlib
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from pydantic import ValidationError

from config import get_settings
from database import AsyncSessionLocal
from schemas.edge import CaptureResult, EdgeHello
from services.edge_hub import EdgeConnection, EdgeHub
from services.plate_capture_service import CaptureRejected, PlateCaptureService
from repositories.edge_device_repo import EdgeDeviceRepository
from repositories.garage_repo import GarageRepository
from repositories.garage_config_repo import GarageConfigRepository
from models.edge_device import EdgeDevice

logger = logging.getLogger(__name__)

router = APIRouter(tags=["edge"])

_HELLO_TIMEOUT_S = 5.0


from sqlalchemy.ext.asyncio import AsyncSession

async def _authenticate_edge_device(
    websocket: WebSocket,
    db: AsyncSession | None = None,
) -> tuple[EdgeDevice | None, bool]:
    """Authenticates the edge device using X-Edge-Token (or Authorization/query fallback).
    Checks that the device exists, is not revoked, and that the garage is entitled to ANPR.
    Uses a short-lived AsyncSession and closes it immediately to prevent connection pool starvation.
    Returns (device, is_entitled).
    """
    token = websocket.headers.get("x-edge-token")
    if not token:
        # Fallback to query parameter or Authorization Bearer header
        token = websocket.query_params.get("token")
        if not token:
            header = websocket.headers.get("authorization", "")
            scheme, _, bearer_token = header.partition(" ")
            if scheme.lower() == "bearer":
                token = bearer_token.strip()

    if not token:
        return None, False

    token_hash = hashlib.sha256(token.strip().encode()).hexdigest()

    async def _check(session: AsyncSession) -> tuple[EdgeDevice | None, bool]:
        device_repo = EdgeDeviceRepository(session)
        device = await device_repo.get_by_token_hash(token_hash)
        if not device or device.revoked_at is not None:
            return None, False

        # Check ANPR entitlement (can_use_anpr and anpr_enabled)
        garage_repo = GarageRepository(session)
        config_repo = GarageConfigRepository(session)
        garage = await garage_repo.get_by_id(device.garage_id)
        config = await config_repo.get_by_garage_id(device.garage_id)

        if not garage or not config:
            return None, False

        is_entitled = bool(garage.can_use_anpr and config.anpr_enabled)
        if not is_entitled:
            return device, False

        await device_repo.update_last_seen(device.id)
        await session.flush()

        return device, True

    if db is not None:
        return await _check(db)

    app_db = None
    if hasattr(websocket, "app") and hasattr(websocket.app, "state"):
        app_db = getattr(websocket.app.state, "db", None)

    if app_db is not None:
        return await _check(app_db)

    # Open brief session and close immediately before accepting websocket
    async with AsyncSessionLocal() as session:
        res = await _check(session)
        await session.commit()
        return res


@router.websocket("/ws/edge")
async def edge_socket(websocket: WebSocket) -> None:
    settings = get_settings()
    hub: EdgeHub | None = getattr(websocket.app.state, "edge_hub", None)

    device, is_entitled = await _authenticate_edge_device(websocket)

    # Reject connection before accept() if unauthenticated or not entitled for ANPR
    if hub is None or device is None or not is_entitled:
        logger.warning(
            "Rejected edge connection from %s: device=%s, entitled=%s",
            websocket.client, device, is_entitled
        )
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Policy Violation: Authentication or ANPR license required",
        )
        return

    await websocket.accept()
    try:
        raw = await asyncio.wait_for(websocket.receive_json(), _HELLO_TIMEOUT_S)
        hello = EdgeHello.model_validate(raw)
    except (asyncio.TimeoutError, ValidationError, ValueError, KeyError, WebSocketDisconnect) as exc:
        logger.warning("Edge handshake failed: %s", exc)
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="bad hello")
        return

    conn = EdgeConnection(websocket=websocket, node_id=hello.node_id, gates=hello.gates)
    await hub.register(conn)
    try:
        await conn.send_json({"event": "HELLO_ACK", "conn_id": conn.conn_id})
        while True:
            message = await websocket.receive_text()
            await _handle_message(conn, hub, settings, message)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Edge %s connection crashed", conn.node_id)
        await conn.close(status.WS_1011_INTERNAL_ERROR, "server error")
    finally:
        await hub.unregister(conn)


async def _handle_message(conn: EdgeConnection, hub: EdgeHub, settings, message: str) -> None:
    try:
        data = json.loads(message)
        event = data.get("event")
    except (ValueError, AttributeError):
        logger.warning("Edge %s sent non-JSON frame", conn.node_id)
        return

    if event == "PING":
        await conn.send_json({"event": "PONG"})
        return
    if event != "CAPTURE_RESULT":
        logger.warning("Edge %s sent unknown event %r", conn.node_id, event)
        return

    try:
        result = CaptureResult.model_validate(data)
    except ValidationError as exc:
        logger.warning("Edge %s sent invalid result: %s", conn.node_id, exc.errors()[:3])
        await conn.send_json({
            "event": "RESULT_ACK", "session_id": data.get("session_id"),
            "accepted": False, "reason": "invalid payload",
        })
        return

    accepted, reason = True, None
    try:
        async with AsyncSessionLocal() as db:
            session = await PlateCaptureService(db, settings).record_result(result, conn.gates)
        logger.info(
            "Plate result session=%s status=%s plate=%r timings=%s",
            session.id, session.plate_capture_status, session.plate_number, result.timings_ms,
        )
    except CaptureRejected as exc:
        accepted, reason = False, str(exc)
        logger.info("Edge result for session %s rejected: %s", result.session_id, exc)
    except Exception:
        accepted, reason = False, "server error"
        logger.exception("Failed to store plate result for session %s", result.session_id)

    if accepted:
        try:
            await hub.publish_result(result.session_id)
        except Exception as exc:
            # Waiters still pick it up through their periodic DB re-check.
            logger.warning("Result notify failed for session %s: %s", result.session_id, exc)

    await conn.send_json({
        "event": "RESULT_ACK", "session_id": result.session_id,
        "accepted": accepted, "reason": reason,
    })


__all__ = ["router"]
