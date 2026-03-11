import json
import hashlib
import hmac
import time
from typing import Any, Callable
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from fastapi.responses import PlainTextResponse

from src.chat_logger import log_event
from src.runtime import TurnTask


def _resolve(dep: Any) -> Any:
    return dep() if callable(dep) else dep


def _extract_meta_whatsapp_body(message: dict) -> str:
    if not isinstance(message, dict):
        return ""
    message_type = str(message.get("type") or "").strip().lower()
    if message_type == "text":
        text_obj = message.get("text") or {}
        return str(text_obj.get("body") or "").strip()
    if message_type == "button":
        button_obj = message.get("button") or {}
        return str(button_obj.get("text") or button_obj.get("payload") or "").strip()
    if message_type == "interactive":
        interactive = message.get("interactive") or {}
        interactive_type = str(interactive.get("type") or "").strip().lower()
        if interactive_type == "button_reply":
            reply = interactive.get("button_reply") or {}
            return str(reply.get("title") or reply.get("id") or "").strip()
        if interactive_type == "list_reply":
            reply = interactive.get("list_reply") or {}
            return str(reply.get("title") or reply.get("id") or "").strip()
    return ""


def _normalize_whatsapp_number(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    digits = "".join(ch for ch in value if ch.isdigit())
    if digits:
        return f"whatsapp:+{digits}"
    lowered = value.lower()
    if lowered.startswith("whatsapp:"):
        return value
    return f"whatsapp:{value}"


def _extract_infobip_text(result: dict) -> str:
    if not isinstance(result, dict):
        return ""
    message = result.get("message") or {}
    if isinstance(message, dict):
        text = str(message.get("text") or message.get("body") or "").strip()
        if text:
            return text
    content = result.get("content") or {}
    if isinstance(content, dict):
        text = str(content.get("text") or content.get("body") or "").strip()
        if text:
            return text
    return str(result.get("text") or result.get("body") or "").strip()


def _route_path(url_or_path: str, default_path: str) -> str:
    raw = str(url_or_path or "").strip()
    if not raw:
        return default_path
    parsed = urlparse(raw)
    candidate = parsed.path if parsed.scheme and parsed.netloc else raw
    candidate = candidate.strip()
    if not candidate:
        return default_path
    if not candidate.startswith("/"):
        candidate = f"/{candidate}"
    if len(candidate) > 1:
        candidate = candidate.rstrip("/")
    return candidate


def register_webhook_routes(
    app: Any,
    *,
    settings: Any,
    logger: Any,
    request_validator: Any,
    sid_store: Any,
    session_manager: Any,
    twilio_client: Any,
    turn_processor: Any,
    booking_repository: Any,
    user_processing_guard: Any,
    user_turn_buffer: Any,
    set_user_bot_identity: Callable[[str, str], None],
    submit_next_buffered_turn: Callable[[str], None],
    get_telegram_bot_username: Callable[[], str],
) -> None:
    twilio_inbound_path = _route_path(getattr(settings, "twilio_webhook_url", ""), "/webhook")
    telegram_inbound_path = _route_path(getattr(settings, "telegram_webhook_url", ""), "/telegram/webhook")
    whatsapp_webhook_path = _route_path(getattr(settings, "whatsapp_webhook_url", ""), "/whatsapp/webhook")
    infobip_webhook_path = _route_path(getattr(settings, "infobip_webhook_url", ""), "/infobip/webhook")

    def _queue_whatsapp_turn(
        *,
        from_number: str,
        body: str,
        inbound_sid: str,
        to_number: str,
    ) -> PlainTextResponse:
        sid_store_local = _resolve(sid_store)
        session_manager_local = _resolve(session_manager)
        twilio_client_local = _resolve(twilio_client)
        turn_processor_local = _resolve(turn_processor)
        user_processing_guard_local = _resolve(user_processing_guard)
        user_turn_buffer_local = _resolve(user_turn_buffer)
        started = time.perf_counter()
        if inbound_sid:
            duplicate = sid_store_local.seen_or_add(inbound_sid)
            if duplicate:
                logger.info("Duplicate inbound MessageSid ignored sid=%s from=%s", inbound_sid, from_number)
                return PlainTextResponse("", status_code=200)
        set_user_bot_identity(from_number, to_number)
        try:
            pre_state = session_manager_local.get_or_create(from_number).state
        except Exception:
            pre_state = "INIT"

        if settings.twilio_use_rest_responses and (
            twilio_client_local
            or (
                (getattr(settings, "whatsapp_provider", "") or "").strip().lower() in {"meta", "auto"}
                and (getattr(settings, "whatsapp_api_token", "") or "").strip()
                and (getattr(settings, "whatsapp_phone_number_id", "") or "").strip()
            )
            or (
                (getattr(settings, "whatsapp_provider", "") or "").strip().lower() in {"infobip", "auto"}
                and (getattr(settings, "infobip_api_key", "") or "").strip()
                and (getattr(settings, "infobip_base_url", "") or "").strip()
                and (getattr(settings, "infobip_whatsapp_number", "") or "").strip()
            )
        ):
            acquired = False
            try:
                acquired = user_processing_guard_local.acquire(from_number)
                if not acquired:
                    buffered = user_turn_buffer_local.push(
                        TurnTask(
                            from_number=from_number,
                            body=body,
                            inbound_sid=inbound_sid,
                            pre_state=pre_state,
                        )
                    )
                    logger.info(
                        "Buffered inbound while processing sid=%s from=%s pending=%d collapsed=%s dropped_oldest=%s",
                        inbound_sid or "-",
                        from_number,
                        buffered.pending_count,
                        buffered.collapsed,
                        buffered.dropped_oldest,
                    )
                    return PlainTextResponse("", status_code=200)

                task = TurnTask(
                    from_number=from_number,
                    body=body,
                    inbound_sid=inbound_sid,
                    pre_state=pre_state,
                )
                user_turn_buffer_local.record_dispatch(from_number, body)
                enqueued = turn_processor_local.submit(task)
                if not enqueued:
                    user_processing_guard_local.release(from_number)
                    buffered = user_turn_buffer_local.push(task)
                    logger.warning(
                        "Queue full. Buffered inbound sid=%s from=%s backlog=%d pending=%d collapsed=%s dropped_oldest=%s",
                        inbound_sid or "-",
                        from_number,
                        turn_processor_local.backlog_size(),
                        buffered.pending_count,
                        buffered.collapsed,
                        buffered.dropped_oldest,
                    )
                    return PlainTextResponse("", status_code=200)

                elapsed_ms = int((time.perf_counter() - started) * 1000)
                logger.info(
                    "Queued inbound sid=%s from=%s state=%s backlog=%d ack_ms=%d",
                    inbound_sid or "-",
                    from_number,
                    pre_state,
                    turn_processor_local.backlog_size(),
                    elapsed_ms,
                )
                return PlainTextResponse("", status_code=200)
            except Exception as exc:
                if acquired:
                    user_processing_guard_local.release(from_number)
                buffered = user_turn_buffer_local.push(
                    TurnTask(
                        from_number=from_number,
                        body=body,
                        inbound_sid=inbound_sid,
                        pre_state=pre_state,
                    )
                )
                logger.warning(
                    "ACK-first fallback buffered sid=%s from=%s pending=%d error=%s",
                    inbound_sid or "-",
                    from_number,
                    buffered.pending_count,
                    exc,
                )
                return PlainTextResponse("", status_code=200)

        buffered = user_turn_buffer_local.push(
            TurnTask(
                from_number=from_number,
                body=body,
                inbound_sid=inbound_sid,
                pre_state=pre_state,
            )
        )
        logger.info(
            "ACK-first buffered inbound sid=%s from=%s pending=%d collapsed=%s dropped_oldest=%s",
            inbound_sid or "-",
            from_number,
            buffered.pending_count,
            buffered.collapsed,
            buffered.dropped_oldest,
        )
        submit_next_buffered_turn(from_number)
        return PlainTextResponse("", status_code=200)

    @app.post(twilio_inbound_path)
    async def webhook(request: Request):
        request_validator_local = _resolve(request_validator)
        form = await request.form()
        inbound_sid = (form.get("MessageSid") or form.get("SmsMessageSid") or "").strip()
        button_payload = (form.get("ButtonPayload") or "").strip()
        button_text = (form.get("ButtonText") or "").strip()
        body = (
            button_payload
            or button_text
            or (form.get("Body") or "").strip()
        )
        from_number = form.get("From") or "unknown"
        to_number = (form.get("To") or settings.twilio_whatsapp_from or "").strip()

        if settings.enable_twilio_signature_validation:
            signature = request.headers.get("X-Twilio-Signature", "")
            valid = request_validator_local.validate(str(request.url), dict(form), signature)
            if not valid:
                logger.warning("Rejected webhook due to invalid Twilio signature")
                raise HTTPException(status_code=403, detail="Invalid Twilio signature")

        logger.info(
            "Incoming WhatsApp message sid=%s from=%s body=%s button_payload=%s button_text=%s",
            inbound_sid or "-",
            from_number,
            body,
            button_payload or "-",
            button_text or "-",
        )
        return _queue_whatsapp_turn(
            from_number=from_number,
            body=body,
            inbound_sid=inbound_sid,
            to_number=to_number,
        )

    @app.get(whatsapp_webhook_path)
    async def whatsapp_webhook_verify(request: Request):
        mode = str(request.query_params.get("hub.mode") or "").strip()
        challenge = str(request.query_params.get("hub.challenge") or "").strip()
        verify_token = str(request.query_params.get("hub.verify_token") or "").strip()
        expected_verify_token = (getattr(settings, "whatsapp_webhook_verify_token", "") or "").strip()
        if mode == "subscribe" and expected_verify_token and verify_token == expected_verify_token:
            return PlainTextResponse(challenge, status_code=200)
        raise HTTPException(status_code=403, detail="Invalid webhook verify token")

    @app.post(whatsapp_webhook_path)
    async def whatsapp_webhook(request: Request):
        booking_repository_local = _resolve(booking_repository)
        raw_body = await request.body()
        if settings.enable_meta_signature_validation and settings.meta_app_secret:
            header_signature = str(request.headers.get("X-Hub-Signature-256") or "").strip()
            expected_hash = hmac.new(
                settings.meta_app_secret.encode("utf-8"),
                raw_body,
                hashlib.sha256,
            ).hexdigest()
            expected_signature = f"sha256={expected_hash}"
            if not hmac.compare_digest(header_signature, expected_signature):
                logger.warning("Rejected Meta webhook due to invalid signature")
                raise HTTPException(status_code=403, detail="Invalid Meta signature")

        payload = await request.json()
        entries = payload.get("entry") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            return PlainTextResponse("", status_code=200)

        for entry in entries:
            changes = (entry or {}).get("changes")
            if not isinstance(changes, list):
                continue
            for change in changes:
                value = (change or {}).get("value") or {}
                metadata = value.get("metadata") or {}
                display_phone = str(metadata.get("display_phone_number") or "").strip()
                phone_number_id = str(metadata.get("phone_number_id") or "").strip()
                bot_identity = display_phone or phone_number_id

                messages = value.get("messages") or []
                if isinstance(messages, list):
                    for message in messages:
                        if not isinstance(message, dict):
                            continue
                        inbound_sid = str(message.get("id") or "").strip()
                        wa_from = str(message.get("from") or "").strip()
                        text_body = _extract_meta_whatsapp_body(message)
                        from_number = f"whatsapp:+{wa_from}" if wa_from else "unknown"
                        to_number = f"whatsapp:+{bot_identity}" if bot_identity and bot_identity.isdigit() else (bot_identity or "")
                        logger.info(
                            "Incoming Meta WhatsApp message sid=%s from=%s body=%s",
                            inbound_sid or "-",
                            from_number,
                            text_body,
                        )
                        response = _queue_whatsapp_turn(
                            from_number=from_number,
                            body=text_body,
                            inbound_sid=inbound_sid,
                            to_number=to_number,
                        )
                        if response.status_code != 200:
                            return response

                statuses = value.get("statuses") or []
                if isinstance(statuses, list) and booking_repository_local:
                    for status in statuses:
                        if not isinstance(status, dict):
                            continue
                        msg_sid = str(status.get("id") or "-")
                        msg_status = str(status.get("status") or "-")
                        recipient_id = str(status.get("recipient_id") or "-")
                        errors = status.get("errors") or []
                        err_code = "-"
                        err_msg = "-"
                        if isinstance(errors, list) and errors:
                            first_error = errors[0] or {}
                            err_code = str(first_error.get("code") or "-")
                            err_msg = str(first_error.get("title") or first_error.get("message") or "-")
                        logger.info(
                            "Meta status sid=%s status=%s to=%s error_code=%s error_message=%s",
                            msg_sid,
                            msg_status,
                            recipient_id,
                            err_code,
                            err_msg,
                        )
                        try:
                            booking_repository_local.upsert_delivery_status(
                                provider="meta",
                                provider_message_sid=msg_sid,
                                channel="whatsapp",
                                message_status=msg_status,
                                to_number=f"whatsapp:+{recipient_id}" if recipient_id and recipient_id.isdigit() else recipient_id,
                                from_number=(display_phone or phone_number_id or "-"),
                                error_code=err_code,
                                error_message=err_msg,
                                payload_json=json.dumps(status, ensure_ascii=False),
                            )
                        except Exception as exc:
                            logger.warning("Failed to persist Meta callback sid=%s error=%s", msg_sid, exc)
        return PlainTextResponse("", status_code=200)

    @app.post(infobip_webhook_path)
    async def infobip_webhook(request: Request):
        booking_repository_local = _resolve(booking_repository)
        payload = await request.json()
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            return PlainTextResponse("", status_code=200)

        for result in results:
            if not isinstance(result, dict):
                continue
            inbound_sid = str(
                result.get("messageId")
                or result.get("id")
                or result.get("pairedMessageId")
                or ""
            ).strip()
            from_number = _normalize_whatsapp_number(str(result.get("from") or ""))
            to_number = _normalize_whatsapp_number(str(result.get("to") or ""))
            text_body = _extract_infobip_text(result)

            if text_body:
                logger.info(
                    "Incoming Infobip WhatsApp message sid=%s from=%s body=%s",
                    inbound_sid or "-",
                    from_number or "unknown",
                    text_body,
                )
                response = _queue_whatsapp_turn(
                    from_number=from_number or "unknown",
                    body=text_body,
                    inbound_sid=inbound_sid,
                    to_number=to_number,
                )
                if response.status_code != 200:
                    return response
                continue

            if booking_repository_local and inbound_sid:
                status_obj = result.get("status") or {}
                status_name = str(
                    (status_obj.get("name") if isinstance(status_obj, dict) else "")
                    or (status_obj.get("groupName") if isinstance(status_obj, dict) else "")
                    or "unknown"
                ).strip()
                error_obj = result.get("error") or {}
                error_code = str(
                    (error_obj.get("id") if isinstance(error_obj, dict) else "")
                    or (status_obj.get("id") if isinstance(status_obj, dict) else "")
                    or "-"
                ).strip()
                error_message = str(
                    (error_obj.get("description") if isinstance(error_obj, dict) else "")
                    or (status_obj.get("description") if isinstance(status_obj, dict) else "")
                    or "-"
                ).strip()
                logger.info(
                    "Infobip status sid=%s status=%s to=%s error_code=%s error_message=%s",
                    inbound_sid,
                    status_name or "-",
                    (to_number or "-"),
                    error_code or "-",
                    error_message or "-",
                )
                try:
                    booking_repository_local.upsert_delivery_status(
                        provider="infobip",
                        provider_message_sid=inbound_sid,
                        channel="whatsapp",
                        message_status=status_name or "unknown",
                        to_number=to_number or "-",
                        from_number=from_number or "-",
                        error_code=error_code or "-",
                        error_message=error_message or "-",
                        payload_json=json.dumps(result, ensure_ascii=False),
                    )
                except Exception as exc:
                    logger.warning("Failed to persist Infobip callback sid=%s error=%s", inbound_sid, exc)
        return PlainTextResponse("", status_code=200)

    @app.post(telegram_inbound_path)
    async def telegram_webhook(request: Request):
        sid_store_local = _resolve(sid_store)
        session_manager_local = _resolve(session_manager)
        turn_processor_local = _resolve(turn_processor)
        user_processing_guard_local = _resolve(user_processing_guard)
        user_turn_buffer_local = _resolve(user_turn_buffer)
        if settings.telegram_webhook_secret:
            secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if secret != settings.telegram_webhook_secret:
                logger.warning("Rejected Telegram webhook due to invalid secret token")
                raise HTTPException(status_code=403, detail="Invalid Telegram webhook secret")

        started = time.perf_counter()
        payload = await request.json()
        message = payload.get("message") or payload.get("edited_message") or {}
        text = str(message.get("text") or "").strip()
        from_user = message.get("from") or {}
        chat = message.get("chat") or {}
        telegram_user_id = str(from_user.get("id") or chat.get("id") or "").strip()
        inbound_sid = str(message.get("message_id") or "").strip()
        if not telegram_user_id:
            return PlainTextResponse("", status_code=200)
        from_number = f"telegram:{telegram_user_id}"
        try:
            log_event(telegram_user_id, "WEBHOOK_ARRIVED", sid=inbound_sid, text=text[:100])
        except Exception:
            pass

        logger.info(
            "Incoming Telegram message sid=%s from=%s body=%s",
            inbound_sid or "-",
            from_number,
            text,
        )

        if inbound_sid:
            dedup_sid = f"TG{telegram_user_id}:{inbound_sid}"
            duplicate = sid_store_local.seen_or_add(dedup_sid)
            if duplicate:
                logger.info("Duplicate inbound Telegram message ignored sid=%s from=%s", dedup_sid, from_number)
                return PlainTextResponse("", status_code=200)

        runtime_username = get_telegram_bot_username()
        bot_identity = f"telegram_username:{runtime_username}" if runtime_username else ""
        set_user_bot_identity(from_number, bot_identity)
        try:
            pre_state = session_manager_local.get_or_create(from_number).state
        except Exception:
            pre_state = "INIT"
        acquired = False
        try:
            acquired = user_processing_guard_local.acquire(from_number)
            try:
                log_event(telegram_user_id, "LOCK_ACQUIRED" if acquired else "LOCK_BUSY")
            except Exception:
                pass
            if not acquired:
                buffered = user_turn_buffer_local.push(
                    TurnTask(
                        from_number=from_number,
                        body=text,
                        inbound_sid=inbound_sid,
                        pre_state=pre_state,
                    )
                )
                logger.info(
                    "Buffered inbound Telegram while processing sid=%s from=%s pending=%d collapsed=%s dropped_oldest=%s",
                    inbound_sid or "-",
                    from_number,
                    buffered.pending_count,
                    buffered.collapsed,
                    buffered.dropped_oldest,
                )
                return PlainTextResponse("", status_code=200)

            task = TurnTask(
                from_number=from_number,
                body=text,
                inbound_sid=inbound_sid,
                pre_state=pre_state,
            )
            user_turn_buffer_local.record_dispatch(from_number, text)
            enqueued = turn_processor_local.submit(task)
            if not enqueued:
                user_processing_guard_local.release(from_number)
                buffered = user_turn_buffer_local.push(task)
                logger.warning(
                    "Queue full. Buffered inbound sid=%s from=%s backlog=%d pending=%d collapsed=%s dropped_oldest=%s",
                    inbound_sid or "-",
                    from_number,
                    turn_processor_local.backlog_size(),
                    buffered.pending_count,
                    buffered.collapsed,
                    buffered.dropped_oldest,
                )
                return PlainTextResponse("", status_code=200)

            elapsed_ms = int((time.perf_counter() - started) * 1000)
            try:
                log_event(
                    telegram_user_id,
                    "TURN_QUEUED",
                    sid=inbound_sid,
                    state=pre_state,
                    backlog=turn_processor_local.backlog_size(),
                    ack_ms=elapsed_ms,
                )
            except Exception:
                pass
            logger.info(
                "Queued inbound Telegram sid=%s from=%s state=%s backlog=%d ack_ms=%d",
                inbound_sid or "-",
                from_number,
                pre_state,
                turn_processor_local.backlog_size(),
                elapsed_ms,
            )
            return PlainTextResponse("", status_code=200)
        except Exception as exc:
            if acquired:
                user_processing_guard_local.release(from_number)
            buffered = user_turn_buffer_local.push(
                TurnTask(
                    from_number=from_number,
                    body=text,
                    inbound_sid=inbound_sid,
                    pre_state=pre_state,
                )
            )
            logger.warning(
                "ACK-first Telegram fallback buffered sid=%s from=%s pending=%d error=%s",
                inbound_sid or "-",
                from_number,
                buffered.pending_count,
                exc,
            )
            return PlainTextResponse("", status_code=200)

    @app.post("/twilio/status")
    async def twilio_status_callback(request: Request):
        booking_repository_local = _resolve(booking_repository)
        form = await request.form()
        msg_sid = form.get("MessageSid") or form.get("SmsSid") or "-"
        msg_status = form.get("MessageStatus") or form.get("SmsStatus") or "-"
        err_code = form.get("ErrorCode") or "-"
        err_msg = form.get("ErrorMessage") or "-"
        to_number = form.get("To") or "-"
        from_number = form.get("From") or "-"
        logger.info(
            "Twilio status sid=%s status=%s to=%s from=%s error_code=%s error_message=%s",
            msg_sid,
            msg_status,
            to_number,
            from_number,
            err_code,
            err_msg,
        )
        if booking_repository_local:
            try:
                booking_repository_local.upsert_delivery_status(
                    provider="twilio",
                    provider_message_sid=str(msg_sid),
                    channel="whatsapp",
                    message_status=str(msg_status),
                    to_number=str(to_number),
                    from_number=str(from_number),
                    error_code=str(err_code),
                    error_message=str(err_msg),
                    payload_json=json.dumps(dict(form), ensure_ascii=False),
                )
            except Exception as exc:
                logger.warning("Failed to persist Twilio callback sid=%s error=%s", msg_sid, exc)
        return PlainTextResponse("", status_code=200)
