import threading
import time
from collections import deque
from typing import Callable

from fastapi import APIRouter, HTTPException, Query
from fastapi import Depends, Request

from src.repositories.booking_repository import BookingRepository
from src.repositories.scheduling_repository import SchedulingRepository


def _build_guard(
    rate_limit_per_minute: int,
) -> Callable[[Request], None]:
    lock = threading.Lock()
    buckets: dict[str, deque[float]] = {}
    window_seconds = 60.0
    max_requests = max(1, rate_limit_per_minute)

    def guard(request: Request) -> None:
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
        return None

    return guard


def create_admin_router(
    booking_repository: BookingRepository | None,
    scheduling_repository: SchedulingRepository | None,
    auth_repository=None,
    admin_api_key: str = "",
    rate_limit_per_minute: int = 60,
    token_ttl_minutes: int = 480,
) -> APIRouter:
    guard = _build_guard(
        rate_limit_per_minute=rate_limit_per_minute,
    )
    router = APIRouter(prefix="/api", tags=["admin"])

    @router.get("/clinics")
    def list_clinics(
        doctor_id: int = Query(...),
        admin_id: int | None = Query(default=None),
        limit: int = Query(default=10, ge=1, le=50),
        _=Depends(guard),
    ):
        if not scheduling_repository:
            raise HTTPException(status_code=503, detail="Scheduling repository not configured.")
        clinics = scheduling_repository.list_clinics_for_doctor(
            doctor_id=doctor_id,
            admin_id=admin_id,
            limit=limit,
        )
        return {
            "doctor_id": doctor_id,
            "admin_id": admin_id,
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
        _=Depends(guard),
    ):
        if not scheduling_repository:
            raise HTTPException(status_code=503, detail="Scheduling repository not configured.")
        dates = scheduling_repository.list_available_dates(
            doctor_id=doctor_id,
            clinic_id=clinic_id,
            admin_id=admin_id,
            limit=limit,
        )
        return {"doctor_id": doctor_id, "clinic_id": clinic_id, "admin_id": admin_id, "dates": dates}

    @router.get("/availability/times")
    def list_times(
        doctor_id: int = Query(...),
        clinic_id: int = Query(...),
        slot_date: str = Query(...),
        admin_id: int | None = Query(default=None),
        limit: int = Query(default=3, ge=1, le=50),
        _=Depends(guard),
    ):
        if not scheduling_repository:
            raise HTTPException(status_code=503, detail="Scheduling repository not configured.")
        times = scheduling_repository.list_available_times(
            doctor_id=doctor_id,
            clinic_id=clinic_id,
            slot_date=slot_date,
            admin_id=admin_id,
            limit=limit,
        )
        return {
            "doctor_id": doctor_id,
            "clinic_id": clinic_id,
            "slot_date": slot_date,
            "admin_id": admin_id,
            "times": times,
        }

    @router.post("/schedules/{schedule_id}/generate-slots")
    def generate_slots(
        schedule_id: int,
        days_ahead: int = Query(default=30, ge=1, le=365),
        _=Depends(guard),
    ):
        if not scheduling_repository:
            raise HTTPException(status_code=503, detail="Scheduling repository not configured.")
        scheduling_repository.generate_slots_for_schedule(schedule_id=schedule_id, days_ahead=days_ahead)
        return {"ok": True, "schedule_id": schedule_id, "days_ahead": days_ahead}

    @router.get("/appointments/{appointment_id}")
    def appointment_status(appointment_id: int, _=Depends(guard)):
        if not booking_repository:
            raise HTTPException(status_code=503, detail="Booking repository not configured.")
        row = booking_repository.get_appointment_status(appointment_id)
        if not row:
            raise HTTPException(status_code=404, detail="Appointment not found.")
        return row

    return router
