"""AI Chatbot — citizen ticket filing + admin database assistant with write actions.

Citizen mode: conversational ticket creation using Groq.
Admin mode: read + write actions (assign, status change, bulk ops).
"""
import json
import re
import logging
from datetime import datetime, date
from typing import Optional

from sqlalchemy import select, func, and_, or_, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.models import User, Department, Ticket, TicketHistory, Attachment, Notification, ChatHistory
from app.ai.suggest import DEPARTMENTS, suggest_department

logger = logging.getLogger(__name__)

# ─── Groq helper ──────────────────────────────────────────────────

async def _groq_chat(system_prompt: str, user_message: str) -> Optional[str]:
    """Send a chat completion to Groq and return the response text."""
    api_key = settings.GROQ_API_KEY
    if not api_key:
        return None
    try:
        from langchain_groq import ChatGroq
        from langchain_core.messages import HumanMessage, SystemMessage
        llm = ChatGroq(
            groq_api_key=api_key,
            model_name="llama-3.1-8b-instant",
            temperature=0.0,
            max_tokens=800,
        )
        resp = llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_message)])
        return resp.content.strip()
    except Exception as e:
        logger.error(f"Groq chat failed: {e}")
        return None


# ─── Ticket number generator ──────────────────────────────────────

async def _next_ticket_number(db: AsyncSession) -> str:
    today = date.today().strftime("%Y%m%d")
    result = await db.execute(
        select(func.count(Ticket.id)).where(
            Ticket.ticket_number.like(f"SIT-{today}-%")
        )
    )
    count = (result.scalar() or 0) + 1
    return f"SIT-{today}-{count:04d}"


async def save_chat_message(db: AsyncSession, user_id: int, role: str, content: str):
    """Save a chat message to the database."""
    msg = ChatHistory(user_id=user_id, role=role, content=content)
    db.add(msg)
    await db.commit()


async def load_chat_history(db: AsyncSession, user_id: int, limit: int = 50) -> list[dict]:
    """Load recent chat history for a user from the database."""
    result = await db.execute(
        select(ChatHistory.role, ChatHistory.content)
        .where(ChatHistory.user_id == user_id)
        .order_by(ChatHistory.created_at.desc())
        .limit(limit)
    )
    rows = result.all()
    # Reverse to get chronological order
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]


async def clear_chat_history(db: AsyncSession, user_id: int):
    """Clear all chat history for a user."""
    await db.execute(
        text("DELETE FROM chat_history WHERE user_id = :uid"), {"uid": user_id}
    )
    await db.commit()


# ═══════════════════════════════════════════════════════════════════
#  CITIZEN MODE
# ═══════════════════════════════════════════════════════════════════

CITIZEN_SYSTEM = """You are a helpful ticket-filing assistant for the Government of Sindh, Pakistan.

You help citizens file complaints and requests. You must gather information conversationally.

## CONVERSATION FLOW:
1. If the user's FIRST message already contains issue details, skip the greeting and ACKNOWLEDGE it immediately. For example:
   - User says: "create complaint ticket about loadshading" → You say: "I understand you want to report a loadshedding issue. Let me help you file this complaint."
   - User says: "I want to complain about water supply in Karachi" → You say: "I'll help you file a complaint about water supply in Karachi."
   - User says: "Road has big pothole near Bahadurabad" → You say: "I understand — a road pothole near Bahadurabad. Let me file this for you."

2. If the first message is unclear or too short, THEN ask what issue they want to report.

3. After acknowledging, ask for a ONE-LINE subject if not already provided.

4. Ask for a DETAILED description if not already provided.

5. Ask which CITY this is in (e.g., Karachi, Hyderabad, Sukkur, Larkana, Mirpur Khas, etc.) if not mentioned.

6. Ask for SERVICE TYPE if not mentioned (e.g., Road Repair, Water Supply, Electricity, Sanitation, etc.).

7. SUMMARIZE everything you've collected and ask: "Shall I submit this complaint?"

8. When user confirms (yes/sure/submit/karo/do it), output EXACTLY this JSON on its own line:
{"action": "submit", "subject": "...", "description": "...", "city": "...", "service_name": "..."}

## IMPORTANT RULES:
- Be polite, professional, and use simple language.
- Reply in the same language the user writes in (English or Urdu/Roman Urdu).
- NEVER repeat the same question. If you already asked for the subject, move on.
- If the user gives information in their first message, USE IT. Don't ask again.
- Keep responses SHORT (1-3 sentences max). Don't be overly chatty.
- When you have enough information, summarize and ask for confirmation — don't keep asking questions.
- If user says "no" or "cancel" after summary, say OK and offer to start over.
- You can infer service_name from the description (e.g., pothole → Road Repair, no water → Water Supply, dark road → Electricity).
- You can infer city if mentioned in the description.
"""

