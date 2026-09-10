"""Notification service — creates DB notifications + triggers emails."""
import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import User, Ticket, Notification
from app.services.email_service import send_email


async def create_notification(
    db: AsyncSession,
    user_id: int,
    title: str,
    message: str,
    ticket_id: int = None,
):
    """Create an in-app notification."""
    notif = Notification(
        user_id=user_id,
        ticket_id=ticket_id,
        title=title,
        message=message,
    )
    db.add(notif)
    return notif


async def notify_admin_new_ticket(db: AsyncSession, ticket: Ticket, submitter_name: str):
    """When citizen submits a ticket → notify all admins + send email to minister."""
    # 1. In-app notification for all admins
    admins = (await db.execute(
        select(User).where(User.role == "admin", User.is_active == True)
    )).scalars().all()

    for admin in admins:
        msg = f"Citizen '{submitter_name}' submitted ticket {ticket.ticket_number}."
        if ticket.ai_suggested_dept:
            msg += f" AI suggests: {ticket.ai_suggested_dept} ({ticket.ai_confidence}% confidence)"
        await create_notification(
            db, admin.id,
            f"🆕 New Ticket: {ticket.subject[:60]}",
            msg,
            ticket.id,
        )

    # 2. Send email to minister (all admins with email)
    minister_emails = [a.email for a in admins if a.email]
    if minister_emails:
        ctx = {
            "ticket_number": ticket.ticket_number,
            "subject": ticket.subject,
            "description": ticket.description,
            "priority": ticket.priority,
            "city": ticket.city or "N/A",
            "service": ticket.service_name or "N/A",
            "ai_dept": ticket.ai_suggested_dept or "Pending",
            "ai_confidence": ticket.ai_confidence,
            "submitter": submitter_name,
            "ticket_url": f"https://sindh-it-ticket.14.jugaar.ai/ticket/{ticket.id}",
            "system_name": "Sindh IT Ticket System",
        }
        for email in minister_emails:
            asyncio.create_task(send_email(
                to_email=email,
                subject=f"🆕 New Ticket {ticket.ticket_number}: {ticket.subject[:80]}",
                template_name="new_ticket.html",
                context=ctx,
            ))


async def notify_citizen_status_change(
    db: AsyncSession,
    ticket: Ticket,
    old_status: str,
    new_status: str,
    changed_by_name: str,
    note: str = "",
):
    """When admin/department changes ticket status → notify the citizen."""
    submitter = await db.get(User, ticket.submitted_by)
    if not submitter:
        return

    status_labels = {
        "submitted": "Submitted",
        "assigned": "Assigned to Department",
        "in_progress": "In Progress",
        "resolved": "Resolved ✅",
        "closed": "Closed",
        "rejected": "Rejected ❌",
    }

    notif_msg = f"Your ticket {ticket.ticket_number} status changed: {status_labels.get(old_status, old_status)} → {status_labels.get(new_status, new_status)}"
    if note:
        notif_msg += f"\nNote: {note}"
    notif_msg += f"\nUpdated by: {changed_by_name}"

    await create_notification(
        db,
        submitter.id,
        f"📋 Ticket {ticket.ticket_number}: {status_labels.get(new_status, new_status)}",
        notif_msg,
        ticket.id,
    )

    # Send email to citizen if they have one
    if submitter.email:
        ctx = {
            "ticket_number": ticket.ticket_number,
            "subject": ticket.subject,
            "old_status": status_labels.get(old_status, old_status),
            "new_status": status_labels.get(new_status, new_status),
            "note": note,
            "changed_by": changed_by_name,
            "ticket_url": f"https://sindh-it-ticket.14.jugaar.ai/ticket/{ticket.id}",
            "system_name": "Sindh IT Ticket System",
        }
        asyncio.create_task(send_email(
            to_email=submitter.email,
            subject=f"📋 Ticket {ticket.ticket_number} — Status Updated to {status_labels.get(new_status, new_status)}",
            template_name="status_update.html",
            context=ctx,
        ))
