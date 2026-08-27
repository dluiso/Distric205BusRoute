"""Isolated SMTP transport used by configuration tests and application delivery.

The transport is intentionally independent from Flask's global configuration so
concurrent diagnostics, broadcasts, and scheduled notifications cannot overwrite
one another's connection state.
"""

from __future__ import annotations

from contextlib import contextmanager, suppress
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
import smtplib
import socket
import ssl
from typing import Iterator, Sequence


@dataclass(frozen=True)
class SMTPSettings:
    provider: str
    server: str
    port: int
    use_tls: bool
    use_ssl: bool
    username: str
    password: str
    from_email: str
    from_name: str


class EmailTransportError(RuntimeError):
    """A sanitized transport failure safe to persist or display."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.retryable = retryable


def classify_transport_error(exc: BaseException) -> EmailTransportError:
    if isinstance(exc, EmailTransportError):
        return exc
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return EmailTransportError(
            "authentication_rejected",
            "Authentication was rejected by the email provider.",
        )
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return EmailTransportError(
            "sender_rejected",
            "The provider rejected the configured From address. Verify Send As permission.",
        )
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return EmailTransportError(
            "recipient_rejected",
            "The provider rejected the recipient address.",
        )
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return EmailTransportError(
            "smtp_feature_unsupported",
            "The server does not support a required SMTP security feature.",
        )
    if isinstance(exc, (ssl.SSLError, ssl.CertificateError)):
        return EmailTransportError(
            "tls_failed",
            "TLS negotiation or certificate verification failed.",
        )
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return EmailTransportError(
            "connection_timeout",
            "The SMTP connection timed out.",
            retryable=True,
        )
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return EmailTransportError(
            "server_disconnected",
            "The SMTP server disconnected unexpectedly.",
            retryable=True,
        )
    if isinstance(exc, smtplib.SMTPResponseException):
        response_code = int(getattr(exc, "smtp_code", 0) or 0)
        return EmailTransportError(
            "smtp_temporary_failure" if 400 <= response_code < 500 else "smtp_rejected",
            "The SMTP server temporarily rejected the request."
            if 400 <= response_code < 500
            else "The SMTP server rejected the request.",
            retryable=400 <= response_code < 500,
        )
    if isinstance(exc, (ConnectionRefusedError, socket.gaierror, OSError)):
        return EmailTransportError(
            "connection_failed",
            "The SMTP server could not be reached.",
            retryable=True,
        )
    return EmailTransportError(
        "delivery_failed",
        "Email delivery failed for an unexpected transport reason.",
        retryable=True,
    )


def _open_connection(settings: SMTPSettings) -> smtplib.SMTP:
    try:
        if settings.use_ssl:
            smtp = smtplib.SMTP_SSL(
                settings.server,
                settings.port,
                timeout=15,
                context=ssl.create_default_context(),
            )
        else:
            smtp = smtplib.SMTP(settings.server, settings.port, timeout=15)
        smtp.ehlo()
        if settings.use_tls:
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
        if settings.username:
            if not settings.password:
                raise EmailTransportError(
                    "password_required",
                    "A password is required for the selected SMTP account.",
                )
            smtp.login(settings.username, settings.password)
        return smtp
    except Exception as exc:
        smtp_instance = locals().get("smtp")
        if smtp_instance is not None:
            with suppress(Exception):
                smtp_instance.close()
        raise classify_transport_error(exc) from exc


@contextmanager
def smtp_session(settings: SMTPSettings) -> Iterator[smtplib.SMTP]:
    smtp = _open_connection(settings)
    try:
        yield smtp
    except Exception as exc:
        raise classify_transport_error(exc) from exc
    finally:
        with suppress(Exception):
            smtp.quit()
        with suppress(Exception):
            smtp.close()


def verify_connection(settings: SMTPSettings) -> None:
    with smtp_session(settings):
        return


def send_email(
    settings: SMTPSettings,
    *,
    subject: str,
    recipients: Sequence[str],
    body: str,
) -> None:
    clean_recipients = [address.strip() for address in recipients if address and address.strip()]
    if not clean_recipients:
        raise EmailTransportError("recipient_required", "At least one recipient is required.")
    if not settings.from_email:
        raise EmailTransportError("sender_required", "A From address is required.")
    header_values = [subject, settings.from_name, settings.from_email, *clean_recipients]
    if any("\r" in value or "\n" in value for value in header_values):
        raise EmailTransportError(
            "invalid_header", "Email header values cannot contain line breaks.")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((settings.from_name, settings.from_email))
    message["To"] = ", ".join(clean_recipients)
    message.set_content(body)

    with smtp_session(settings) as smtp:
        try:
            smtp.send_message(message)
        except Exception as exc:
            raise classify_transport_error(exc) from exc
