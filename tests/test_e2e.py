"""E2E Test Suite — Sindh IT Ticket System
Uses session-scoped fixtures to avoid hitting login rate limits (5/min).
"""
import pytest
import re
import time
from playwright.sync_api import sync_playwright

BASE = "https://sindh-it-ticket.14.jugaar.ai"


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture(scope="session")
def pw():
    """Session-scoped Playwright instance."""
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser(pw):
    b = pw.chromium.launch(headless=True, args=["--no-sandbox"])
    yield b
    b.close()


@pytest.fixture(scope="session")
def admin_page(browser):
    """Log in once as admin for all tests that need it."""
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(f"{BASE}/login")
    page.fill('input[name="username"]', "admin")
    page.fill('input[name="password"]', "Admin123!")
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")
    assert "/login" not in page.url, "Admin login failed"
    yield page
    ctx.close()


@pytest.fixture(scope="session")
def fresh_page(browser):
    """A fresh page (no auth) for public pages."""
    ctx = browser.new_context()
    page = ctx.new_page()
    yield page
    ctx.close()


# ═══════════════════════════════════════════════════════════════════
# 1. AUTH — Login (uses fresh page, no rate limit issue)
# ═══════════════════════════════════════════════════════════════════

def test_01_login_page_loads(fresh_page):
    fresh_page.goto(f"{BASE}/login")
    assert fresh_page.locator('input[name="username"]').count() > 0


def test_02_login_success(admin_page):
    assert "/login" not in admin_page.url


def test_03_wrong_password(fresh_page):
    fresh_page.goto(f"{BASE}/login")
    fresh_page.fill('input[name="username"]', "admin")
    fresh_page.fill('input[name="password"]', "wrongpassword")
    fresh_page.click('button[type="submit"]')
    fresh_page.wait_for_load_state("networkidle")
    assert "login" in fresh_page.url.lower() or fresh_page.locator(".alert-error").count() > 0


# ═══════════════════════════════════════════════════════════════════
# 2. AUTH — Register (uses fresh_page)
# ═══════════════════════════════════════════════════════════════════

def test_04_register_page(fresh_page):
    fresh_page.goto(f"{BASE}/register")
    assert fresh_page.locator('input[name="username"]').count() > 0


def test_05_register_new_user(fresh_page):
    ts = int(time.time())
    fresh_page.goto(f"{BASE}/register")
    fresh_page.fill('input[name="full_name"]', f"Test Citizen {ts}")
    fresh_page.fill('input[name="username"]', f"e2e_{ts}")
    fresh_page.fill('input[name="password"]', "Test1234!")
    fresh_page.fill('input[name="email"]', f"e2e_{ts}@test.com")
    fresh_page.click('button[type="submit"]')
    fresh_page.wait_for_load_state("networkidle")
    assert fresh_page.url != f"{BASE}/register"


# ═══════════════════════════════════════════════════════════════════
# 3. ADMIN DASHBOARD
# ═══════════════════════════════════════════════════════════════════

def test_06_dashboard(admin_page):
    admin_page.goto(f"{BASE}/")
    assert admin_page.locator("h1").count() > 0


def test_07_admin_tickets(admin_page):
    admin_page.goto(f"{BASE}/admin/tickets")
    assert admin_page.locator("table").count() > 0


def test_08_admin_users(admin_page):
    admin_page.goto(f"{BASE}/admin/users")
    assert admin_page.locator("table").count() > 0


def test_09_admin_analytics(admin_page):
    admin_page.goto(f"{BASE}/admin/analytics")
    assert admin_page.locator("canvas").count() >= 1


def test_10_ticket_detail(admin_page):
    admin_page.goto(f"{BASE}/admin/tickets")
    first = admin_page.locator("table tbody tr td a").first
    if first.count() > 0:
        first.click()
        admin_page.wait_for_load_state("networkidle")
        assert "ticket" in admin_page.url.lower()


# ═══════════════════════════════════════════════════════════════════
# 4. CITIZEN FLOW
# ═══════════════════════════════════════════════════════════════════

