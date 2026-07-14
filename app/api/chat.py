"""AI Chatbot — citizen ticket filing + admin database query assistant.

Citizen mode: conversational ticket creation using Groq.
Admin mode: read-only database queries using Groq for intent parsing.
"""
import json
import re
import logging
from datetime import datetime, date
from typing import Optional

from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.models import User, Department, Ticket, TicketHistory, Attachment, Notification
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
            max_tokens=500,
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


# ═══════════════════════════════════════════════════════════════════
#  CITIZEN MODE
# ═══════════════════════════════════════════════════════════════════

CITIZEN_SYSTEM = """You are a helpful ticket-filing assistant for the Government of Sindh, Pakistan.

You help citizens file complaints and requests. You must gather information conversationally.

Your conversation flow:
1. Greet the user and ask what issue they want to report.
2. Ask for a brief subject (one line summary).
3. Ask for a detailed description.
4. Ask which city this is in (e.g., Karachi, Hyderabad, Sukkur, Larkana, etc.).
5. Optionally ask for a service name if relevant (e.g., Water Supply, Road Repair).
6. Summarize everything and ask for confirmation before submitting.

IMPORTANT RULES:
- Be polite, professional, and use simple language.
- Reply in the same language the user writes in (English or Urdu).
- After each question, WAIT for the user's response. Do not ask multiple questions at once.
- When the user gives a short answer, acknowledge it and move to the next question.
- When the user confirms, output EXACTLY this JSON on its own line (no other text):
  {"action": "submit", "subject": "...", "description": "...", "city": "...", "service_name": "..."}
- service_name can be empty string if not relevant.
- If the user says "no" or "cancel" after you summarize, say OK and offer to start over.
- Keep responses short (2-3 sentences max).
"""

CITIZEN_WELCOME = "Assalam o Alaikum! Welcome to the Sindh Government Ticket System. I'm here to help you file a complaint or request. What issue would you like to report?"


async def citizen_chat(user_id: int, message: str, history: list[dict], db: AsyncSession) -> dict:
    """Handle citizen chat. Returns {"reply": str, "action": optional dict}."""
    # Build conversation history for Groq
    messages_text = ""
    for msg in history:
        role = "Assistant" if msg["role"] == "assistant" else "Citizen"
        messages_text += f"{role}: {msg['content']}\n"
    messages_text += f"Citizen: {message}\n"

    response = await _groq_chat(CITIZEN_SYSTEM, messages_text)
    if not response:
        return {"reply": "I'm having trouble connecting to the AI service. Please try again or use the submit form directly."}

    # Check if AI wants to submit
    json_match = re.search(r'\{"action"\s*:\s*"submit"[^}]+\}', response)
    if json_match:
        try:
            action_data = json.loads(json_match.group())
            # Actually create the ticket
            subject = action_data.get("subject", "")
            description = action_data.get("description", "")
            city = action_data.get("city", "")
            service_name = action_data.get("service_name", "")

            # Get AI department suggestion
            ai_result = await suggest_department(subject, description)
            ai_dept = ai_result["dept"] if ai_result else None
            ai_conf = ai_result.get("confidence", 0) if ai_result else None

            # Find the department ID
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

            # Build confirmation message
            reply = (
                f"✅ Your ticket has been filed successfully!\n\n"
                f"📋 **Ticket Number:** {ticket.ticket_number}\n"
                f"📝 **Subject:** {subject}\n"
                f"🏛️ **Department:** {ai_dept or 'Pending assignment'}"
                f"{' (' + str(ai_conf) + '% confidence)' if ai_conf else ''}\n"
                f"📍 **City:** {city or 'Not specified'}\n\n"
                f"You can track your ticket status using the Track page or check your dashboard."
            )
            return {"reply": reply}

        except Exception as e:
            logger.error(f"Ticket submission from chat failed: {e}")
            return {"reply": "Sorry, there was an error filing your ticket. Please try using the submit form."}

    # Return the AI's conversational reply (strip any accidental JSON)
    clean_reply = re.sub(r'\{[^}]*"action"[^}]*\}', '', response).strip()
    return {"reply": clean_reply or response}


