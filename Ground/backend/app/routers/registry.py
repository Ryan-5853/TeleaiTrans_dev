"""Ground-authoritative aircraft registry, claims, and v2 identity redeem."""

import secrets
import time
import uuid
from typing import Any

from fastapi import APIRouter, Request

from ..errors import ApiError
from ..hub import Hub
from ..schemas import AircraftRegisterRequest, ClaimCreateRequest, ClaimRedeemRequest

router = APIRouter(prefix="/api/v2", tags=["registry"])

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _hub(request: Request) -> Hub:
    return request.app.state.hub


def _public_base(request: Request) -> str:
    settings = request.app.state.settings
    configured = settings.public_base.rstrip("/")
    if "127.0.0.1" not in configured and "localhost" not in configured:
        return configured
    return str(request.base_url).rstrip("/")


@router.post("/aircraft", status_code=201)
async def register_aircraft(
    body: AircraftRegisterRequest, request: Request
) -> dict[str, Any]:
    hub = _hub(request)
    now = time.time()
    await hub.db.execute(
        """
        INSERT INTO aircraft
          (id, name, model, alias, static_ip, claim_state, registered_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'unclaimed', ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          name = excluded.name,
          model = excluded.model,
          alias = excluded.alias,
          static_ip = excluded.static_ip,
          claim_state = CASE WHEN aircraft.claim_state = 'claimed'
                             THEN aircraft.claim_state ELSE 'unclaimed' END,
          updated_at = excluded.updated_at
        """,
        (
            body.aircraft_id,
            body.name or body.aircraft_id,
            body.model,
            body.aircraft_id,
            body.static_ip or None,
            now,
            now,
        ),
    )
    await hub.record_event(
        body.aircraft_id,
        "info",
        "aircraft.registered",
        f"飞机 {body.aircraft_id} 已登记（static_ip={body.static_ip or '未设置'}）",
    )
    item = await hub.aircraft_view(body.aircraft_id)
    return {"aircraft": item}


@router.post("/claims", status_code=201)
async def create_claim(body: ClaimCreateRequest, request: Request) -> dict[str, Any]:
    hub = _hub(request)
    settings = request.app.state.settings
    aircraft = await hub.db.query_one(
        "SELECT * FROM aircraft WHERE id = ?", (body.aircraft_id,)
    )
    if aircraft is None:
        raise ApiError(
            404,
            "aircraft_not_registered",
            f"飞机 {body.aircraft_id} 未登记，请先 POST /api/v2/aircraft",
        )
    claim_id = uuid.uuid4().hex[:20]
    now = time.time()
    ttl_h = body.ttl_h if body.ttl_h is not None else settings.claim_ttl_h
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(settings.claim_code_length))
    await hub.db.execute(
        "INSERT INTO claims "
        "(claim_id, aircraft_id, code, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
        (claim_id, body.aircraft_id, code, now, now + max(0.1, ttl_h) * 3600.0),
    )
    await hub.db.execute(
        "UPDATE aircraft SET claim_state = 'pending', updated_at = ? WHERE id = ?",
        (now, body.aircraft_id),
    )
    await hub.record_event(
        body.aircraft_id,
        "info",
        "claim.created",
        "已生成 claim 码",
        {"claim_id": claim_id},
    )
    return {
        "claim_id": claim_id,
        "aircraft_id": body.aircraft_id,
        "code": code,
        "expires_at": now + max(0.1, ttl_h) * 3600.0,
    }


@router.post("/claims/redeem")
async def redeem_claim(body: ClaimRedeemRequest, request: Request) -> dict[str, Any]:
    hub = _hub(request)
    claim = await hub.db.query_one(
        "SELECT * FROM claims WHERE code = ? AND redeemed_at IS NULL ORDER BY created_at DESC LIMIT 1",
        (body.code.upper(),),
    )
    if claim is None:
        raise ApiError(404, "claim_not_found", "claim 码不存在或已被使用")
    now = time.time()
    if claim["expires_at"] and now > claim["expires_at"]:
        raise ApiError(410, "claim_expired", "claim 码已过期")
    aircraft = await hub.db.query_one(
        "SELECT * FROM aircraft WHERE id = ?", (claim["aircraft_id"],)
    )
    if aircraft is None:
        raise ApiError(404, "aircraft_not_found", "claim 对应飞机不存在")
    await hub.db.execute(
        "UPDATE claims SET redeemed_at = ?, redeemed_ip = ? WHERE claim_id = ?",
        (now, request.client.host if request.client else None, claim["claim_id"]),
    )
    await hub.db.execute(
        "UPDATE aircraft SET claim_state = 'claimed', updated_at = ? WHERE id = ?",
        (now, claim["aircraft_id"]),
    )
    base = _public_base(request)
    await hub.record_event(
        claim["aircraft_id"],
        "info",
        "claim.redeemed",
        "载板已通过 claim 码领取身份",
    )
    return {
        "identity": {
            "aircraft_id": aircraft["id"],
            "name": aircraft["name"] or aircraft["id"],
            "model": aircraft["model"],
            "static_ip": aircraft["static_ip"],
        },
        "ground": {
            "url": base,
            "plane_ws": f"{base.replace('http://', 'ws://').replace('https://', 'wss://')}"
            f"/plane/v2/ws?aircraft_id={aircraft['id']}",
        },
    }


@router.get("/claims")
async def list_claims(request: Request) -> dict[str, Any]:
    hub = _hub(request)
    rows = await hub.db.query_all(
        "SELECT claim_id, aircraft_id, code, created_at, expires_at, "
        "redeemed_at, redeemed_ip FROM claims ORDER BY created_at DESC LIMIT 500"
    )
    return {"claims": rows}