CITIZEN_WELCOME = "Assalam o Alaikum! Welcome to the Sindh Government Ticket System. I'm here to help you file a complaint or request.\n\nYou can describe your issue and I'll file a ticket for you. What problem would you like to report?"


async def citizen_chat(user_id: int, message: str, history: list[dict], db: AsyncSession) -> dict:
    """Handle citizen chat. Returns {"reply": str, "action": optional dict}."""
    # Save user message to database
    await save_chat_message(db, user_id, "user", message)

    messages_text = ""
    for msg in history:
        role = "Assistant" if msg["role"] == "assistant" else "Citizen"
        messages_text += f"{role}: {msg['content']}\n"
    messages_text += f"Citizen: {message}\n"

    response = await _groq_chat(CITIZEN_SYSTEM, messages_text)
    if not response:
        reply = "I'm having trouble connecting to the AI service. Please try again or use the submit form directly."
        await save_chat_message(db, user_id, "assistant", reply)
        return {"reply": reply}

    json_match = re.search(r'\{"action"\s*:\s*"submit"[^}]+\}', response)
    if json_match:
        try:
            action_data = json.loads(json_match.group())
            subject = action_data.get("subject", "")
            description = action_data.get("description", "")
            city = action_data.get("city", "")
            service_name = action_data.get("service_name", "")

            ai_result = await suggest_department(subject, description)
            ai_dept = ai_result["dept"] if ai_result else None
            ai_conf = ai_result.get("confidence", 0) if ai_result else None

            dept_id = None
            if ai_dept:
                dept_result = await db.execute(
                    select(Department.id).where(Department.name == ai_dept)
                )
                row = dept_result.first()
                if row:
                    dept_id = row[0]

            ticket_number = await _next_ticket_number(db)

            ticket = Ticket(
                ticket_number=ticket_number,
                subject=subject,
                description=description,
                category="complaint",
                priority="medium",
                status="submitted",
                submitted_by=user_id,
                assigned_to_dept=dept_id,
                ai_suggested_dept=ai_dept,
                ai_confidence=ai_conf,
                service_name=service_name if service_name else None,
                city=city if city else None,
            )
            db.add(ticket)
            await db.commit()
            await db.refresh(ticket)

            reply = (
                f"✅ Your ticket has been filed successfully!\n\n"
                f"📋 **Ticket Number:** {ticket.ticket_number}\n"
                f"📝 **Subject:** {subject}\n"
                f"🏛️ **Department:** {ai_dept or 'Pending assignment'}"
                f"{' (' + str(ai_conf) + '% confidence)' if ai_conf else ''}\n"
                f"📍 **City:** {city or 'Not specified'}\n\n"
                f"You can track your ticket status using the Track page or check your dashboard."
            )
            await save_chat_message(db, user_id, "assistant", reply)
            return {"reply": reply}

        except Exception as e:
            logger.error(f"Ticket submission from chat failed: {e}")
            reply = "Sorry, there was an error filing your ticket. Please try using the submit form."
            await save_chat_message(db, user_id, "assistant", reply)
            return {"reply": reply}

    clean_reply = re.sub(r'\{[^}]*"action"[^}]*\}', '', response).strip()
    final_reply = clean_reply or response
    await save_chat_message(db, user_id, "assistant", final_reply)
    return {"reply": final_reply}


# ═══════════════════════════════════════════════════════════════════
#  ADMIN MODE — Read + Write
# ═══════════════════════════════════════════════════════════════════

ADMIN_SYSTEM = """You are an AI assistant for the Government of Sindh IT Ticket System admin.

You can perform TWO types of actions:

## 1. READ — when admin wants to VIEW data
Output a query JSON:
  {"action": "query", "query": "count_by_status"}
  {"action": "query", "query": "count_by_department"}
  {"action": "query", "query": "count_by_priority"}
  {"action": "query", "query": "count_by_city"}
  {"action": "query", "query": "count_by_service"}
  {"action": "query", "query": "today_summary"}
  {"action": "query", "query": "resolution_stats"}
  {"action": "query", "query": "department_tickets", "department": "<name>"}
  {"action": "query", "query": "status_tickets", "status": "<status>"}
  {"action": "query", "query": "recent_tickets", "limit": 10}
  {"action": "query", "query": "overdue_tickets"}
  {"action": "query", "query": "search_tickets", "q": "<term>"}
  {"action": "query", "query": "ticket_detail", "ticket_number": "<SIT-...>"}

## 2. WRITE — when admin wants to MODIFY data
Output an action JSON:
  {"action": "assign_ticket", "ticket_number": "SIT-...", "department": "Transport Department"}
  {"action": "change_status", "ticket_number": "SIT-...", "status": "in_progress"}
  {"action": "bulk_assign", "filter": {"status": "submitted"}, "department": "Transport Department"}
  {"action": "bulk_status", "filter": {"status": "submitted"}, "new_status": "in_progress"}

Valid statuses: submitted, assigned, in_progress, resolved, closed
Valid departments: Health Department, Education Department, Transport Department, Revenue Department, Police Department, Excise & Taxation, Social Welfare Department, Information Department, Works & Services, Agriculture Department

## RULES:
- NEVER answer with made-up numbers. Use only the database data provided.
- When unsure, ask for clarification.
- For write actions, always include the exact ticket number or clear filter.
- Reply concisely. Use bullet points for lists.
- Reply in the same language the user writes in.
"""

