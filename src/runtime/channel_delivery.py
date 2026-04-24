import json
import html
import re
import os
import time
import uuid
from typing import Any, Callable, Optional
from urllib import error as urlerror
from urllib import request as urlrequest

from src.runtime.account_scope import parse_scoped_user_id


class ChannelDelivery:
    def __init__(
        self,
        settings: Any,
        twilio_client: Any,
        logger: Any,
        log_event_fn: Callable[..., None],
        extract_chat_id_fn: Callable[[str], str],
        channel_account_lookup_fn: Optional[Callable[[int], dict]] = None,
    ) -> None:
        self.settings = settings
        self.twilio_client = twilio_client
        self.logger = logger
        self.log_event_fn = log_event_fn
        self.extract_chat_id_fn = extract_chat_id_fn
        self.channel_account_lookup_fn = channel_account_lookup_fn

    def _resolve_account(self, scoped_user_id: str) -> dict:
        account_id, _ = parse_scoped_user_id(scoped_user_id)
        if account_id is None or not self.channel_account_lookup_fn:
            return {}
        try:
            account = self.channel_account_lookup_fn(account_id) or {}
            if isinstance(account, dict):
                return account
        except Exception:
            pass
        return {}

    @staticmethod
    def _account_value(account: dict, *keys: str) -> str:
        for key in keys:
            value = account.get(key)
            text = str(value or "").strip()
            if text:
                return text
        return ""

    def _telegram_bot_token_for_account(self, account: dict) -> str:
        return self._account_value(account, "telegram_bot_token")

    def _provider_for_account(self, account: dict) -> str:
        account_provider = self._account_value(account, "provider", "_provider").lower()
        if account_provider in {"meta", "infobip", "twilio"}:
            return account_provider
        configured = (getattr(self.settings, "whatsapp_provider", "") or "").strip().lower()
        if configured in {"meta", "infobip", "twilio"}:
            return configured
        # auto mode:
        # prefer explicit account credentials when present, otherwise env credentials.
        if self._has_meta_credentials(account):
            return "meta"
        if self._has_infobip_credentials(account):
            return "infobip"
        if self._has_twilio_credentials(account):
            return "twilio"
        if (getattr(self.settings, "whatsapp_api_token", "") or "").strip() and (
            getattr(self.settings, "whatsapp_phone_number_id", "") or ""
        ).strip():
            return "meta"
        if (getattr(self.settings, "infobip_api_key", "") or "").strip() and (
            getattr(self.settings, "infobip_base_url", "") or ""
        ).strip() and (getattr(self.settings, "infobip_whatsapp_number", "") or "").strip():
            return "infobip"
        if self.twilio_client and (getattr(self.settings, "twilio_whatsapp_from", "") or "").strip():
            return "twilio"
        return ""

    def _has_meta_credentials(self, account: dict) -> bool:
        token = self._account_value(account, "whatsapp_api_token", "meta_access_token")
        phone_number_id = self._account_value(account, "whatsapp_phone_number_id", "meta_phone_number_id")
        return bool(token and phone_number_id)

    def _has_infobip_credentials(self, account: dict) -> bool:
        api_key = self._account_value(account, "infobip_api_key")
        base_url = self._account_value(account, "infobip_base_url")
        sender = self._account_value(account, "infobip_whatsapp_number", "infobip_sender", "whatsapp_from")
        return bool(api_key and base_url and sender)

    def _has_twilio_credentials(self, account: dict) -> bool:
        account_sid = self._account_value(account, "twilio_account_sid")
        auth_token = self._account_value(account, "twilio_auth_token")
        return bool(account_sid and auth_token)

    def _twilio_client_for_account(self, account: dict) -> Any:
        account_sid = self._account_value(account, "twilio_account_sid")
        auth_token = self._account_value(account, "twilio_auth_token")
        if account_sid and auth_token:
            try:
                from twilio.rest import Client as TwilioClient

                return TwilioClient(account_sid, auth_token)
            except Exception:
                self.logger.warning("Failed to create per-account Twilio client; falling back to global client.")
        return self.twilio_client

    def _twilio_from_number(self, account: dict) -> str:
        return self._account_value(account, "twilio_whatsapp_from", "whatsapp_from") or str(
            getattr(self.settings, "twilio_whatsapp_from", "") or ""
        ).strip()

    def _twilio_status_callback_url(self, account: dict) -> str:
        return self._account_value(account, "twilio_status_callback_url") or str(
            getattr(self.settings, "twilio_status_callback_url", "") or ""
        ).strip()

    @staticmethod
    def _raw_destination(scoped_user_id: str) -> str:
        _account_id, raw = parse_scoped_user_id(scoped_user_id)
        return raw

    def send_channel_response(self, to_number: str, reply_text: str, fsm_state: str, fsm, inbound_sid: str = "") -> None:
        if not (reply_text or "").strip():
            self.logger.info("Reply suppressed for sid=%s to=%s (empty/silent response).", inbound_sid or "-", to_number)
            return
        raw_to = self._raw_destination(to_number)
        if (raw_to or "").strip().lower().startswith("telegram:"):
            self.send_plain_channel_message(to_number=to_number, body=reply_text, inbound_sid=inbound_sid)
            return
        self.send_whatsapp_response(
            to_number=to_number,
            reply_text=reply_text,
            fsm_state=fsm_state,
            fsm=fsm,
            inbound_sid=inbound_sid,
        )

    def send_whatsapp_response(self, to_number: str, reply_text: str, fsm_state: str, fsm, inbound_sid: str = "") -> None:
        account = self._resolve_account(to_number)
        raw_to = self._raw_destination(to_number)
        provider = self._provider_for_account(account)
        if provider == "infobip":
            sid = self._send_infobip_whatsapp_text(to_number=raw_to, body=reply_text, account=account)
            self.logger.info(
                "Sent Infobip WhatsApp response sid=%s inbound_sid=%s state=%s",
                sid,
                inbound_sid or "-",
                fsm_state,
            )
            return
        if provider == "meta":
            sid = self._send_meta_whatsapp_text(to_number=raw_to, body=reply_text, account=account)
            self.logger.info(
                "Sent Meta WhatsApp response sid=%s inbound_sid=%s state=%s",
                sid,
                inbound_sid or "-",
                fsm_state,
            )
            return
        if not provider:
            raise RuntimeError("No WhatsApp provider is configured for this channel account.")
        twilio_client = self._twilio_client_for_account(account)
        twilio_from = self._twilio_from_number(account)
        if not twilio_client or not twilio_from:
            raise RuntimeError("Twilio client or sender number is not configured for this channel account.")

        template_sid = self.template_for_state(fsm_state)
        content_variables = self.content_variables_for_state(fsm_state, fsm)

        try:
            if template_sid:
                kwargs = {
                    "from_": twilio_from,
                    "to": raw_to,
                    "content_sid": template_sid,
                }
                if content_variables:
                    kwargs["content_variables"] = json.dumps(content_variables, ensure_ascii=False)
                sid = self.send_with_retries(
                    kwargs,
                    client=twilio_client,
                    status_callback_url=self._twilio_status_callback_url(account),
                )
                self.logger.info(
                    "Sent template response sid=%s inbound_sid=%s state=%s template_sid=%s",
                    sid,
                    inbound_sid or "-",
                    fsm_state,
                    template_sid,
                )
                return

            sid = self.send_with_retries(
                {
                    "from_": twilio_from,
                    "to": raw_to,
                    "body": reply_text,
                },
                client=twilio_client,
                status_callback_url=self._twilio_status_callback_url(account),
            )
            self.logger.info(
                "Sent plain REST response sid=%s inbound_sid=%s state=%s",
                sid,
                inbound_sid or "-",
                fsm_state,
            )
        except Exception as exc:
            self.logger.exception("Failed to send WhatsApp response via REST API: %s", exc)
            raise

    def send_plain_channel_message(self, to_number: str, body: str, inbound_sid: str = "") -> str:
        account = self._resolve_account(to_number)
        raw_to = self._raw_destination(to_number)
        if (raw_to or "").strip().lower().startswith("telegram:"):
            t_send = time.perf_counter()
            self.send_telegram_message(to_number=to_number, body=body, inbound_sid=inbound_sid)
            send_ms = int((time.perf_counter() - t_send) * 1000)
            try:
                self.log_event_fn(self.extract_chat_id_fn(to_number), "BOT_REPLY_SENT", send_ms=send_ms, reply=body[:80])
            except Exception:
                pass
            return "telegram"
        provider = self._provider_for_account(account)
        if provider == "infobip":
            sid = self._send_infobip_whatsapp_text(to_number=raw_to, body=body, account=account)
            self.logger.info("Sent Infobip safe processing message sid=%s inbound_sid=%s to=%s", sid, inbound_sid or "-", to_number)
            return sid
        if provider == "meta":
            sid = self._send_meta_whatsapp_text(to_number=raw_to, body=body, account=account)
            self.logger.info("Sent Meta safe processing message sid=%s inbound_sid=%s to=%s", sid, inbound_sid or "-", to_number)
            return sid
        if not provider:
            raise RuntimeError("No WhatsApp provider is configured for this channel account.")
        twilio_client = self._twilio_client_for_account(account)
        twilio_from = self._twilio_from_number(account)
        if not twilio_client or not twilio_from:
            raise RuntimeError("Twilio client or sender number is not configured for this channel account.")
        sid = self.send_with_retries(
            {
                "from_": twilio_from,
                "to": raw_to,
                "body": body,
            },
            client=twilio_client,
            status_callback_url=self._twilio_status_callback_url(account),
        )
        self.logger.info("Sent safe processing message sid=%s inbound_sid=%s to=%s", sid, inbound_sid or "-", to_number)
        return sid

    def send_plain_channel_document(self, to_number: str, file_path: str, caption: str = "", inbound_sid: str = "") -> None:
        raw_to = self._raw_destination(to_number)
        if (raw_to or "").strip().lower().startswith("telegram:"):
            self.send_telegram_document(
                to_number=to_number,
                file_path=file_path,
                caption=caption,
                inbound_sid=inbound_sid,
            )
            return
        provider = self._provider_for_account(self._resolve_account(to_number))
        if not provider:
            raise RuntimeError("No WhatsApp provider is configured for this channel account.")
        self.logger.warning(
            "WhatsApp document send degraded to text provider=%s to=%s file=%s "
            "- configure a public media hosting endpoint to enable file delivery.",
            provider,
            to_number,
            os.path.basename(file_path),
        )
        msg = caption.strip() if caption else "Doctor reminder report generated."
        msg = f"{msg}\nReport file: {os.path.basename(file_path)}"
        self.send_plain_channel_message(to_number=to_number, body=msg, inbound_sid=inbound_sid)

    def _use_meta_whatsapp(self, account: Optional[dict] = None) -> bool:
        account = account or {}
        provider = self._provider_for_account(account)
        if provider == "meta":
            return True
        provider = (getattr(self.settings, "whatsapp_provider", "") or "").strip().lower()
        if provider == "meta":
            return True
        if provider == "infobip":
            return False
        if provider == "twilio":
            return False
        token = (getattr(self.settings, "whatsapp_api_token", "") or "").strip()
        phone_number_id = (getattr(self.settings, "whatsapp_phone_number_id", "") or "").strip()
        # auto mode: prefer Meta when both required values are present
        return bool(token and phone_number_id)

    def _use_infobip_whatsapp(self, account: Optional[dict] = None) -> bool:
        account = account or {}
        provider = self._provider_for_account(account)
        if provider == "infobip":
            return True
        provider = (getattr(self.settings, "whatsapp_provider", "") or "").strip().lower()
        if provider == "infobip":
            return True
        if provider in {"meta", "twilio"}:
            return False
        api_key = (getattr(self.settings, "infobip_api_key", "") or "").strip()
        base_url = (getattr(self.settings, "infobip_base_url", "") or "").strip()
        sender = (getattr(self.settings, "infobip_whatsapp_number", "") or "").strip()
        # auto mode: allow Infobip when credentials are present.
        return bool(api_key and base_url and sender)

    def _infobip_whatsapp_messages_url(self, account: Optional[dict] = None) -> str:
        account = account or {}
        raw_base = self._account_value(account, "infobip_base_url") or (getattr(self.settings, "infobip_base_url", "") or "").strip().rstrip("/")
        if not raw_base:
            raise RuntimeError("INFOBIP_BASE_URL is not configured.")
        if "://" not in raw_base:
            raw_base = f"https://{raw_base}"
        return f"{raw_base}/whatsapp/1/message/text"

    def _meta_graph_messages_url(self, account: Optional[dict] = None) -> str:
        account = account or {}
        version = self._account_value(account, "whatsapp_graph_api_version", "meta_graph_api_version") or (
            getattr(self.settings, "whatsapp_graph_api_version", "") or "v21.0"
        ).strip()
        phone_number_id = self._account_value(account, "whatsapp_phone_number_id", "meta_phone_number_id") or (
            getattr(self.settings, "whatsapp_phone_number_id", "") or ""
        ).strip()
        return f"https://graph.facebook.com/{version}/{phone_number_id}/messages"

    @staticmethod
    def _normalize_meta_to_number(to_number: str) -> str:
        raw = (to_number or "").strip()
        if raw.lower().startswith("whatsapp:"):
            raw = raw[len("whatsapp:") :].strip()
        digits = re.sub(r"\D+", "", raw)
        return digits

    def _send_meta_whatsapp_text(self, to_number: str, body: str, account: Optional[dict] = None) -> str:
        account = account or {}
        token = self._account_value(account, "whatsapp_api_token", "meta_access_token") or (
            getattr(self.settings, "whatsapp_api_token", "") or ""
        ).strip()
        if not token:
            raise RuntimeError("WHATSAPP_API_TOKEN/WHATSAPP_BOT_TOKEN is not configured.")
        phone_number_id = self._account_value(account, "whatsapp_phone_number_id", "meta_phone_number_id") or (
            getattr(self.settings, "whatsapp_phone_number_id", "") or ""
        ).strip()
        if not phone_number_id:
            raise RuntimeError("WHATSAPP_PHONE_NUMBER_ID is not configured.")
        to_digits = self._normalize_meta_to_number(to_number)
        if not to_digits:
            raise RuntimeError(f"Invalid WhatsApp destination: {to_number}")
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_digits,
            "type": "text",
            "text": {"preview_url": False, "body": body},
        }
        req = urlrequest.Request(
            url=self._meta_graph_messages_url(account=account),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            method="POST",
        )
        with urlrequest.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {}
        if isinstance(parsed, dict):
            messages = parsed.get("messages")
            if isinstance(messages, list) and messages:
                msg_id = str((messages[0] or {}).get("id") or "").strip()
                if msg_id:
                    return msg_id
        return "-"

    def _send_infobip_whatsapp_text(self, to_number: str, body: str, account: Optional[dict] = None) -> str:
        account = account or {}
        api_key = self._account_value(account, "infobip_api_key") or (getattr(self.settings, "infobip_api_key", "") or "").strip()
        if not api_key:
            raise RuntimeError("INFOBIP_API_KEY is not configured.")
        sender = self._normalize_meta_to_number(
            self._account_value(account, "infobip_whatsapp_number", "infobip_sender", "whatsapp_from")
            or getattr(self.settings, "infobip_whatsapp_number", "")
            or ""
        )
        if not sender:
            raise RuntimeError("INFOBIP_WHATSAPP_NUMBER is not configured.")
        to_digits = self._normalize_meta_to_number(to_number)
        if not to_digits:
            raise RuntimeError(f"Invalid WhatsApp destination: {to_number}")
        payload = {
            "from": sender,
            "to": to_digits,
            "content": {"text": body},
        }
        req = urlrequest.Request(
            url=self._infobip_whatsapp_messages_url(account=account),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"App {api_key}",
            },
            method="POST",
        )
        with urlrequest.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {}
        if isinstance(parsed, dict):
            messages = parsed.get("messages")
            if isinstance(messages, list) and messages:
                msg_id = str((messages[0] or {}).get("messageId") or "").strip()
                if msg_id:
                    return msg_id
            msg_id = str(parsed.get("messageId") or "").strip()
            if msg_id:
                return msg_id
        return "-"

    def send_telegram_message(self, to_number: str, body: str, inbound_sid: str = "") -> None:
        account = self._resolve_account(to_number)
        token = self._telegram_bot_token_for_account(account)
        if not token:
            raise RuntimeError("telegram_bot_token is not configured for the selected channel account.")
        chat_id = self.telegram_chat_id_from_user(self._raw_destination(to_number))
        if not chat_id:
            raise RuntimeError(f"Invalid Telegram destination: {to_number}")
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps(
            {
                "chat_id": chat_id,
                "text": self._format_telegram_text(body),
                "parse_mode": "HTML",
            }
        ).encode("utf-8")
        req = urlrequest.Request(
            url=url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlrequest.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                self.logger.info(
                    "Sent Telegram message inbound_sid=%s to=%s response=%s",
                    inbound_sid or "-",
                    to_number,
                    raw[:200],
                )
        except urlerror.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            self.logger.error("Telegram send failed status=%s body=%s", exc.code, detail[:400])
            raise
        except Exception as exc:
            self.logger.error("Telegram send failed error=%s", exc)
            raise

    @staticmethod
    def _format_telegram_text(body: str) -> str:
        escaped = html.escape(body or "")
        escaped = re.sub(
            r"\*(Appointment ID:\s*[^*\n]+)\*",
            lambda match: f"<b>{match.group(1)}</b>",
            escaped,
        )
        escaped = re.sub(
            r"\*(Clinic:\s*[^*\n]+)\*",
            lambda match: f"<b>{match.group(1)}</b>",
            escaped,
        )
        escaped = re.sub(
            r"\*(Date:\s*[^*\n]+)\*",
            lambda match: f"<b>{match.group(1)}</b>",
            escaped,
        )
        escaped = re.sub(
            r"\*(Time:\s*[^*\n]+)\*",
            lambda match: f"<b>{match.group(1)}</b>",
            escaped,
        )
        escaped = re.sub(
            r"\*(अपॉइंटमेंट आईडी:\s*[^*\n]+)\*",
            lambda match: f"<b>{match.group(1)}</b>",
            escaped,
        )
        escaped = re.sub(
            r"\*(क्लिनिक:\s*[^*\n]+)\*",
            lambda match: f"<b>{match.group(1)}</b>",
            escaped,
        )
        escaped = re.sub(
            r"\*(तारीख:\s*[^*\n]+)\*",
            lambda match: f"<b>{match.group(1)}</b>",
            escaped,
        )
        escaped = re.sub(
            r"\*(समय:\s*[^*\n]+)\*",
            lambda match: f"<b>{match.group(1)}</b>",
            escaped,
        )
        escaped = re.sub(
            r"\*(Patient ID:\s*[^*\n]+)\*",
            lambda match: f"<b>{match.group(1)}</b>",
            escaped,
        )
        return re.sub(
            r"\*(Appointment ID:|Patient ID:|अपॉइंटमेंट आईडी:)\*",
            lambda match: f"<b>{match.group(1)}</b>",
            escaped,
        )

    def send_telegram_document(self, to_number: str, file_path: str, caption: str = "", inbound_sid: str = "") -> None:
        account = self._resolve_account(to_number)
        token = self._telegram_bot_token_for_account(account)
        if not token:
            raise RuntimeError("telegram_bot_token is not configured for the selected channel account.")
        chat_id = self.telegram_chat_id_from_user(self._raw_destination(to_number))
        if not chat_id:
            raise RuntimeError(f"Invalid Telegram destination: {to_number}")
        if not os.path.exists(file_path):
            raise RuntimeError(f"Report file not found: {file_path}")

        url = f"https://api.telegram.org/bot{token}/sendDocument"
        boundary = f"----CodexBoundary{uuid.uuid4().hex}"
        file_name = os.path.basename(file_path)
        with open(file_path, "rb") as handle:
            file_bytes = handle.read()

        parts = bytearray()

        def append_field(name: str, value: str) -> None:
            parts.extend(f"--{boundary}\r\n".encode("utf-8"))
            parts.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
            parts.extend((value or "").encode("utf-8"))
            parts.extend(b"\r\n")

        append_field("chat_id", chat_id)
        if caption:
            append_field("caption", caption)

        parts.extend(f"--{boundary}\r\n".encode("utf-8"))
        parts.extend(
            (
                f'Content-Disposition: form-data; name="document"; filename="{file_name}"\r\n'
                "Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n"
            ).encode("utf-8")
        )
        parts.extend(file_bytes)
        parts.extend(b"\r\n")
        parts.extend(f"--{boundary}--\r\n".encode("utf-8"))

        req = urlrequest.Request(
            url=url,
            data=bytes(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urlrequest.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                self.logger.info(
                    "Sent Telegram document inbound_sid=%s to=%s file=%s response=%s",
                    inbound_sid or "-",
                    to_number,
                    file_name,
                    raw[:200],
                )
        except urlerror.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            self.logger.error("Telegram document send failed status=%s body=%s", exc.code, detail[:400])
            raise
        except Exception as exc:
            self.logger.error("Telegram document send failed error=%s", exc)
            raise

    def telegram_chat_id_from_user(self, user_id: str) -> str:
        raw = (user_id or "").strip()
        if raw.startswith("telegram:"):
            return raw[len("telegram:") :]
        return raw

    def resolve_telegram_bot_username(self, account: Optional[dict] = None) -> str:
        token = self._telegram_bot_token_for_account(account or {})
        if not token:
            return ""
        url = f"https://api.telegram.org/bot{token}/getMe"
        req = urlrequest.Request(url=url, method="GET")
        try:
            with urlrequest.urlopen(req, timeout=10) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
            if not isinstance(payload, dict) or not payload.get("ok"):
                return ""
            result = payload.get("result") or {}
            username = str(result.get("username") or "").strip().lstrip("@")
            return username
        except Exception:
            return ""

    def send_with_retries(self, kwargs: dict, *, client: Any = None, status_callback_url: str = "") -> str:
        twilio_client = client or self.twilio_client
        if not twilio_client:
            return "-"
        callback = (status_callback_url or "").strip() or str(getattr(self.settings, "twilio_status_callback_url", "") or "").strip()
        if callback:
            kwargs = dict(kwargs)
            kwargs["status_callback"] = callback
        last_exc = None
        total_attempts = max(1, self.settings.twilio_send_retries + 1)
        for attempt in range(1, total_attempts + 1):
            try:
                message = twilio_client.messages.create(**kwargs)
                return message.sid
            except Exception as exc:
                last_exc = exc
                self.logger.warning("Twilio send attempt failed attempt=%d/%d error=%s", attempt, total_attempts, exc)
                if attempt == total_attempts:
                    raise
                continue
        if last_exc:
            raise last_exc
        return "-"

    def template_for_state(self, state: str) -> str:
        mapping = {
            "ASK_PHONE": self.settings.twilio_template_phone_choice_sid,
            "ASK_CLINIC": self.settings.twilio_template_clinic_sid,
            "ASK_DATE": self.settings.twilio_template_date_sid,
            "ASK_TIME": self.settings.twilio_template_time_sid,
        }
        return mapping.get(state, "")

    def content_variables_for_state(self, state: str, fsm) -> dict:
        if state == "ASK_DATE":
            dates = fsm._date_options()
            if len(dates) < 3:
                return {}
            d1, d2, d3 = dates[:3]
            return {"1": d1, "2": d2, "3": d3}
        if state == "ASK_TIME":
            slots = fsm._suggested_slots()
            if len(slots) < 3:
                return {}
            return {"1": slots[0], "2": slots[1], "3": slots[2]}
        return {}
