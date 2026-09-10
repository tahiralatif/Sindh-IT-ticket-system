"""Email sending service via Brevo (formerly Sendinblue) transactional email API.

Gracefully skips if BREVO_API_KEY is not configured — logs and returns False.
Uses Jinja2 templates from templates/emails/ for HTML email rendering.
"""
import asyncio
import logging
from typing import Optional

import httpx
from jinja2 import Environment, FileSystemLoader
import os

from app.core.config import settings

logger = logging.getLogger(__name__)

# Brevo API
BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"

# Jinja2 template rendering
_template_dir = os.path.join(os.path.dirname(__file__), "..", "templates", "emails")
_jinja_env = Environment(loader=FileSystemLoader(_template_dir))


async def send_email(
    to_email: str,
    subject: str,
    template_name: str,
    context: dict,
    cc_email: Optional[str] = None,
) -> bool:
    """Send an HTML email via Brevo transactional email API.

    Returns True on success, False on failure or if API key is missing.
    """
    api_key = settings.BREVO_API_KEY
    sender_email = settings.BREVO_SENDER_EMAIL
    sender_name = settings.EMAIL_FROM_NAME

    if not api_key:
        logger.warning(f"[EMAIL SKIPPED] BREVO_API_KEY not configured — would send to {to_email}: {subject}")
        return False

    if not sender_email:
        logger.warning(f"[EMAIL SKIPPED] BREVO_SENDER_EMAIL not configured — would send to {to_email}: {subject}")
        return False

    # Render HTML from Jinja2 template
    try:
        template = _jinja_env.get_template(template_name)
        html_body = template.render(**context)
    except Exception as e:
        logger.error(f"[EMAIL ERROR] Failed to render template '{template_name}': {e}")
        return False

    # Build Brevo API payload
    payload = {
        "sender": {"name": sender_name, "email": sender_email},
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": html_body,
    }
    if cc_email:
        payload["cc"] = [{"email": cc_email}]

    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "api-key": api_key,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(BREVO_API_URL, json=payload, headers=headers)

        if response.status_code in (200, 201):
            logger.info(f"[EMAIL SENT] To: {to_email} | Subject: {subject}")
            return True
        else:
            error_detail = response.text[:500]
            logger.error(f"[EMAIL ERROR] Brevo API returned {response.status_code}: {error_detail}")
            return False

    except httpx.TimeoutException:
        logger.error(f"[EMAIL ERROR] Timeout sending to {to_email}")
        return False
    except Exception as e:
        logger.error(f"[EMAIL ERROR] {e}")
        return False