# ─── Read query templates ─────────────────────────────────────────

QUERY_TEMPLATES = {
    "count_by_status": "SELECT status, COUNT(*) FROM tickets GROUP BY status",
    "count_by_department": """SELECT d.name, COUNT(t.id) FROM departments d
        LEFT JOIN tickets t ON d.id = t.assigned_to_dept GROUP BY d.id""",
    "count_by_priority": "SELECT priority, COUNT(*) FROM tickets GROUP BY priority",
    "count_by_city": """SELECT city, COUNT(*) FROM tickets
        WHERE city IS NOT NULL AND city != '' GROUP BY city ORDER BY COUNT(*) DESC""",
    "count_by_service": """SELECT service_name, COUNT(*) FROM tickets
        WHERE service_name IS NOT NULL AND service_name != '' GROUP BY service_name ORDER BY COUNT(*) DESC""",
    "user_stats": "SELECT role, COUNT(*) FROM users GROUP BY role",
}


async def _execute_query(query_key: str, params: dict = None) -> dict:
    """Execute a predefined read query and return results."""
    from app.core.database import async_session

    async with async_session() as db:
        result_data = {}

        if query_key in QUERY_TEMPLATES:
            result = await db.execute(text(QUERY_TEMPLATES[query_key]))
            rows = result.all()
            result_data = {"raw": [list(r) for r in rows]}

        elif query_key == "today_summary":
            today = date.today().isoformat()
            total = (await db.execute(select(func.count(Ticket.id)))).scalar() or 0
            today_count = (await db.execute(
                select(func.count(Ticket.id)).where(func.date(Ticket.created_at) == today)
            )).scalar() or 0
            submitted = (await db.execute(
                select(func.count(Ticket.id)).where(Ticket.status == "submitted")
            )).scalar() or 0
            in_progress = (await db.execute(
                select(func.count(Ticket.id)).where(Ticket.status == "in_progress")
            )).scalar() or 0
            resolved = (await db.execute(
                select(func.count(Ticket.id)).where(Ticket.status == "resolved")
            )).scalar() or 0
            result_data = {
                "total_all_time": total,
                "submitted_today": today_count,
                "currently_submitted": submitted,
                "currently_in_progress": in_progress,
                "currently_resolved": resolved,
            }

        elif query_key == "resolution_stats":
            total = (await db.execute(select(func.count(Ticket.id)))).scalar() or 0
            resolved = (await db.execute(
                select(func.count(Ticket.id)).where(Ticket.status.in_(["resolved", "closed"]))
            )).scalar() or 0
            result = await db.execute(
                select(
                    func.julianday(Ticket.resolved_at) - func.julianday(Ticket.created_at)
                ).where(Ticket.resolved_at.isnot(None))
            )
            days = [r[0] for r in result.all() if r[0] is not None]
            result_data = {
                "total": total,
                "resolved": resolved,
                "rate": round(resolved / total * 100, 1) if total > 0 else 0,
                "avg_days": round(sum(days) / len(days), 1) if days else 0,
                "min_days": round(min(days), 1) if days else 0,
                "max_days": round(max(days), 1) if days else 0,
            }

        elif query_key == "department_tickets":
            dept_name = params.get("department", "") if params else ""
            result = await db.execute(
                select(Ticket.ticket_number, Ticket.subject, Ticket.status, Ticket.priority)
                .join(Department, Ticket.assigned_to_dept == Department.id, isouter=True)
                .where(Department.name.contains(dept_name) if dept_name else True)
                .order_by(Ticket.created_at.desc()).limit(20)
            )
            result_data = {"tickets": [{"number": r[0], "subject": r[1], "status": r[2], "priority": r[3]} for r in result.all()]}

        elif query_key == "status_tickets":
            status = params.get("status", "") if params else ""
            result = await db.execute(
                select(Ticket.ticket_number, Ticket.subject, Ticket.status, Ticket.priority)
                .where(Ticket.status == status)
                .order_by(Ticket.created_at.desc()).limit(20)
            )
            result_data = {"tickets": [{"number": r[0], "subject": r[1], "status": r[2], "priority": r[3]} for r in result.all()]}

        elif query_key == "recent_tickets":
            limit = (params.get("limit", 10) if params else 10)
            result = await db.execute(
                select(Ticket.ticket_number, Ticket.subject, Ticket.status, Ticket.priority, Ticket.created_at)
                .order_by(Ticket.created_at.desc()).limit(limit)
            )
            result_data = {"tickets": [{"number": r[0], "subject": r[1], "status": r[2], "priority": r[3], "date": str(r[4])} for r in result.all()]}

        elif query_key == "overdue_tickets":
            from datetime import timedelta
            cutoff = datetime.utcnow() - timedelta(days=7)
            result = await db.execute(
                select(Ticket.ticket_number, Ticket.subject, Ticket.status, Ticket.priority, Ticket.created_at)
                .where(Ticket.status.in_(["submitted", "assigned"]))
                .where(Ticket.created_at < cutoff)
                .order_by(Ticket.created_at.asc()).limit(20)
            )
            result_data = {"tickets": [{"number": r[0], "subject": r[1], "status": r[2], "priority": r[3], "date": str(r[4])} for r in result.all()]}

        elif query_key == "search_tickets":
            q = params.get("q", "") if params else ""
            result = await db.execute(
                select(Ticket.ticket_number, Ticket.subject, Ticket.status, Ticket.priority)
                .where(or_(Ticket.subject.contains(q), Ticket.ticket_number.contains(q)))
                .order_by(Ticket.created_at.desc()).limit(20)
            )
            result_data = {"tickets": [{"number": r[0], "subject": r[1], "status": r[2], "priority": r[3]} for r in result.all()]}

        elif query_key == "ticket_detail":
            tn = params.get("ticket_number", "") if params else ""
            result = await db.execute(
                select(Ticket, User.full_name, Department.name)
                .join(User, Ticket.submitted_by == User.id, isouter=True)
                .join(Department, Ticket.assigned_to_dept == Department.id, isouter=True)
                .where(Ticket.ticket_number == tn)
            )
            row = result.first()
            if row:
                t, submitter, dept = row
                result_data = {
                    "ticket_number": t.ticket_number,
                    "subject": t.subject,
                    "description": t.description,
                    "status": t.status,
                    "priority": t.priority,
                    "department": dept or "Unassigned",
                    "city": t.city or "Not specified",
                    "service_name": t.service_name or "Not specified",
                    "submitted_by": submitter,
                    "created_at": str(t.created_at),
                }
            else:
                result_data = {"error": f"Ticket {tn} not found"}

        return result_data


