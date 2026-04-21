"""SMS Notification Service - Isolated, modular SMS delivery."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional
from urllib import error as urlerror
from urllib import request as urlrequest


class SMSNotificationService:
    """
    Handles SMS notifications for appointment events.
    Completely isolated - no database access, no FSM knowledge.
    Configuration driven from .env settings.
    """

    def __init__(self, settings: Any, logger: Any) -> None:
        """
        Initialize SMS service with settings from environment.

        Args:
            settings: Settings object with SMS_* environment variables
            logger: Logger instance
        """
        self.settings = settings
        self.logger = logger

        # Parse SMS configuration from settings
        self.sms_enabled = self._parse_bool(getattr(settings, "sms_enabled", "false"))
        self.sms_api_url = (getattr(settings, "sms_api_url", "") or "").strip()
        self.sms_api_key = (getattr(settings, "sms_api_key", "") or "").strip()
        self.sms_sender = (getattr(settings, "sms_sender", "Dappto") or "Dappto").strip()
        self.sms_message_type = (getattr(settings, "sms_message_type", "TXT") or "TXT").strip()
        self.sms_response = (getattr(settings, "sms_response", "Y") or "Y").strip()
        self.frontend_base_url = (getattr(settings, "frontend_base_url", "") or "").strip()

        # Parse enabled channels (comma-separated from .env)
        enabled_channels_raw = (getattr(settings, "sms_enabled_channels", "") or "").strip()
        self.enabled_channels = self._parse_channels(enabled_channels_raw)

        self.logger.info(
            "SMS Service initialized: enabled=%s channels=%s",
            self.sms_enabled,
            self.enabled_channels,
        )

    @staticmethod
    def _parse_bool(value: Any) -> bool:
        """Parse boolean from string."""
        text = str(value or "").strip().lower()
        return text in {"true", "1", "yes", "on"}

    @staticmethod
    def _parse_channels(channels_str: str) -> set[str]:
        """Parse comma-separated channels into set."""
        if not channels_str:
            return set()
        return {ch.strip().lower() for ch in channels_str.split(",") if ch.strip()}

    def is_sms_enabled_for_channel(self, channel: str) -> bool:
        """
        Check if SMS should be sent for this channel.

        Args:
            channel: Appointment channel (qr_scan, whatsapp_web, app, web, etc.)

        Returns:
            True if SMS should be sent for this channel
        """
        if not self.sms_enabled:
            return False
        if not self.enabled_channels:
            return False
        normalized = channel.strip().lower() if channel else ""
        return normalized in self.enabled_channels

    def build_confirmation_message(
        self,
        patient_name: str,
        doctor_name: str,
        appointment_date: str,
        appointment_time: str,
        clinic_name: str,
    ) -> str:
        """
        Build appointment confirmation SMS message.

        Args:
            patient_name: Patient's name
            doctor_name: Doctor's name
            appointment_date: Date (e.g., "2026-04-25")
            appointment_time: Time (e.g., "14:30")
            clinic_name: Clinic name

        Returns:
            Formatted SMS message
        """
        display_date = self._format_display_date(appointment_date)
        display_time = self._format_display_time(appointment_time)
        # Manager's template format
        message = (
            f"Dear {patient_name},\n"
            f"Your appointment with Dr. {doctor_name} on {display_date} at {clinic_name}, "
            f"{display_time} has successfully booked.\n"
            f"Arrive 10 min early.\n"
            f"Manage: {self.frontend_base_url}\n"
            f"- Dapto"
        )
        return message

    def build_cancellation_message(
        self,
        patient_name: str,
        doctor_name: str,
        appointment_date: str,
        appointment_time: str,
        clinic_name: str,
    ) -> str:
        """
        Build appointment cancellation SMS message.

        Args:
            patient_name: Patient's name
            doctor_name: Doctor's name
            appointment_date: Date
            appointment_time: Time
            clinic_name: Clinic name

        Returns:
            Formatted SMS message
        """
        display_date = self._format_display_date(appointment_date)
        display_time = self._format_display_time(appointment_time)
        message = (
            f"Update: Your appointment with Dr. {doctor_name} on {display_date} at "
            f"{clinic_name}, {display_time} was cancelled by the doctor.\n"
            f"Please book another slot.\n"
            f"Manage: {self.frontend_base_url}\n"
            f"- Dapto"
        )
        return message

    def build_rescheduled_message(
        self,
        patient_name: str,
        doctor_name: str,
        appointment_date: str,
        appointment_time: str,
        clinic_name: str,
    ) -> str:
        """
        Build appointment rescheduled SMS message.

        Args:
            patient_name: Patient's name
            doctor_name: Doctor's name
            appointment_date: New date
            appointment_time: New time
            clinic_name: Clinic name

        Returns:
            Formatted SMS message
        """
        display_date = self._format_display_date(appointment_date)
        display_time = self._format_display_time(appointment_time)
        message = (
            f"Update: Your appointment with Dr. {doctor_name} has been rescheduled.\n"
            f"New slot: {display_date} at {clinic_name}, {display_time}.\n"
            f"Manage: {self.frontend_base_url}\n"
            f"- Dapto"
        )
        return message

    def build_message_by_event_type(
        self,
        event_type: str,
        patient_name: str = "",
        doctor_name: str = "",
        appointment_date: str = "",
        appointment_time: str = "",
        clinic_name: str = "",
    ) -> str:
        """
        Build SMS message based on event type.

        Args:
            event_type: CONFIRMATION, CANCELLED, RESCHEDULED, DOCTOR_DELAYED
            patient_name: Patient name
            doctor_name: Doctor name
            appointment_date: Appointment date
            appointment_time: Appointment time
            clinic_name: Clinic name

        Returns:
            Formatted SMS message
        """
        event_type_upper = (event_type or "").strip().upper()

        if event_type_upper == "CONFIRMATION":
            return self.build_confirmation_message(
                patient_name, doctor_name, appointment_date, appointment_time, clinic_name
            )
        elif event_type_upper == "CANCELLED":
            return self.build_cancellation_message(
                patient_name, doctor_name, appointment_date, appointment_time, clinic_name
            )
        elif event_type_upper == "RESCHEDULED":
            return self.build_rescheduled_message(
                patient_name, doctor_name, appointment_date, appointment_time, clinic_name
            )
        else:
            # Fallback for unknown event types
            return f"Appointment update: {event_type_upper}. Manage: {self.frontend_base_url}"

    def send_sms(
        self,
        phone_number: str,
        message: str,
        meta_json: str = "",
    ) -> tuple[bool, Optional[str]]:
        """
        Send SMS via configured API.

        Args:
            phone_number: Recipient phone number
            message: SMS message text
            meta_json: Optional metadata JSON (for logging)

        Returns:
            Tuple of (success: bool, provider_message_id: Optional[str])
            - success: True if SMS sent successfully
            - provider_message_id: API response message ID or None
        """
        if not self.sms_enabled:
            self.logger.warning("SMS is disabled in configuration")
            return False, None

        if not self.sms_api_url or not self.sms_api_key:
            self.logger.error("SMS API URL or API key not configured")
            return False, None

        phone_normalized = self._normalize_phone(phone_number)
        if not phone_normalized:
            self.logger.error("Invalid phone number: %s", phone_number)
            return False, None

        try:
            # Build SMS API request URL
            api_url = (
                f"{self.sms_api_url}"
                f"?sender={self._url_encode(self.sms_sender)}"
                f"&numbers={phone_normalized}"
                f"&messagetype={self._url_encode(self.sms_message_type)}"
                f"&message={self._url_encode(message)}"
                f"&response={self._url_encode(self.sms_response)}"
                f"&apikey={self._url_encode(self.sms_api_key)}"
            )

            self.logger.info(
                "Sending SMS to phone=%s length=%d",
                phone_normalized,
                len(message),
            )

            # Make HTTP request to SMS API
            req = urlrequest.Request(api_url)
            with urlrequest.urlopen(req, timeout=10) as response:
                response_text = response.read().decode("utf-8")
                response_status = response.status

            self.logger.info(
                "SMS API response: status=%s response=%s",
                response_status,
                response_text,
            )

            if response_status == 200:
                provider_message_id = self._extract_provider_message_id(response_text)
                self.logger.info(
                    "SMS sent successfully to %s provider_id=%s",
                    phone_normalized,
                    provider_message_id or "",
                )
                return True, provider_message_id
            else:
                self.logger.warning(
                    "SMS API returned status %s: %s",
                    response_status,
                    response_text,
                )
                return False, None

        except urlerror.HTTPError as exc:
            error_msg = f"HTTP Error {exc.code}: {exc.reason}"
            self.logger.error("SMS send failed: %s", error_msg)
            return False, None
        except urlerror.URLError as exc:
            error_msg = f"URL Error: {exc.reason}"
            self.logger.error("SMS send failed: %s", error_msg)
            return False, None
        except Exception as exc:
            error_msg = f"Unexpected error: {str(exc)}"
            self.logger.error("SMS send failed: %s", error_msg)
            return False, None

    @staticmethod
    def _normalize_phone(phone_number: str) -> str:
        """
        Normalize phone number - keep digits only.

        Args:
            phone_number: Raw phone number

        Returns:
            Digits-only phone number
        """
        if not phone_number:
            return ""
        # Remove all non-digit characters
        normalized = "".join(ch for ch in str(phone_number) if ch.isdigit())
        return normalized

    @staticmethod
    def _url_encode(text: str) -> str:
        """URL encode text for query parameters."""
        if not text:
            return ""
        # Simple URL encoding for special characters
        import urllib.parse
        return urllib.parse.quote(str(text))

    @staticmethod
    def _format_display_time(raw_time: str) -> str:
        text = str(raw_time or "").strip()
        if not text:
            return ""
        for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M:%S %p"):
            try:
                return datetime.strptime(text.upper(), fmt).strftime("%I:%M %p").lstrip("0")
            except ValueError:
                continue
        return text

    @staticmethod
    def _format_display_date(raw_date: str) -> str:
        text = str(raw_date or "").strip()
        if not text:
            return ""
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(text, fmt).strftime("%d %b %Y")
            except ValueError:
                continue
        return text

    @staticmethod
    def _extract_provider_message_id(response_text: str) -> Optional[str]:
        """
        Extract a short provider message identifier from the SMS API response.

        Falls back to a safe truncated value so DB persistence does not fail.
        """
        raw = str(response_text or "").strip()
        if not raw:
            return None
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                data = payload.get("data")
                if isinstance(data, list) and data:
                    first = data[0]
                    if isinstance(first, dict):
                        for key in ("uniqueid", "message_id", "messageid", "id"):
                            value = str(first.get(key) or "").strip()
                            if value:
                                return value[:80]
                for key in ("uniqueid", "message_id", "messageid", "id", "status"):
                    value = str(payload.get(key) or "").strip()
                    if value:
                        return value[:80]
        except Exception:
            pass
        return raw[:80]
