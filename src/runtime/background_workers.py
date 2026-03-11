from typing import Any, Callable

from src.runtime import TurnTask


def run_overflow_turn_poll_loop(
    *,
    conversation_repository: Any,
    turn_processor: Any,
    overflow_poll_stop: Any,
    settings: Any,
    overflow_worker_id: str,
    logger: Any,
    sid_retention_days: int,
    sid_purge_interval_seconds: int,
    track_overflow_task: Callable[[TurnTask, int], None],
) -> None:
    if not conversation_repository:
        return
    claim_size = max(1, settings.queue_worker_count)
    sid_retention_days = max(1, int(sid_retention_days))
    sid_purge_interval_seconds = max(60, int(sid_purge_interval_seconds))
    next_sid_purge_at = 0.0
    while not overflow_poll_stop.is_set():
        try:
            now_monotonic = __import__("time").monotonic()
            if now_monotonic >= next_sid_purge_at:
                try:
                    deleted = conversation_repository.purge_old_message_sids(retention_days=sid_retention_days)
                    if deleted:
                        logger.info(
                            "Purged old inbound_message_sids rows=%d retention_days=%d",
                            deleted,
                            sid_retention_days,
                        )
                except Exception:
                    logger.exception("Failed to purge inbound_message_sids")
                next_sid_purge_at = now_monotonic + sid_purge_interval_seconds
            rows = conversation_repository.claim_overflow_turns(limit=claim_size, worker_id=overflow_worker_id)
            if not rows:
                overflow_poll_stop.wait(0.8)
                continue
            for row in rows:
                task = TurnTask(
                    from_number=row.from_number,
                    body=row.body,
                    inbound_sid=row.inbound_sid,
                    pre_state=row.pre_state,
                    attempt=max(0, int(row.attempt_count)),
                )
                submit_overflow = getattr(turn_processor, "submit_overflow", None)
                accepted = submit_overflow(task) if callable(submit_overflow) else turn_processor.submit(task)
                if accepted:
                    track_overflow_task(task, row.queue_id)
                    continue
                conversation_repository.release_overflow_turn(
                    queue_id=row.queue_id,
                    reason="Runtime queue still full",
                    backoff_seconds=max(1, int(settings.queue_overflow_requeue_backoff_seconds)),
                )
        except Exception as exc:
            logger.warning("Overflow queue poll failed error=%s", exc)
            overflow_poll_stop.wait(1.0)


def run_doctor_cache_invalidation_loop(
    *,
    scheduling_repository: Any,
    cache_inv_stop: Any,
    settings: Any,
    cache_inv_worker_id: str,
    logger: Any,
) -> None:
    if not scheduling_repository:
        return
    claim_size = max(1, min(100, settings.queue_worker_count * 10))
    while not cache_inv_stop.is_set():
        try:
            events = scheduling_repository.claim_cache_invalidation_events(
                limit=claim_size,
                worker_id=cache_inv_worker_id,
            )
            if not events:
                cache_inv_stop.wait(0.8)
                continue
            for event in events:
                try:
                    scheduling_repository.process_cache_invalidation_event(event)
                    scheduling_repository.mark_cache_invalidation_done(event.queue_id)
                except Exception:
                    logger.exception(
                        "Doctor cache invalidation failed queue_id=%s entity=%s doctor=%s clinic=%s",
                        event.queue_id,
                        event.entity_type,
                        event.doctor_id,
                        event.clinic_id,
                    )
                    scheduling_repository.release_cache_invalidation(event.queue_id)
        except Exception as exc:
            logger.warning("Doctor cache invalidation poll failed error=%s", exc)
            cache_inv_stop.wait(1.0)
