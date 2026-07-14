# 🇵🇰 Sindh IT Ticket System

> **Live:** [sindh-it-ticket.14.jugaar.ai](https://sindh-it-ticket.14.jugaar.ai)

## Ye Kya Hai?

Ye ek **government complaint management system** hai — Sindh Government ke liye banaya gaya hai. Citizens apni complaints (tickets) submit karte hain, aur system **AI ki madad se automatically** decide karta hai kaunsa department handle karega.

**Simple mein:** Citizen complaint kare → AI department assign kare → Admin manage kare → Problem solve ho.

---

## 🎯 System Kaise Kaam Karta Hai

### Step 1: Citizen Complaint Submit Karta Hai
Citizen apna complaint fill karta hai:
- **Subject:** Problem ka title (e.g., "Pothole on Shahrah-e-Faisal")
- **Description:** Detail mein problem explain karta hai
- **Category:** Complaint, Request, ya Emergency
- **Priority:** Low, Medium, High, Critical
- **City:** Karachi, Hyderabad, Sukkur, Larkana, etc.
- **Service:** Road Repair, Water Supply, Electricity, etc.

### Step 2: AI Automatically Department Assign Karta Hai 🤖
Jab citizen subject aur description likhta hai, **Groq AI (Llama 3.1 model)** automatically:

1. Complaint ko samajhta hai
2. 10 departments mein se sahi wala choose karta hai
3. **Confidence score** deta hai (kitna sure hai — 0% se 100%)

**Example:**
```
Citizen likha: "Pothole on Shahrah-e-Faisal near Bahadurabad"
AI ne decide kiya: Transport Department (Confidence: 95%)
```

**10 Departments jo AI handle karta hai:**
| Department | Kya Handle Karta Hai |
|------------|---------------------|
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

**Agar AI ko confidence kam lagta hai** (kam than 60%), to system keyword matching use karta hai as backup — phir bhi ticket assign hota hai.

### Step 3: Admin Ticket Manage Karta Hai
Admin dashboard pe sab tickets dikhte hain. Wo:
- Tickets ko departments mein assign kar sakta hai
- Status change kar sakta hai (Submitted → Assigned → In Progress → Resolved)
- Filters use kar sakta hai (city, department, status, search)
- Analytics dekh sakta hai

---

## 🤖 Admin Chatbot — Ye Sabse Powerful Feature Hai

Admin ke liye ek **AI chatbot** hai jo almost sab kuch kar sakta hai. Ye sirf read-only nahi hai — ye **database mein changes bhi kar sakta hai**.

### Chatbot Kya Kya Kar Sakta Hai:

#### 📊 Read Queries (Data Dekhna)
Admin puch sakta hai:
- **"Kitne tickets pending hain?"** → Real data dikhata hai
- **"Health Department mein kitne tickets hain?"** → Department-wise count
- **"Aaj kitne tickets aaye?"** → Today's summary
- **"Karachi se kitne tickets hain?"** → City-wise filter
- **"SIT-20260714-0004 ki detail dikhao"** → Full ticket info
- **"Koi overdue tickets hain?"** → Escalated tickets
- **"Resolution time kya hai?"** → Average days to resolve

#### ✏️ Write Actions (Database Mein Changes)
**Single Ticket Actions** (instant execute hota hai):
- **"Assign ticket 4 to Transport Department"** → Ticket assign ho jata hai
- **"Change ticket 5 status to resolved"** → Status change ho jata hai

**Bulk Actions** (confirmation mangta hai):
- **"Saare submitted tickets ko Transport mein assign karo"**
  - Bot pehle dikhata hai: "Ye 3 tickets affect hongi. Confirm karein?"
  - Admin "confirm" bole → Sab tickets assign ho jaate hain
  - Admin "cancel" bole → Kuch nahi hota

**Valid Statuses:** submitted, assigned, in_progress, resolved, closed

### Chatbot Example Conversations:

```
Admin: How many tickets are pending?
Bot: There are 6 tickets with status "submitted" waiting for assignment.

Admin: Assign ticket 4 to Transport Department
Bot: ✅ Ticket SIT-20260714-0004 assigned to Transport Department.

Admin: Move all submitted Karachi tickets to Works & Services
Bot: This will affect 4 tickets. Type "confirm" to proceed.
Admin: confirm
Bot: ✅ 4 tickets assigned to Works & Services Department.

Admin: Show me ticket SIT-20260714-0009
Bot: 📋 Ticket SIT-20260714-0009
     Subject: Fire broke out in Sukkur market area
     Status: assigned
     City: Sukkur
     Priority: critical
     Department: Police Department
```

---

## 👥 Citizen Chatbot

Citizens ke liye bhi ek chatbot hai jo **complaint file karne mein help** karta hai.

**Ye kaise kaam karta hai:**
1. Citizen bole: "I want to file a complaint"
2. Bot poochta hai: "Kya problem hai?"
3. Citizen describe karta hai
4. Bot automatically:
   - Subject suggest karta hai
   - City detect karta hai
   - Department suggest karta hai (AI se)
   - Ticket create karta hai
   - Ticket number deta hai (e.g., SIT-20260714-0005)

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

## 📊 Analytics Dashboard

Admin ko charts aur graphs dikhte hain:
- **Status Distribution:** Kitne tickets submitted, assigned, resolved
- **Priority Breakdown:** Kitne high, medium, low priority
- **Department-wise:** Har department mein kitne tickets
- **City-wise:** Har city se kitne tickets
- **Service-wise:** Har service type ke kitne tickets
- **Timeline:** Time ke saath tickets ka trend
- **Resolution Stats:** Average kitne din mein resolve hota hai

---

## 🔒 Security Features

- **Encrypted Sessions:** Login cookies Fernet encryption se protected hain
- **CSRF Protection:** Har form mein CSRF token hota hai
- **Rate Limiting:** Login pe 5 attempts per minute ka limit
- **Password Hashing:** bcrypt se passwords encrypted hain
- **AI Department Routing:** Groq LLM se accurate department assignment

---

## 🛠️ Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python FastAPI |
| Database | SQLite (async) |
| Templates | Jinja2 (server-side rendering) |
| AI | Groq API (Llama 3.1 8B Instant) |
| Authentication | Fernet encryption + bcrypt |
| Frontend | HTML/CSS/JS (vanilla) |
| Charts | Chart.js |
| Hosting | PM2 + Nginx + Let's Encrypt SSL |

---

## 🚀 How to Run Locally

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

## 📝 Default Accounts

| Role | Username | Password | Kya Kar Sakta Hai |
|------|----------|----------|-------------------|
| Admin | admin | SindhIT-ZPEU0008 | Sab kuch — tickets manage, chatbot, analytics |
| Citizen | fatima_test | demo1234 | Complaint submit, track, chatbot |
| Citizen | uzairlatif | password123 | Complaint submit, track, chatbot |

---

## 📁 Project Structure

```
Sindh-IT-ticket-system/
├── app/
│   ├── main.py              # Main server — routes, logic
│   ├── api/
│   │   ├── chat.py          # AI Chatbot logic (citizen + admin)
│   │   └── chat.py          # Chat endpoints
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

## 🎯 Key Features Summary

1. **AI Department Routing** — Complaint automatically sahi department mein jaata hai
2. **Admin Chatbot** — Natural language mein tickets manage karo
3. **Citizen Chatbot** — Conversational complaint filing
4. **Real-time Analytics** — Charts aur graphs se data samjho
5. **Multi-city Support** — Karachi, Hyderabad, Sukkur, Larkana, etc.
6. **Priority Management** — Low se lekar Critical tak
7. **Status Tracking** — Submit se lekar Resolve tak ka full trail
8. **Responsive Design** — Mobile aur desktop dono pe kaam karta hai
9. **Secure** — Encrypted sessions, CSRF protection, rate limiting
10. **File Attachments** — Complaint ke saath photos/documents attach karo

---

## 📞 Contact

**Tahira Latif** — [GitHub](https://github.com/tahiralatif)

**Live Demo:** [sindh-it-ticket.14.jugaar.ai](https://sindh-it-ticket.14.jugaar.ai)