# ─── Write action helpers ─────────────────────────────────────────

VALID_STATUSES = ["submitted", "assigned", "in_progress", "resolved", "closed"]
VALID_DEPARTMENTS = [
    "Health Department", "Education Department", "Transport Department",
    "Revenue Department", "Police Department", "Excise & Taxation",
    "Social Welfare Department", "Information Department",
    "Works & Services", "Agriculture Department",
]


async def _resolve_dept_id(db: AsyncSession, dept_name: str) -> Optional[int]:
    """Find department ID by name (exact or fuzzy match)."""
    # First try exact match
    result = await db.execute(select(Department.id, Department.name).where(Department.name == dept_name))
    row = result.first()
    if row:
        return row[0]
    # Then try fuzzy match against all valid departments
    all_depts = (await db.execute(select(Department.id, Department.name))).all()
    for did, dname in all_depts:
        if dept_name.lower() in dname.lower() or dname.lower() in dept_name.lower():
            return did
    return None


async def _resolve_ticket_id(db: AsyncSession, ticket_number: str) -> Optional[int]:
    """Find ticket ID by ticket number."""
    result = await db.execute(select(Ticket.id).where(Ticket.ticket_number == ticket_number))
    row = result.first()
    return row[0] if row else None


async def _find_matching_tickets(db: AsyncSession, filt: dict) -> list[int]:
    """Find ticket IDs matching filter criteria for bulk operations."""
    query = select(Ticket.id)
    conditions = []
    if filt.get("status"):
        conditions.append(Ticket.status == filt["status"])
    if filt.get("city"):
        conditions.append(Ticket.city.contains(filt["city"]))
    if filt.get("department"):
        query = query.join(Department, Ticket.assigned_to_dept == Department.id, isouter=True)
        conditions.append(Department.name.contains(filt["department"]))
    if filt.get("service_name"):
        conditions.append(Ticket.service_name.contains(filt["service_name"]))
    if filt.get("priority"):
        conditions.append(Ticket.priority == filt["priority"])
    if conditions:
        query = query.where(and_(*conditions))
    result = await db.execute(query)
    return [r[0] for r in result.all()]


