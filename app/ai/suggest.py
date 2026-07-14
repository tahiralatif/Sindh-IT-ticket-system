"""AI-powered department suggestion using LLM proxy."""
import json
import httpx
from typing import Optional
from app.core.config import settings

# Department list for context
DEPARTMENTS = [
    {"name": "Health Department", "code": "HEALTH"},
    {"name": "Education Department", "code": "EDUCATION"},
    {"name": "Transport Department", "code": "TRANSPORT"},
    {"name": "Revenue Department", "code": "REVENUE"},
    {"name": "Police Department", "code": "POLICE"},
    {"name": "Excise & Taxation", "code": "EXCISE"},
    {"name": "Social Welfare Department", "code": "SOCIAL"},
    {"name": "Information Department", "code": "INFO"},
    {"name": "Works & Services", "code": "WORKS"},
    {"name": "Agriculture Department", "code": "AGRI"},
]

SYSTEM_PROMPT = """You are a government ticket classification AI for the Government of Sindh, Pakistan.

Given a ticket subject and description, determine the most appropriate department to handle it.

Available departments:
{departments}

Return ONLY a JSON object in this exact format, nothing else:
{{"dept": "<department name>", "confidence": <0-100>}}

Rules:
- confidence is 0-100 (how sure you are)
- Pick the SINGLE best department
- If unsure, pick the closest match with lower confidence
- Only output the JSON object, no explanation"""


async def suggest_department(subject: str, description: str) -> Optional[dict]:
    """Call LLM to suggest the best department for a ticket."""
    if not settings.OPENROUTER_API_KEY:
        return None

    dept_list = "\n".join(f"- {d['name']} ({d['code']})" for d in DEPARTMENTS)
    system_msg = SYSTEM_PROMPT.format(departments=dept_list)
    user_msg = f"Subject: {subject}\n\nDescription: {description}"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                settings.OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://tickets.12.jugaar.ai",
                },
                json={
                    "model": settings.OPENROUTER_MODEL,
                    "messages": [
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": user_msg},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 100,
                },
            )

            if response.status_code == 200:
                data = response.json()
                content = data["choices"][0]["message"]["content"].strip()
                # Parse JSON from response
                if content.startswith("{"):
                    result = json.loads(content)
                    return {
                        "dept": result.get("dept", ""),
                        "confidence": min(100, max(0, int(result.get("confidence", 50)))),
                    }
    except Exception:
        pass

    return None


def keyword_fallback(subject: str, description: str) -> list[dict]:
    """Simple keyword-based fallback when LLM is unavailable."""
    text = f"{subject} {description}".lower()
    dept_keywords = {
        "Transport Department": ["road", "pothole", "traffic", "accident", "vehicle", "highway", "bridge"],
        "Health Department": ["hospital", "doctor", "health", "medical", "medicine", "patient", "clinic"],
        "Education Department": ["school", "university", "education", "student", "admission", "college", "exam"],
        "Police Department": ["theft", "crime", "police", "stolen", "robbery", "murder", "assault"],
        "Revenue Department": ["land", "property", "revenue", "tax", "inteqal", "registration"],
        "Works & Services": ["water", "building", "construction", "sewerage", "electricity", "road repair"],
        "Excise & Taxation": ["vehicle registration", "permit", "excise", "tax", "license"],
        "Social Welfare Department": ["welfare", "social", "poverty", "relief", "disability"],
        "Information Department": ["IT", "internet", "computer", "software", "system", "website"],
        "Agriculture Department": ["crop", "farm", "agriculture", "irrigation", "pesticide", "livestock"],
    }

    results = []
    for dept, keywords in dept_keywords.items():
        matches = [kw for kw in keywords if kw in text]
        if matches:
            score = len(matches) / len(keywords)
            results.append({"dept": dept, "confidence": min(100, max(10, int(score * 100))), "keywords": matches})

    results.sort(key=lambda x: x["confidence"], reverse=True)
    return results[:3]