# ═══════════════════════════════════════════════════════════════════
#  ADMIN MODE
# ═══════════════════════════════════════════════════════════════════

ADMIN_SYSTEM = """You are an internal query assistant for the Government of Sindh IT Ticket System admin.

You have access to the ticket database. You can answer questions about:
- Total tickets, pending tickets, resolved tickets
- Tickets by department, status, priority, city
- Tickets submitted today or in a date range
- Ticket resolution rates and average resolution time
- Lists of recent or overdue tickets

RULES:
- NEVER modify, create, or delete any data. You are READ-ONLY.
- NEVER answer with made-up numbers. You MUST use the provided database data.
- When the user asks a question, output EXACTLY a JSON query object on its own line.
  The JSON must be one of these formats:

  {"query": "count_by_status"}
  {"query": "count_by_department"}
  {"query": "count_by_priority"}
  {"query": "count_by_city"}
  {"query": "count_by_service"}
  {"query": "today_summary"}
  {"query": "resolution_stats"}
  {"query": "department_tickets", "department": "<name>"}
  {"query": "status_tickets", "status": "<status>"}
  {"query": "recent_tickets", "limit": 10}
  {"query": "overdue_tickets"}
  {"query": "search_tickets", "q": "<search term>"}
  {"query": "user_stats"}

- After receiving database data, format a clear, readable answer.
- Use bullet points and tables for clarity.
- Keep answers concise but complete.
- Reply in the same language the user writes in.
"""

# Query templates for building SQL
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
    """Execute a predefined query and return formatted results."""
    from app.core.database import async_session

    async with async_session() as db:
        result_data = {}

        if query_key in QUERY_TEMPLATES:
            result = await db.execute(text(QUERY_TEMPLATES[query_key]))
            rows = result.all()
            result_data = {"rows": [{"col_" + str(i): v for i, v in enumerate(r)} for r in rows],
                           "raw": [list(r) for r in rows]}

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
            # Tickets in "submitted" status for more than 7 days
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

        return result_data


from sqlalchemy import text


async def admin_chat(user_id: int, message: str, history: list[dict], db: AsyncSession) -> dict:
    """Handle admin chat. Returns {"reply": str}."""
    # Build conversation history
    messages_text = ""
    for msg in history:
        role = "Admin" if msg["role"] == "user" else "Assistant"
        messages_text += f"{role}: {msg['content']}\n"
    messages_text += f"Admin: {message}\n"

    # First, let the AI generate a query
    response = await _groq_chat(ADMIN_SYSTEM, messages_text)
    if not response:
        return {"reply": "I'm having trouble connecting to the AI service. Please try again."}

    # Try to extract query JSON
    json_match = re.search(r'\{"query"\s*:\s*"[^"]+"[^}]*\}', response)
    if json_match:
        try:
            query_obj = json.loads(json_match.group())
            query_key = query_obj.get("query", "")
            query_params = {k: v for k, v in query_obj.items() if k != "query"}

            # Execute the actual database query
            result_data = await _execute_query(query_key, query_params)

            # Now send the query results back to the AI for formatting
            data_prompt = f"""The admin asked: "{message}"

I executed this query: {query_key} {json.dumps(query_params) if query_params else ''}

Here are the raw results from the database:
{json.dumps(result_data, indent=2, default=str)}

Format this into a clear, readable answer for the admin. Use bullet points, numbers, and short sentences. Do NOT make up any numbers — only use what's in the results above."""

            formatted = await _groq_chat(
                "You are a data formatting assistant. Format the given database results into a clear, readable answer. Use the exact numbers from the data. Do not hallucinate.",
                data_prompt
            )

            return {"reply": formatted or f"Query result: {json.dumps(result_data, indent=2, default=str)}"}

        except Exception as e:
            logger.error(f"Admin chat query failed: {e}")
            return {"reply": f"I encountered an error processing that query. Please try rephrasing."}

    # AI gave a direct answer without a query (e.g., greeting)
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
