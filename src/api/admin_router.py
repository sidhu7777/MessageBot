import threading
import time
from collections import deque
from datetime import datetime
from typing import Callable

from fastapi import APIRouter, HTTPException, Query
from fastapi import Depends, Request
from pydantic import BaseModel

from src.repositories.auth_repository import AuthPrincipal, AuthRepository
from src.repositories.booking_repository import BookingRepository
from src.repositories.scheduling_repository import SchedulingRepository


class AdminLoginRequest(BaseModel):
    email: str
    password: str


def _bearer_token_from_request(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "").strip()
    if not auth_header.lower().startswith("bearer "):
        return ""
    return auth_header[7:].strip()


def _build_guard(
    admin_api_key: str,
    rate_limit_per_minute: int,
    auth_repository: AuthRepository | None,
) -> Callable[[Request], AuthPrincipal]:
    lock = threading.Lock()
    buckets: dict[str, deque[float]] = {}
    window_seconds = 60.0
    max_requests = max(1, rate_limit_per_minute)

    def guard(request: Request) -> AuthPrincipal:
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        with lock:
            queue = buckets.get(client_ip)
            if queue is None:
                queue = deque()
                buckets[client_ip] = queue
            while queue and now - queue[0] > window_seconds:
                queue.popleft()
            if len(queue) >= max_requests:
                raise HTTPException(status_code=429, detail="Admin API rate limit exceeded.")
            queue.append(now)

        token = _bearer_token_from_request(request)
        if auth_repository is None:
            raise HTTPException(status_code=503, detail="Auth repository not configured.")
        if not token:
            raise HTTPException(status_code=401, detail="Bearer token required.")
        principal = auth_repository.validate_token(token)
        if principal:
            return principal
        raise HTTPException(status_code=401, detail="Invalid or expired bearer token.")

    return guard


def create_admin_router(
    booking_repository: BookingRepository | None,
    scheduling_repository: SchedulingRepository | None,
    auth_repository: AuthRepository | None = None,
    admin_api_key: str = "",
    rate_limit_per_minute: int = 60,
    token_ttl_minutes: int = 480,
) -> APIRouter:
    guard = _build_guard(
        admin_api_key=admin_api_key,
        rate_limit_per_minute=rate_limit_per_minute,
        auth_repository=auth_repository,
    )
    router = APIRouter(prefix="/api", tags=["admin"])

    @router.post("/auth/login")
    def admin_login(payload: AdminLoginRequest):
        if auth_repository is None:
            raise HTTPException(status_code=503, detail="Auth repository not configured.")
        principal = auth_repository.login_admin(
            email=payload.email,
            password=payload.password,
            ttl_minutes=token_ttl_minutes,
        )
        if not principal:
            raise HTTPException(status_code=401, detail="Invalid admin credentials.")
        return {
            "access_token": principal.token,
            "token_type": "bearer",
            "expires_at": principal.expires_at.isoformat(),
            "user_id": principal.user_id,
            "role": principal.role,
            "admin_id": principal.admin_id,
        }

    @router.post("/auth/logout")
    def admin_logout(request: Request):
        if auth_repository is None:
            raise HTTPException(status_code=503, detail="Auth repository not configured.")
        token = _bearer_token_from_request(request)
        if not token:
            raise HTTPException(status_code=401, detail="Bearer token required.")
        revoked = auth_repository.revoke_token(token)
        return {"ok": bool(revoked)}

    @router.get("/clinics")
    def list_clinics(
        doctor_id: int = Query(...),
        admin_id: int | None = Query(default=None),
        limit: int = Query(default=10, ge=1, le=50),
        principal: AuthPrincipal = Depends(guard),
    ):
        if not scheduling_repository:
            raise HTTPException(status_code=503, detail="Scheduling repository not configured.")
        effective_admin_id = principal.admin_id or admin_id
        if principal.admin_id is not None and admin_id is not None and admin_id != principal.admin_id:
            raise HTTPException(status_code=403, detail="admin_id mismatch for authenticated admin.")
        clinics = scheduling_repository.list_clinics_for_doctor(
            doctor_id=doctor_id,
            admin_id=effective_admin_id,
            limit=limit,
        )
        return {
            "doctor_id": doctor_id,
            "admin_id": effective_admin_id,
            "count": len(clinics),
            "items": [
                {
                    "clinic_id": c.clinic_id,
                    "clinic_name": c.clinic_name,
                    "location": c.location,
                    "today_slots": c.today_slots,
                }
                for c in clinics
            ],
        }

    @router.get("/availability/dates")
    def list_dates(
        doctor_id: int = Query(...),
        clinic_id: int = Query(...),
        admin_id: int | None = Query(default=None),
        limit: int = Query(default=3, ge=1, le=31),
        principal: AuthPrincipal = Depends(guard),
    ):
        if not scheduling_repository:
            raise HTTPException(status_code=503, detail="Scheduling repository not configured.")
        effective_admin_id = principal.admin_id or admin_id
        if principal.admin_id is not None and admin_id is not None and admin_id != principal.admin_id:
            raise HTTPException(status_code=403, detail="admin_id mismatch for authenticated admin.")
        dates = scheduling_repository.list_available_dates(
            doctor_id=doctor_id,
            clinic_id=clinic_id,
            admin_id=effective_admin_id,
            limit=limit,
        )
        return {"doctor_id": doctor_id, "clinic_id": clinic_id, "admin_id": effective_admin_id, "dates": dates}

    @router.get("/availability/times")
    def list_times(
        doctor_id: int = Query(...),
        clinic_id: int = Query(...),
        slot_date: str = Query(...),
        admin_id: int | None = Query(default=None),
        limit: int = Query(default=3, ge=1, le=50),
        principal: AuthPrincipal = Depends(guard),
    ):
        if not scheduling_repository:
            raise HTTPException(status_code=503, detail="Scheduling repository not configured.")
        effective_admin_id = principal.admin_id or admin_id
        if principal.admin_id is not None and admin_id is not None and admin_id != principal.admin_id:
            raise HTTPException(status_code=403, detail="admin_id mismatch for authenticated admin.")
        times = scheduling_repository.list_available_times(
            doctor_id=doctor_id,
            clinic_id=clinic_id,
            slot_date=slot_date,
            admin_id=effective_admin_id,
            limit=limit,
        )
        return {
            "doctor_id": doctor_id,
            "clinic_id": clinic_id,
            "slot_date": slot_date,
            "admin_id": effective_admin_id,
            "times": times,
        }

    @router.post("/schedules/{schedule_id}/generate-slots")
    def generate_slots(
        schedule_id: int,
        days_ahead: int = Query(default=30, ge=1, le=365),
        _principal: AuthPrincipal = Depends(guard),
    ):
        if not scheduling_repository:
            raise HTTPException(status_code=503, detail="Scheduling repository not configured.")
        scheduling_repository.generate_slots_for_schedule(schedule_id=schedule_id, days_ahead=days_ahead)
        return {"ok": True, "schedule_id": schedule_id, "days_ahead": days_ahead}

    @router.get("/appointments/{appointment_id}")
    def appointment_status(appointment_id: int, _principal: AuthPrincipal = Depends(guard)):
        if not booking_repository:
            raise HTTPException(status_code=503, detail="Booking repository not configured.")
        row = booking_repository.get_appointment_status(appointment_id)
        if not row:
            raise HTTPException(status_code=404, detail="Appointment not found.")
        return row

    return router