async def _execute_write_action(action: dict, db: AsyncSession) -> dict:
    """Execute a confirmed write action."""
    action_type = action.get("action")

    if action_type == "assign_ticket":
        ticket_num = action.get("ticket_number", "")
        dept_name = action.get("department", "")
        ticket_id = await _resolve_ticket_id(db, ticket_num)
        if not ticket_id:
            return {"success": False, "message": f"❌ Ticket {ticket_num} not found."}
        dept_id = await _resolve_dept_id(db, dept_name)
        if not dept_id:
            all_depts = (await db.execute(select(Department.name))).all()
            valid_list = [r[0] for r in all_depts]
            return {"success": False, "message": f"❌ Department '{dept_name}' not found. Valid: {', '.join(valid_list)}"}
        await db.execute(
            text("UPDATE tickets SET assigned_to_dept = :dept, status = 'assigned' WHERE id = :tid"),
            {"dept": dept_id, "tid": ticket_id}
        )
        await db.commit()
        return {"success": True, "message": f"✅ Ticket **{ticket_num}** assigned to **{dept_name}**."}

    elif action_type == "change_status":
        ticket_num = action.get("ticket_number", "")
        new_status = action.get("status", "")
        if new_status not in VALID_STATUSES:
            return {"success": False, "message": f"❌ Invalid status '{new_status}'. Valid: {', '.join(VALID_STATUSES)}"}
        ticket_id = await _resolve_ticket_id(db, ticket_num)
        if not ticket_id:
            return {"success": False, "message": f"❌ Ticket {ticket_num} not found."}
        update_fields = {"status": new_status}
        if new_status in ("resolved", "closed"):
            update_fields["resolved_at"] = datetime.utcnow().isoformat()
        set_clause = ", ".join(f"{k} = :{k}" for k in update_fields)
        await db.execute(
            text(f"UPDATE tickets SET {set_clause} WHERE id = :tid"),
            {**update_fields, "tid": ticket_id}
        )
        await db.commit()
        return {"success": True, "message": f"✅ Ticket **{ticket_num}** status changed to **{new_status}**."}

    elif action_type == "bulk_assign":
        filt = action.get("filter", {})
        dept_name = action.get("department", "")
        dept_id = await _resolve_dept_id(db, dept_name)
        if not dept_id:
            return {"success": False, "message": f"❌ Department '{dept_name}' not found."}
        ticket_ids = await _find_matching_tickets(db, filt)
        if not ticket_ids:
            return {"success": False, "message": "❌ No tickets matched the filter criteria."}
        placeholders = ", ".join([f":t{i}" for i in range(len(ticket_ids))])
        params = {f"t{i}": tid for i, tid in enumerate(ticket_ids)}
        params["dept"] = dept_id
        await db.execute(
            text(f"UPDATE tickets SET assigned_to_dept = :dept, status = 'assigned' WHERE id IN ({placeholders})"),
            params
        )
        await db.commit()
        return {"success": True, "message": f"✅ **{len(ticket_ids)} ticket(s)** assigned to **{dept_name}**."}

    elif action_type == "bulk_status":
        filt = action.get("filter", {})
        new_status = action.get("new_status", "")
        if new_status not in VALID_STATUSES:
            return {"success": False, "message": f"❌ Invalid status '{new_status}'."}
        ticket_ids = await _find_matching_tickets(db, filt)
        if not ticket_ids:
            return {"success": False, "message": "❌ No tickets matched the filter criteria."}
        placeholders = ", ".join([f":t{i}" for i in range(len(ticket_ids))])
        params = {f"t{i}": tid for i, tid in enumerate(ticket_ids)}
        params["status"] = new_status
        extra = ""
        if new_status in ("resolved", "closed"):
            extra = ", resolved_at = :resolved"
            params["resolved"] = datetime.utcnow().isoformat()
        await db.execute(
            text(f"UPDATE tickets SET status = :status{extra} WHERE id IN ({placeholders})"),
            params
        )
        await db.commit()
        return {"success": True, "message": f"✅ **{len(ticket_ids)} ticket(s)** status changed to **{new_status}**."}

    return {"success": False, "message": f"❌ Unknown action: {action_type}"}


# ─── Pending action markers (hidden from user) ────────────────────

_PENDING_OPEN = "{_PENDING_ACTION_:"
_PENDING_CLOSE = "}"