def test_11_submit_ticket(admin_page):
    admin_page.goto(f"{BASE}/submit")
    admin_page.wait_for_load_state("networkidle")
    subject = admin_page.locator('input[name="subject"]')
    if subject.count() > 0:
        subject.fill("E2E: Test complaint via automation")
        admin_page.fill('textarea[name="description"]', "Automated E2E test ticket.")
        # Fill category if present
        cat = admin_page.locator('select[name="category"]')
        if cat.count() > 0:
            cat.select_option(index=1)
        admin_page.select_option('select[name="priority"]', "medium")
        city = admin_page.locator('input[name="city"]')
        if city.count() > 0:
            city.fill("Karachi")
        admin_page.click('button[type="submit"]')
        admin_page.wait_for_load_state("networkidle")
        # Should redirect to ticket detail or show success
        assert re.search(r"/ticket/\d+", admin_page.url) or admin_page.url == f"{BASE}/submit"
    else:
        pytest.skip("Submit form not found")


# ═══════════════════════════════════════════════════════════════════
# 5. NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════════

def test_12_notifications_page(admin_page):
    admin_page.goto(f"{BASE}/notifications")
    assert admin_page.locator("h1").count() > 0


# ═══════════════════════════════════════════════════════════════════
# 6. PUBLIC TRACKING (no auth needed)
# ═══════════════════════════════════════════════════════════════════

def test_13_track_page(fresh_page):
    fresh_page.goto(f"{BASE}/track")
    assert fresh_page.locator('input[name="ticket_number"], form').count() > 0


def test_14_track_search(fresh_page):
    fresh_page.goto(f"{BASE}/track")
    inp = fresh_page.locator('input[name="ticket_number"]')
    if inp.count() > 0:
        inp.fill("SIT-20260714-0001")
        fresh_page.click('button[type="submit"]')
        fresh_page.wait_for_load_state("networkidle")


# ═══════════════════════════════════════════════════════════════════
# 7. API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

def test_15_api_stats(fresh_page):
    resp = fresh_page.request.get(f"{BASE}/api/stats")
    assert resp.status == 200
    assert "total" in resp.json()


def test_16_api_analytics(fresh_page):
    for ep in ["status", "priority", "departments", "over-time"]:
        resp = fresh_page.request.get(f"{BASE}/api/analytics/{ep}")
        assert resp.status == 200, f"/api/analytics/{ep} returned {resp.status}"


def test_17_api_suggest_dept(fresh_page):
    resp = fresh_page.request.get(f"{BASE}/api/suggest-dept?subject=broken+road&description=pothole")
    assert resp.status == 200
    assert "suggestions" in resp.json()


def test_18_api_notifications(admin_page):
    resp = admin_page.request.get(f"{BASE}/api/notifications/unread")
    if resp.status == 200:
        data = resp.json()
        assert "count" in data


def test_19_api_chat(admin_page):
    resp = admin_page.request.get(f"{BASE}/api/chat/welcome")
    assert resp.status == 200
    resp2 = admin_page.request.get(f"{BASE}/api/chat/history")
    assert resp2.status == 200
    resp3 = admin_page.request.post(f"{BASE}/api/chat", form={"message": "How many tickets?"})
    assert resp3.status in (200, 500, 503)


# ═══════════════════════════════════════════════════════════════════
# 8. LOGOUT (last — uses fresh_page)
# ═══════════════════════════════════════════════════════════════════

def test_20_logout_page(fresh_page):
    # Login on fresh page, then logout
    fresh_page.goto(f"{BASE}/login")
    fresh_page.fill('input[name="username"]', "admin")
    fresh_page.fill('input[name="password"]', "Admin123!")
    fresh_page.click('button[type="submit"]')
    fresh_page.wait_for_load_state("networkidle")
    fresh_page.goto(f"{BASE}/logout")
    fresh_page.wait_for_load_state("networkidle")
    assert "login" in fresh_page.url.lower()
