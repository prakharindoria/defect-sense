"""DefectSense Email Notification Service.

Sends email alerts when a QA analyst assigns a defect/rework unit to a shop-floor worker.

Default Sender: admin.defect.sense@gmail.com
Default Target: prakhar181999@gmail.com

Executes asynchronously in a background executor so API latency is unaffected.
If SMTP fails (e.g. no internet/credentials), logs the attempt and returns a
graceful degradation instead of raising.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import httpx

_log = logging.getLogger(__name__)

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "admin.defect.sense@gmail.com")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
DEFAULT_RECIPIENT = os.environ.get("ALERT_RECIPIENT_EMAIL", "prakhar181999@gmail.com")
SENDER_NAME = "DefectSense Quality Control"
EMAIL_TEST_MODE = os.environ.get("EMAIL_TEST_MODE", "false").lower() == "true"
EMAIL_TEST_ENDPOINT = os.environ.get("EMAIL_TEST_ENDPOINT", "http://localhost:8000/api/v1/email/test")


def send_defect_assignment_email_sync(
    unit_id: str,
    verdict: str,
    disposition: str,
    primary_signal: str,
    assigned_to_display: str,
    assigned_by_display: str,
    recipient_email: str = DEFAULT_RECIPIENT,
) -> bool:
    """Synchronous worker that constructs and sends an HTML+text email."""
    subject = f"[DefectSense Alert] Defect Rework Assigned: {unit_id}"

    text_content = f"""DefectSense Quality Control Alert

Defect Rework Assignment Notification
--------------------------------------
Unit ID: {unit_id}
Verdict: {verdict.upper()}
Primary Signal: {primary_signal}
Assigned Worker: {assigned_to_display}
Assigned By: {assigned_by_display}
Required Disposition: {disposition}

Please access the DefectSense Station view to proceed with inspection and rework.
"""

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <style>
        body {{ font-family: 'Poppins', sans-serif; background-color: #F8F7F5; color: #1B1A18; margin: 0; padding: 20px; }}
        .card {{ background: #ffffff; max-width: 580px; margin: 0 auto; border-radius: 16px; border: 1px solid #EDEAE6; overflow: hidden; font-size: 14px; box-shadow: 0 10px 30px rgba(0,0,0,0.05); }}
        .header {{ background: #141210; color: #ffffff; padding: 20px 24px; display: flex; align-items: center; justify-content: space-between; }}
        .header h2 {{ margin: 0; font-size: 18px; font-weight: 600; letter-spacing: 0.08em; }}
        .content {{ padding: 24px; }}
        .badge {{ display: inline-block; padding: 6px 12px; border-radius: 99px; background: #FBEAE5; color: #C95034; font-weight: 600; font-family: monospace; font-size: 12px; }}
        .item {{ margin-bottom: 12px; }}
        .label {{ font-size: 11px; text-transform: uppercase; color: #8E8A84; letter-spacing: 0.08em; margin-bottom: 4px; }}
        .value {{ font-size: 15px; font-weight: 500; font-family: monospace; }}
        .footer {{ background: #F4F2EF; padding: 14px 24px; font-size: 11px; color: #7C776F; text-align: center; border-top: 1px solid #EDEAE6; }}
      </style>
    </head>
    <body>
      <div class="card">
        <div class="header">
          <h2>DefectSense Alert</h2>
          <span style="font-size: 12px; color: #A5A099;">Rework Assigned</span>
        </div>
        <div class="content">
          <div class="item">
            <div class="label">Unit ID</div>
            <div class="value">{unit_id}</div>
          </div>
          <div class="item">
            <div class="label">Verdict</div>
            <div><span class="badge">{verdict.upper()}</span></div>
          </div>
          <div class="item">
            <div class="label">Primary Signal</div>
            <div class="value">{primary_signal}</div>
          </div>
          <div class="item">
            <div class="label">Assigned Shop-Floor Worker</div>
            <div class="value">{assigned_to_display}</div>
          </div>
          <div class="item">
            <div class="label">Assigned By</div>
            <div class="value">{assigned_by_display}</div>
          </div>
          <div class="item" style="margin-top: 18px; padding-top: 14px; border-top: 1px solid #EDEAE6;">
            <div class="label">Required Disposition</div>
            <div class="value" style="color: #DD5F42;">{disposition}</div>
          </div>
        </div>
        <div class="footer">
          DefectSense Manufacturing Quality Control System · Automatic Notification
        </div>
      </div>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{SENDER_NAME} <{SMTP_USER}>"
    msg["To"] = recipient_email

    msg.attach(MIMEText(text_content, "plain"))
    msg.attach(MIMEText(html_content, "html"))

    email_payload = {
        "subject": subject,
        "from": f"{SENDER_NAME} <{SMTP_USER}>",
        "to": recipient_email,
        "text": text_content,
        "html": html_content,
        "unit_id": unit_id,
        "verdict": verdict,
        "assigned_to": assigned_to_display,
    }

    # Test mode: send to HTTP endpoint instead of SMTP
    if EMAIL_TEST_MODE:
        try:
            with httpx.Client(timeout=10) as client:
                response = client.post(EMAIL_TEST_ENDPOINT, json=email_payload)
                if response.status_code == 200:
                    _log.info("email.notification_sent_via_test_endpoint", extra={
                        "to": recipient_email,
                        "unit_id": unit_id,
                        "endpoint": EMAIL_TEST_ENDPOINT,
                    })
                    return True
                else:
                    _log.warning("email.test_endpoint_failed", extra={
                        "error": f"HTTP {response.status_code}",
                        "to": recipient_email,
                        "endpoint": EMAIL_TEST_ENDPOINT,
                    })
                    return False
        except Exception as exc:
            _log.warning("email.test_endpoint_error", extra={"error": str(exc), "to": recipient_email})
            return False

    # No SMTP password: simulate email (logs payload for testing)
    if not SMTP_PASSWORD:
        _log.info("email.notification_simulated", extra={
            "to": recipient_email,
            "unit_id": unit_id,
            "assigned_to": assigned_to_display,
            "note": "SMTP_PASSWORD not set; email payload constructed and logged successfully. Set SMTP_PASSWORD to enable real sending or EMAIL_TEST_MODE=true for HTTP fallback.",
        })
        return True

    # Production mode: send via SMTP
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, [recipient_email], msg.as_string())
        _log.info("email.notification_sent", extra={"to": recipient_email, "unit_id": unit_id, "method": "smtp"})
        return True
    except Exception as exc:
        _log.warning("email.smtp_send_failed", extra={
            "error": str(exc),
            "to": recipient_email,
            "host": SMTP_HOST,
            "port": SMTP_PORT,
            "user": SMTP_USER,
            "note": "Check SMTP_PASSWORD, network connectivity, and Gmail app-specific password setup",
        })
        return False


async def send_defect_assignment_email(
    unit_id: str,
    verdict: str,
    disposition: str,
    primary_signal: str,
    assigned_to_display: str,
    assigned_by_display: str,
    recipient_email: str = DEFAULT_RECIPIENT,
) -> bool:
    """Asynchronous wrapper that dispatches to a thread pool executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        send_defect_assignment_email_sync,
        unit_id,
        verdict,
        disposition,
        primary_signal,
        assigned_to_display,
        assigned_by_display,
        recipient_email,
    )