def _describe_action(action: dict) -> str:
    """Human-readable description of a write action."""
    a = action["action"]
    filt = action.get("filter", {})
    if a == "bulk_assign":
        dept = action.get("department", "?")
        criteria = ", ".join(f"{k}={v}" for k, v in filt.items())
        return f"**Assign** all tickets ({criteria}) → **{dept}**"
    elif a == "bulk_status":
        ns = action.get("new_status", "?")
        criteria = ", ".join(f"{k}={v}" for k, v in filt.items())
        return f"**Change status** of all tickets ({criteria}) → **{ns}**"
    elif a == "assign_ticket":
        return f"**Assign** {action.get('ticket_number', '?')} → {action.get('department', '?')}"
    elif a == "change_status":
        return f"**Change** {action.get('ticket_number', '?')} → {action.get('status', '?')}"
    return str(action)


# ─── Admin chat handler ───────────────────────────────────────────

async def admin_chat(user_id: int, message: str, history: list[dict], db: AsyncSession) -> dict:
    """Handle admin chat. Supports read queries AND write actions."""
    # Save user message to database
    await save_chat_message(db, user_id, "user", message)

    # Check if this is an auto-assign request ("related/correct/right department")
    auto_assign_keywords = ["related department", "correct department", "right department", "appropriate department", "proper department"]
    is_auto_assign = any(kw in message.lower() for kw in auto_assign_keywords)

    # Check if this is a confirmation of a pending write action
    dept_result = await db.execute(select(Department.name).order_by(Department.name))
    valid_depts = [r[0] for r in dept_result.all()]
    depts_str = ", ".join(valid_depts) if valid_depts else "(none configured)"

    # Build dynamic admin system prompt with real department names
    admin_system = f"""You are an AI assistant for the Government of Sindh IT Ticket System admin.

You can perform TWO types of actions:

## 1. READ — when admin wants to VIEW data
Output a query JSON:
  {{"action": "query", "query": "count_by_status"}}
  {{"action": "query", "query": "count_by_department"}}
  {{"action": "query", "query": "count_by_priority"}}
  {{"action": "query", "query": "count_by_city"}}
  {{"action": "query", "query": "count_by_service"}}
  {{"action": "query", "query": "today_summary"}}
  {{"action": "query", "query": "resolution_stats"}}
  {{"action": "query", "query": "department_tickets", "department": "<name>"}}
  {{"action": "query", "query": "status_tickets", "status": "<status>"}}
  {{"action": "query", "query": "recent_tickets", "limit": 10}}
  {{"action": "query", "query": "overdue_tickets"}}
  {{"action": "query", "query": "search_tickets", "q": "<term>"}}
  {{"action": "query", "query": "ticket_detail", "ticket_number": "<SIT-...>"}}

## 2. WRITE — when admin wants to MODIFY data
Output an action JSON:
  {{"action": "assign_ticket", "ticket_number": "SIT-...", "department": "<exact name>"}}
  {{"action": "change_status", "ticket_number": "SIT-...", "status": "in_progress"}}
  {{"action": "bulk_assign", "filter": {{"status": "submitted"}}, "department": "<exact name>"}}
  {{"action": "bulk_status", "filter": {{"status": "submitted"}}, "new_status": "in_progress"}}

Valid statuses: submitted, assigned, in_progress, resolved, closed

## VALID DEPARTMENTS (use ONLY these exact names — do NOT invent new ones):
{depts_str}

## SERVICE-TO-DEPARTMENT MAPPING:
- Water Supply → Works & Services
- Road Repair → Works & Services
- Electricity → Works & Services
- Sanitation → Works & Services
- Street Light → Works & Services
- Gas Supply → Works & Services
- Gas Loadshedding → Works & Services
- Healthcare/Hospital → Health Department
- Vaccination → Health Department
- School/Education → Education Department
- Teacher Attendance → Education Department
- Police/Law Enforcement → Police Department
- Emergency Response → Police Department
- Property Tax/Revenue → Revenue Department
- Building Violation → Revenue Department
- Vehicle/Driving License → Excise & Taxation
- Social Services → Social Welfare Department
- Salary Disbursement → Social Welfare Department
- Government IT/Digital → Information Department
- Internet Infrastructure → Information Department
- Agriculture/Farming → Agriculture Department
- Public Transport → Transport Department

## IMPORTANT: AUTO-ASSIGN FLOW
When the user says "assign to the related department" or "assign to correct department" or "assign to the right department":
1. FIRST query ticket_detail to get the service_name
2. THEN use the SERVICE-TO-DEPARTMENT MAPPING above to pick the department
3. THEN output the assign_ticket action with that department
Example: "assign SIT-20260714-0018 to the related department"
→ Query ticket_detail for SIT-20260714-0018 → service_name: "Water Supply" → mapping says Works & Services → output: {{"action": "assign_ticket", "ticket_number": "SIT-20260714-0018", "department": "Works & Services"}}
NEVER ask the user for the department when they say "related" or "correct" — you must look it up yourself.

## NATURAL LANGUAGE → STATUS MAPPING:
- "in progress", "working on it", "started", "begin" → status: "in_progress"
- "submitted", "new", "pending" → status: "submitted"
- "assigned" → status: "assigned"
- "done", "completed", "resolved", "fixed" → status: "resolved"
- "closed", "archive" → status: "closed"

## RULES:
- NEVER answer with made-up numbers. Use only the database data provided.
- When unsure, ask for clarification.
- For write actions, always include the exact ticket number or clear filter.
- ALWAYS use valid department names from the list above. NEVER make up department names.
- ALWAYS use valid status values from the list above. Map natural language using the mapping above.
- When user describes a desired state (e.g., "it's in progress"), extract the target status and use change_status action.
- Reply concisely. Use bullet points for lists.
- Reply in the same language the user writes in.
"""
    msg_lower = message.strip().lower()
    if msg_lower in ("yes", "confirm", "y", "do it", "proceed", "ok", "go ahead"):
        for msg in reversed(history):
            if msg["role"] == "assistant":
                action_match = re.search(r'\{_PENDING_ACTION_:(.+)\}', msg["content"])
                if action_match:
                    try:
                        action = json.loads(action_match.group(1))
                        result = await _execute_write_action(action, db)
                        reply = result["message"]
                        await save_chat_message(db, user_id, "assistant", reply)
                        return {"reply": reply}
                    except Exception as e:
                        logger.error(f"Write action failed: {e}")
                        return {"reply": f"❌ Action failed: {str(e)}"}
                break

    if msg_lower in ("no", "cancel", "n", "nevermind", "never mind", "abort"):
        reply = "OK, action cancelled. What else can I help you with?"
        await save_chat_message(db, user_id, "assistant", reply)
        return {"reply": reply}

    # Build conversation history for Groq
    messages_text = ""
    for msg in history:
        role = "Admin" if msg["role"] == "user" else "Assistant"
        # Strip pending action markers from history
        clean = re.sub(r'\{_PENDING_ACTION_:.+?\}', '', msg["content"])
        messages_text += f"{role}: {clean}\n"
    messages_text += f"Admin: {message}\n"

    # Let the AI decide: query or write action?
    response = await _groq_chat(admin_system, messages_text)
    if not response:
        reply = "I'm having trouble connecting to the AI service. Please try again."
        await save_chat_message(db, user_id, "assistant", reply)
        return {"reply": reply}

    # ── Try to extract a WRITE action ──
    action_match = re.search(r'\{"action"\s*:\s*"(assign_ticket|change_status|bulk_assign|bulk_status)".*\}', response)
    if action_match:
        try:
            action = json.loads(action_match.group())

            # Validate
            if action["action"] in ("assign_ticket", "change_status"):
                tn = action.get("ticket_number", "")
                if not tn:
                    return {"reply": "Which ticket number? Please provide the ticket number (e.g., SIT-20260714-0001)."}
                # Verify ticket exists
                tid = await _resolve_ticket_id(db, tn)
                if not tid:
                    return {"reply": f"❌ Ticket {tn} not found. Please check the ticket number."}

            if action["action"] in ("change_status", "bulk_status"):
                ns = action.get("status") or action.get("new_status", "")
                if ns not in VALID_STATUSES:
                    return {"reply": f"❌ Invalid status. Valid options: {', '.join(VALID_STATUSES)}"}

            if action["action"] in ("assign_ticket", "bulk_assign"):
                dept = action.get("department", "")
                dept_id = await _resolve_dept_id(db, dept)
                if not dept_id:
                    all_depts = (await db.execute(select(Department.name))).all()
                    valid_list = [r[0] for r in all_depts]
                    return {"reply": f"❌ Department '{dept}' not found. Valid: {', '.join(valid_list)}"}

            # For bulk actions, show confirmation with count
            if action["action"] in ("bulk_assign", "bulk_status"):
                preview = await _find_matching_tickets(db, action.get("filter", {}))
                count = len(preview)
                if count == 0:
                    return {"reply": "No tickets match the specified filter. Nothing to do."}
                desc = _describe_action(action)
                reply = (
                    f"⚠️ **Bulk Action Preview:**\n\n"
                    f"{desc}\n\n"
                    f"**This will affect {count} ticket(s).**\n\n"
                    f"Type **confirm** to proceed or **cancel** to abort."
                )
                # Hide the action JSON from user but store it for confirmation
                await save_chat_message(db, user_id, "assistant", reply)
                return {"reply": reply, "_pending_action": action}

            # Single-ticket actions: execute immediately
            result = await _execute_write_action(action, db)
            reply = result["message"]
            await save_chat_message(db, user_id, "assistant", reply)
            return {"reply": reply}

        except json.JSONDecodeError:
            pass
        except Exception as e:
            logger.error(f"Write action error: {e}")
            return {"reply": f"❌ Error: {str(e)}"}

    # ── Try to extract a READ query ──
    query_match = re.search(r'\{"action"\s*:\s*"query"\s*,\s*"query"\s*:\s*"([^"]+)"(.*)\}', response)
    if query_match:
        try:
            query_key = query_match.group(1)
            extra_str = query_match.group(2).strip()
            query_params = {}
            if extra_str:
                for kv_match in re.finditer(r'"(\w+)"\s*:\s*"([^"]+)"', extra_str):
                    query_params[kv_match.group(1)] = kv_match.group(2)

            result_data = await _execute_query(query_key, query_params)

            data_prompt = (
                f'The admin asked: "{message}"\n\n'
                f"I executed this query: {query_key} {json.dumps(query_params) if query_params else ''}\n\n"
                f"Here are the raw results from the database:\n"
                f"{json.dumps(result_data, indent=2, default=str)}\n\n"
                f"Format this into a clear, readable answer. Use bullet points, numbers, and short sentences. "
                f"Do NOT make up any numbers — only use what's in the results above."
            )
            formatted = await _groq_chat(
                "You are a data formatting assistant. Format the given database results into a clear, readable answer. Use the exact numbers from the data. Do not hallucinate.",
                data_prompt
            )
            final_reply = formatted or f"Query result: {json.dumps(result_data, indent=2, default=str)}"

            # AUTO-ASSIGN: If this was a ticket_detail query during auto-assign request
            if is_auto_assign and query_key == "ticket_detail" and result_data.get("ticket_number"):
                service = (result_data.get("service_name") or "").lower()
                subject = (result_data.get("subject") or "").lower()
                desc = (result_data.get("description") or "").lower()
                ticket_number = result_data.get("ticket_number", "")

                # Service-to-department mapping
                svc_map = {
                    "water supply": "Works & Services",
                    "road repair": "Works & Services",
                    "electricity": "Works & Services",
                    "sanitation": "Works & Services",
                    "street light": "Works & Services",
                    "gas supply": "Works & Services",
                    "gas loadshedding": "Works & Services",
                    "sui gas": "Works & Services",
                    "gas": "Works & Services",
                    "healthcare": "Health Department",
                    "hospital": "Health Department",
                    "vaccination": "Health Department",
                    "school": "Education Department",
                    "education": "Education Department",
                    "teacher": "Education Department",
                    "police": "Police Department",
                    "fire": "Police Department",
                    "emergency": "Police Department",
                    "revenue": "Revenue Department",
                    "property tax": "Revenue Department",
                    "building": "Revenue Department",
                    "vehicle": "Excise & Taxation",
                    "driving license": "Excise & Taxation",
                    "social": "Social Welfare Department",
                    "salary": "Social Welfare Department",
                    "it": "Information Department",
                    "internet": "Information Department",
                    "agriculture": "Agriculture Department",
                    "farming": "Agriculture Department",
                    "transport": "Transport Department",
                }

                matched_dept = None
                for keyword, dept in svc_map.items():
                    if keyword in service or keyword in subject or keyword in desc:
                        matched_dept = dept
                        break

                # Default fallback
                if not matched_dept:
                    matched_dept = "Works & Services"

                # Execute assignment
                dept_id = await _resolve_dept_id(db, matched_dept)
                if dept_id:
                    tid = await _resolve_ticket_id(db, ticket_number)
                    if tid:
                        await db.execute(
                            text("UPDATE tickets SET assigned_to_dept=:dept, status='assigned', updated_at=CURRENT_TIMESTAMP WHERE id=:id"),
                            {"dept": dept_id, "id": tid}
                        )
                        await db.commit()
                        final_reply += f"\n\n✅ Auto-assigned to **{matched_dept}** based on the service type."
                        await save_chat_message(db, user_id, "assistant", final_reply)
                        return {"reply": final_reply}

            await save_chat_message(db, user_id, "assistant", final_reply)
            return {"reply": final_reply}

        except Exception as e:
            logger.error(f"Admin query failed: {e}")
            return {"reply": "I encountered an error processing that query. Please try rephrasing."}

    # AI gave a direct conversational answer
    await save_chat_message(db, user_id, "assistant", response)
    return {"reply": response}


# ═══════════════════════════════════════════════════════════════════
#  MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════

async def handle_chat(user_id: int, role: str, message: str, history: list[dict], db: AsyncSession) -> dict:
    """Route to citizen or admin handler based on role."""
    if role in ("admin", "department"):
        return await admin_chat(user_id, message, history, db)
    else:
        return await citizen_chat(user_id, message, history, db)
