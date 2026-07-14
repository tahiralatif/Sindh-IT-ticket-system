# 🇵🇰 Sindh IT Minister Portal

> **Live:** [sindh-it-ticket.14.jugaar.ai](https://sindh-it-ticket.14.jugaar.ai)

A smart ticket management system where citizens submit complaints, and the IT Minister reviews all tickets, sees which department they belong to, and assigns or moves them forward with a single click.

The built-in AI assistant reads every ticket, understands the issue, automatically suggests the correct department, and enables the minister to manage tickets using simple chat commands — making the entire process faster and more efficient.

---

## How It Works

### Step 1: Citizen Submits a Complaint
Citizen fills out a complaint form with:
- **Subject:** Title of the problem (e.g., "Pothole on Shahrah-e-Faisal")
- **Description:** Detailed explanation of the issue
- **Category:** Complaint, Request, or Emergency
- **Priority:** Low, Medium, High, Critical
- **City:** Karachi, Hyderabad, Sukkur, Larkana, etc.
- **Service:** Road Repair, Water Supply, Electricity, etc.

### Step 2: AI Automatically Assigns a Department
When a citizen submits a complaint, **Groq AI (LLaMA 3.1 model)** automatically:

1. Understands the complaint
2. Chooses the correct department out of 10 available departments
3. Provides a **confidence score** (how sure it is — 0% to 100%)

**Example:**
```
Citizen wrote: "Pothole on Shahrah-e-Faisal near Bahadurabad"
AI decided: Transport Department (Confidence: 95%)
```

**10 Departments the AI handles:**
| Department | Handles |
|------------|---------|
| Health Department | Hospitals, vaccinations, health issues |
| Education Department | Schools, teachers, education |
| Transport Department | Roads, potholes, traffic, transport |
| Revenue Department | Land, property, taxes, construction |
| Police Department | Security, fire, emergencies |
| Excise & Taxation | Vehicle, taxes, excise |
| Social Welfare | Social issues, welfare |
| Information Department | IT, internet, technology |
| Works & Services | Water, electricity, sanitation, street lights |
| Agriculture Department | Farming, agriculture |

If the AI confidence is low (below 60%), the system falls back to keyword matching as a backup — the ticket still gets assigned.

### Step 3: Minister Manages the Ticket
The minister sees all tickets on the admin dashboard and can:
- Assign tickets to departments
- Change status (Submitted → Assigned → In Progress → Resolved)
- Use filters (city, department, status, search)
- View analytics and reports

---

## AI Chatbot for the Minister

A powerful **AI chatbot** is built in for the minister that can do almost everything — it is not read-only, it can **make changes in the database**.

### Read Queries (Viewing Data)
The minister can ask:
- **"How many tickets are pending?"** → Shows real data
- **"How many tickets in Health Department?"** → Department-wise count
- **"How many tickets came today?"** → Today's summary
- **"How many tickets from Karachi?"** → City-wise filter
- **"Show me ticket SIT-20260714-0004"** → Full ticket info
- **"Any overdue tickets?"** → Escalated tickets
- **"What is the resolution time?"** → Average days to resolve

### Write Actions (Making Changes in the Database)
**Single Ticket Actions** (executed immediately):
- **"Assign ticket 4 to Transport Department"** → Ticket gets assigned
- **"Change ticket 5 status to resolved"** → Status changes

**Bulk Actions** (requires confirmation):
- **"Assign all submitted tickets to Transport Department"**
  - Bot shows preview: "This will affect 3 tickets. Confirm?"
  - Minister says "confirm" → All tickets get assigned
  - Minister says "cancel" → Nothing happens

**Valid Statuses:** submitted, assigned, in_progress, resolved, closed

### Example Conversations:
```
Minister: How many tickets are pending?
Bot: There are 6 tickets with status "submitted" waiting for assignment.

Minister: Assign ticket 4 to Transport Department
Bot: ✅ Ticket SIT-20260714-0004 assigned to Transport Department.

Minister: Move all submitted Karachi tickets to Works & Services
Bot: This will affect 4 tickets. Type "confirm" to proceed.
Minister: confirm
Bot: ✅ 4 tickets assigned to Works & Services Department.

Minister: Show me ticket SIT-20260714-0009
Bot: 📋 Ticket SIT-20260714-0009
     Subject: Fire broke out in Sukkur market area
     Status: assigned
     City: Sukkur
     Priority: critical
     Department: Police Department
```

---

## Citizen Chatbot

Citizens also have a chatbot that **helps file complaints** through conversation.

**How it works:**
1. Citizen says: "I want to file a complaint"
2. Bot asks: "What is the problem?"
3. Citizen describes the issue
4. Bot automatically:
   - Suggests a subject
   - Detects the city
   - Suggests a department (using AI)
   - Creates the ticket
   - Returns a ticket number (e.g., SIT-20260714-0005)

**Example:**
```
Citizen: I want to file a complaint about water supply
Bot: Sure! Please describe the issue in detail.
Citizen: No water in Gulshan-e-Iqbal Block 13-D for 3 days
Bot: I'll file this for you.
     Subject: No water supply for 3 days in Gulshan
     Department: Works & Services (Confidence: 92%)
     ✅ Ticket SIT-20260714-0005 created!
```

---

## Analytics Dashboard

The minister gets charts and graphs showing:
- **Status Distribution:** How many tickets are submitted, assigned, resolved
- **Priority Breakdown:** How many high, medium, low priority tickets
- **Department-wise:** Tickets per department
- **City-wise:** Tickets per city
- **Service-wise:** Tickets per service type
- **Timeline:** Ticket trend over time
- **Resolution Stats:** Average days to resolve

---

## Security Features

- **Encrypted Sessions:** Login cookies protected with Fernet encryption
- **CSRF Protection:** Every form has a CSRF token
- **Rate Limiting:** Login limited to 5 attempts per minute
- **Password Hashing:** Passwords encrypted with bcrypt
- **AI Department Routing:** Accurate department assignment using Groq LLM

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python FastAPI |
| Database | SQLite (async) |
| Templates | Jinja2 (server-side rendering) |
| AI | Groq API (LLaMA 3.1 8B Instant) |
| Authentication | Fernet encryption + bcrypt |
| Frontend | HTML/CSS/JS (vanilla) |
| Charts | Chart.js |
| Hosting | PM2 + Nginx + Let's Encrypt SSL |

---

## How to Run Locally

```bash
# 1. Clone the repo
git clone https://github.com/tahiralatif/Sindh-IT-ticket-system.git
cd Sindh-IT-ticket-system

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env and add your GROQ_API_KEY

# 5. Run the server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Visit: `http://localhost:8000`

---

## Default Accounts

| Role | Username | Password | Capabilities |
|------|----------|----------|-------------|
| Admin | admin | SindhIT-ZPEU0008 | Everything — manage tickets, chatbot, analytics |
| Citizen | fatima_test | demo1234 | Submit complaints, track, chatbot |
| Citizen | uzairlatif | password123 | Submit complaints, track, chatbot |

---

## Project Structure

```
Sindh-IT-ticket-system/
├── app/
│   ├── main.py              # Main server — routes, logic
│   ├── api/
│   │   └── chat.py          # AI Chatbot logic (citizen + admin)
│   ├── ai/
│   │   └── suggest.py       # AI department suggestion (Groq)
│   ├── core/
│   │   ├── config.py        # Settings, environment variables
│   │   ├── database.py      # Database connection
│   │   ├── models.py        # Database tables (User, Ticket, Department)
│   │   └── security.py      # Password hashing, session encryption
│   ├── middleware/
│   │   └── auth.py          # Login check, CSRF protection
│   ├── static/
│   │   ├── css/style.css    # Main stylesheet (Pakistan govt theme)
│   │   ├── css/chat.css     # Chatbot styles
│   │   └── js/chat.js       # Chatbot JavaScript
│   └── templates/           # HTML pages
│       ├── layout.html      # Common header/footer
│       ├── dashboard.html   # Admin dashboard
│       ├── admin_tickets.html # All tickets view
│       ├── submit.html      # Submit complaint form
│       ├── ticket_detail.html # Single ticket view
│       └── ...
├── sindh_tickets.db         # SQLite database
├── requirements.txt         # Python packages
├── ecosystem.config.js      # PM2 config
└── .env.example             # Environment template
```

---

## Key Features Summary

1. **AI Department Routing** — Complaints automatically go to the correct department
2. **Minister Chatbot** — Manage tickets using natural language commands
3. **Citizen Chatbot** — Conversational complaint filing
4. **Real-time Analytics** — Understand data through charts and graphs
5. **Multi-city Support** — Karachi, Hyderabad, Sukkur, Larkana, and more
6. **Priority Management** — From Low to Critical
7. **Status Tracking** — Full trail from Submit to Resolve
8. **Responsive Design** — Works on mobile and desktop
9. **Secure** — Encrypted sessions, CSRF protection, rate limiting
10. **File Attachments** — Attach photos/documents with complaints

---

## Team

- **Tahira Latif** — [GitHub](https://github.com/tahiralatif)
- **Mehwish** — Co-developer

**Live Demo:** [sindh-it-ticket.14.jugaar.ai](https://sindh-it-ticket.14.jugaar.ai)
