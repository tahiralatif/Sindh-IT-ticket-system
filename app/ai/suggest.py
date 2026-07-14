"""AI department suggestion using Groq via langchain-groq.

Single classification call: ticket text in → department + confidence out.
Keyword fallback if the API call fails.
"""
import json
import re
import logging
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# ─── Department list (must match the seeded departments) ──────────
DEPARTMENTS = [
    "Health Department",
    "Education Department",
    "Transport Department",
    "Revenue Department",
    "Police Department",
    "Excise & Taxation",
    "Social Welfare Department",
    "Information Department",
    "Works & Services",
    "Agriculture Department",
]

DEPT_KEYWORDS = {
    "Health Department": ["health", "hospital", "doctor", "medical", "medicine", "disease", "clinic", "patient", "medicine", "vaccination", "epidemic", "pharmacy", "ambulance", "surgery", "nurse"],
    "Education Department": ["education", "school", "college", "university", "student", "teacher", "exam", "degree", "scholarship", "admission", "curriculum", "classroom"],
    "Transport Department": ["transport", "road", "highway", "traffic", "bus", "vehicle", "pothole", "bridge", "commute", "route", "bypass", "motorway"],
    "Revenue Department": ["revenue", "tax", "property", "land", "record", "mutation", "stamp", "registration", "assessment", "income", "revenue collection"],
    "Police Department": ["police", "crime", "theft", "robbery", "murder", "assault", "arrest", "fir", "investigation", "safety", "security", "illegal"],
    "Excise & Taxation": ["excise", "duty", "tobacco", "liquor", "vehicle tax", "license", "permit", "customs", "levy"],
    "Social Welfare Department": ["social welfare", "poverty", "elderly", "child", "orphan", "welfare", "disability", "women", "shelter", "benefit", "allowance"],
    "Information Department": ["information", "media", "press", "journalist", "news", "broadcast", "website", "social media", "publicity", "IT", "technology", "software"],
    "Works & Services": ["water", "sewage", "electricity", "construction", "building", "drainage", "garbage", "sanitation", "maintenance", "repair", "infrastructure", "plumbing"],
    "Agriculture Department": ["agriculture", "farming", "crop", "irrigation", "tractor", "fertilizer", "pesticide", "harvest", "livestock", "dairy", "fisheries", "seed"],
}

SYSTEM_PROMPT = """You are a ticket classification system for the Government of Sindh, Pakistan.

Given a ticket's subject and description, classify it into exactly ONE of these departments:

{departments}

You MUST respond with ONLY a JSON object (no explanation, no markdown):
{{"dept": "<department name exactly as listed above>", "confidence": <0.0 to 1.0>}}

Rules:
- confidence reflects how certain you are (1.0 = certain, 0.5 = guess)
- Pick the single most relevant department
- If truly ambiguous, pick the closest and lower confidence
- Do NOT explain. Output ONLY the JSON object."""


def keyword_fallback(subject: str, description: str) -> list[dict]:
    """Keyword-based fallback when AI is unavailable."""
    text = f"{subject} {description}".lower()
    scores = {}
    for dept, keywords in DEPT_KEYWORDS.items():
        matched = [kw for kw in keywords if kw in text]
        if matched:
            score = min(len(matched) * 0.15 + 0.1, 1.0)
            scores[dept] = {"dept": dept, "score": round(score, 2), "keywords": matched}

    results = sorted(scores.values(), key=lambda x: x["score"], reverse=True)

    # Convert to unified format
    for r in results:
        r["confidence"] = int(r["score"] * 100)
        del r["score"]

    return results[:3]


async def suggest_department(subject: str, description: str) -> Optional[dict]:
    """Use Groq via langchain-groq to classify the ticket into a department.

    Returns {"dept": "...", "confidence": 85} or None if AI is unavailable.
    Falls back to keyword matching if the API call fails.
    """
    api_key = settings.GROQ_API_KEY
    if not api_key:
        logger.info("No GROQ_API_KEY set, falling back to keyword matching")
        return None

    try:
        from langchain_groq import ChatGroq
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = ChatGroq(
            groq_api_key=api_key,
            model_name="llama-3.1-8b-instant",
            temperature=0.0,
            max_tokens=200,
        )

        dept_list = "\n".join(f"- {d}" for d in DEPARTMENTS)
        system_msg = SYSTEM_PROMPT.format(departments=dept_list)
        user_msg = f"Subject: {subject}\n\nDescription: {description}"

        messages = [
            SystemMessage(content=system_msg),
            HumanMessage(content=user_msg),
        ]

        response = llm.invoke(messages)
        raw = response.content.strip()

        # Try to extract JSON from the response
        # Handle markdown code blocks
        json_match = re.search(r'\{[^}]+\}', raw)
        if json_match:
            data = json.loads(json_match.group())
        else:
            data = json.loads(raw)

        dept = data.get("dept", "")
        confidence = data.get("confidence", 0.5)

        # Validate department name
        if dept not in DEPARTMENTS:
            # Try fuzzy match
            for valid_dept in DEPARTMENTS:
                if valid_dept.lower() in dept.lower() or dept.lower() in valid_dept.lower():
                    dept = valid_dept
                    break
            else:
                logger.warning(f"AI returned unknown department: {dept}")
                return None

        # Convert 0-1 confidence to percentage
        if isinstance(confidence, float) and confidence <= 1.0:
            confidence = int(confidence * 100)

        return {"dept": dept, "confidence": min(confidence, 100)}

    except Exception as e:
        logger.error(f"AI suggestion failed: {e}")
        return None
