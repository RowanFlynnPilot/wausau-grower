#!/usr/bin/env python3
"""Regression suite for The Wausau Grower.

Serves the repository on a local port, drives index.html in headless Chromium
with Playwright, and exits non-zero on any broken behavior, WCAG contrast
regression, or console error. The National Weather Service and Google Fonts
are stubbed, so the suite runs offline and can exercise failure paths; the
clock is pinned so date-driven copy is testable in every season.

    pip install playwright
    python -m playwright install chromium
    python tests/smoke_test.py            # --headed to watch it run
"""
import http.server
import json
import re
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
AUDIT_JS = (ROOT / "tests" / "contrast-audit.js").read_text(encoding="utf-8")
TZ = "America/Chicago"


def central(y, m, d, hour=11):
    """A wall-clock time in Wausau, as an aware UTC datetime (17:00Z is 11:00 or 12:00 Central)."""
    return datetime(y, m, d, hour + 6, 0, tzinfo=timezone.utc)


FIXED = central(2026, 9, 24)            # in season, peony window open, spinach windows past


# ---------------------------------------------------------------- harness
class Suite:
    def __init__(self):
        self.passed, self.fails = 0, []

    def check(self, cond, msg):
        if cond:
            self.passed += 1
        else:
            self.fails.append(msg)
            print("    FAIL", msg)

    def no_errors(self, errors, where, allow=()):
        bad = [e for e in errors if not any(a in e for a in allow)]
        self.check(not bad, f"{where}: console clean ({'; '.join(bad)[:300]})")


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def log_message(self, *a):
        pass


def serve():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def forecast_fixture(now, low):
    start = now.replace(minute=0, second=0, microsecond=0)
    periods = []
    for i in range(14):
        s = start + timedelta(hours=12 * i)
        day = i % 2 == 0
        periods.append({
            "number": i + 1, "name": f"{'Day' if day else 'Night'} {i // 2 + 1}", "isDaytime": day,
            "temperature": 70 if day else (low if i == 1 else 55), "temperatureUnit": "F",
            "shortForecast": "Sunny" if day else "Clear",
            "probabilityOfPrecipitation": {"value": 40 if i == 2 else 0},
            "startTime": s.isoformat(), "endTime": (s + timedelta(hours=12)).isoformat(),
        })
    return {"properties": {"updateTime": now.isoformat(), "periods": periods}}


def stub_network(page, now, nws="ok", low=48):
    calls = {"points": 0, "forecast": 0, "mode": nws}
    page.route("https://fonts.googleapis.com/**", lambda r: r.fulfill(status=200, content_type="text/css", body=""))

    def points(route):
        calls["points"] += 1
        if calls["mode"] == "fail":
            return route.fulfill(status=500, body="{}")
        route.fulfill(status=200, content_type="application/geo+json",
                      body=json.dumps({"properties": {"forecast": "https://api.weather.gov/gridpoints/GRB/1,1/forecast"}}))

    def forecast(route):
        calls["forecast"] += 1
        if calls["mode"] == "fail":
            return route.fulfill(status=500, body="{}")
        route.fulfill(status=200, content_type="application/geo+json", body=json.dumps(forecast_fixture(now, low)))

    page.route("https://api.weather.gov/points/**", points)
    page.route("https://api.weather.gov/gridpoints/**", forecast)
    return calls


def open_page(browser, base, path="/index.html", scheme="light", when=FIXED, nws="ok", low=48,
              viewport=None, mobile=False, storage=None):
    ctx = browser.new_context(color_scheme=scheme, timezone_id=TZ, is_mobile=mobile, has_touch=mobile,
                              viewport=viewport or {"width": 1280, "height": 900}, accept_downloads=True)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console.error: {m.text}") if m.type == "error" else None)
    page.clock.set_fixed_time(when)
    calls = stub_network(page, when, nws, low)
    if storage:
        page.add_init_script(
            "if (!sessionStorage.getItem('__seeded')) { sessionStorage.setItem('__seeded', '1');"
            f" Object.entries({json.dumps(storage)}).forEach(([k, v]) => localStorage.setItem(k, v)); }}")
    page.goto(base + path)
    page.wait_for_function("() => document.querySelectorAll('.pcard').length > 0")
    page.wait_for_function("() => document.querySelector('#wx-forecast .wx-grid, #wx-forecast .wx-error')")
    return ctx, page, errors, calls


def audit(page, scope):
    page.evaluate(AUDIT_JS)
    return page.evaluate("s => __contrastAudit(s)", scope)


def fmt_fails(fails):
    seen = {}
    for f in fails:
        seen.setdefault(f"{f['el']} '{f['text']}' {f['ratio']}:1 (needs {f['need']})", 0)
    return "; ".join(list(seen)[:6])


# ---------------------------------------------------------------- tests
def test_boot(s, browser, base):
    ctx, page, errors, calls = open_page(browser, base)
    q = page.evaluate
    s.check(q("document.querySelectorAll('.pcard').length") == 31, "boot: 31 plant cards")
    s.check(q("document.querySelectorAll('.cal-row').length") == 31, "boot: 31 calendar rows")
    s.check(q("document.querySelectorAll('.tab').length") == 5, "boot: 5 tabs")
    s.check(q("document.querySelectorAll('.post').length") == 6, "boot: 6 demo posts in prototype mode")
    s.check(q("document.getElementById('dateline-date').textContent") == "Thursday, September 24, 2026", "boot: dateline")
    s.check("Day 133 of the frost-free season" in q("document.getElementById('season-pulse').textContent"), "boot: season pulse")
    s.check(q("document.querySelectorAll('.wx-card').length") == 14, "boot: 14 forecast periods")
    s.check(q("document.getElementById('kicker-proto').hidden") is False, "boot: prototype label shown")
    s.no_errors(errors, "boot")
    ctx.close()


def test_contrast(s, browser, base):
    for scheme in ("light", "dark"):
        ctx, page, errors, _ = open_page(browser, base, scheme=scheme, low=34,
                                         storage={"wg:visit": json.dumps((FIXED - timedelta(days=30)).isoformat())})
        fails = []
        for panel in ("calendar", "guides", "weather", "community", "ask"):
            page.evaluate("id => showPanel(id, false)", panel)
            fails += audit(page, "main")
        fails += audit(page, "header") + audit(page, "footer")
        page.evaluate("() => { calView = 'list'; renderCalendar(); showPanel('calendar', false); }")
        fails += audit(page, "#calendar")
        page.evaluate("() => { calView = 'chart'; renderCalendar(); openModal('broccoli'); }")
        fails += audit(page, "#modal")
        page.evaluate("() => { closeModalUI(); document.getElementById('share-btn').click(); }")
        fails += audit(page, "#modal")
        page.evaluate("() => { closeModalUI(); CONFIG.sponsorUpsell = true; renderMastSponsor(); renderAsk(); showPanel('ask', false); }")
        fails += audit(page, "header") + audit(page, "#ask")
        s.check(page.evaluate("!!document.querySelector('.welcome')"), f"contrast/{scheme}: welcome strip rendered for a returning reader")
        s.check(not fails, f"contrast/{scheme}: all text meets WCAG AA ({fmt_fails(fails)})")
        s.no_errors(errors, f"contrast/{scheme}")
        ctx.close()
    # sponsor preview mode: ribbon + lockups
    for scheme in ("light", "dark"):
        ctx, page, errors, _ = open_page(browser, base, path="/index.html?demo=Test%20Garden%20Club", scheme=scheme)
        page.evaluate("() => showPanel('ask', false)")
        fails = audit(page, "body")
        s.check(page.evaluate("document.querySelector('.lockup .name').textContent") == "Test Garden Club", f"demo/{scheme}: lockup shows the preview name")
        s.check(not fails, f"demo/{scheme}: preview mode meets WCAG AA ({fmt_fails(fails)})")
        s.no_errors(errors, f"demo/{scheme}")
        ctx.close()


def test_tabs_history_modal(s, browser, base):
    ctx, page, errors, _ = open_page(browser, base)
    for tab in ("guides", "weather", "community", "ask", "calendar"):
        page.click(f"#tab-{tab}")
        s.check(page.evaluate("document.querySelector('.panel.active').id") == tab and page.url.endswith("#" + tab), f"tabs: {tab} panel + hash")
    page.focus("#tab-calendar")
    page.keyboard.press("End")
    s.check(page.evaluate("document.activeElement.id") == "tab-ask" and page.evaluate("currentPanel") == "ask", "tabs: End key moves to the last tab")
    page.keyboard.press("Home")
    s.check(page.evaluate("currentPanel") == "calendar", "tabs: Home key moves to the first tab")
    page.keyboard.press("ArrowRight")
    s.check(page.evaluate("currentPanel") == "guides", "tabs: ArrowRight")

    page.click('.cardbtn[data-open="kale"]')
    s.check(page.evaluate("isModalOpen()") and page.url.endswith("#plant/kale"), "modal: opens with a deep link")
    s.check(page.title().startswith("Kale"), "modal: document title names the plant")
    s.check(page.evaluate("document.querySelector('main').inert"), "modal: background is inert")
    page.evaluate("history.back()")
    page.wait_for_function("() => !isModalOpen()")
    s.check(page.evaluate("currentPanel") == "guides" and page.url.startswith(base), "modal: Back closes the guide and stays on the page")
    s.check(page.title() == "The Wausau Grower — Wausau Pilot & Review", "modal: title restored")

    page.click('.cardbtn[data-open="kale"]')
    page.click("#modal-close")
    page.wait_for_function("() => !isModalOpen()")
    s.check(not page.url.endswith("#plant/kale") and page.evaluate("document.activeElement.dataset.open") == "kale", "modal: close button closes it and returns focus to the opener")

    page.click('.cardbtn[data-open="tomato"]')
    page.keyboard.press("Escape")
    page.wait_for_function("() => !isModalOpen()")
    s.check(True, "modal: Escape closes it")

    page.click('.cardbtn[data-open="tomato"]')
    page.click(".rel-chip")
    s.check(page.evaluate("isModalOpen()") and not page.url.endswith("#plant/tomato"), "modal: related chip navigates inside the dialog")
    page.click("[data-ask]")
    s.check(page.evaluate("currentPanel") == "ask" and not page.evaluate("isModalOpen()"), "modal: ask link switches to Ask a Gardener")
    s.check(page.evaluate("document.getElementById('ask-q').value").endswith(": "), "modal: ask link pre-fills the question")
    page.evaluate("history.back()")
    page.wait_for_function("() => currentPanel === 'guides'")
    s.check(True, "history: Back from the ask handoff returns to the guides")

    page.goto(base + "/index.html?deeplink=1#plant/garlic")
    page.wait_for_function("() => isModalOpen()")
    s.check(page.evaluate("document.getElementById('modal-title').textContent") == "Garlic", "deep link: #plant/garlic opens on load")
    page.click("#modal-close")
    page.wait_for_function("() => !isModalOpen()")
    s.check(page.url.endswith("#guides"), "deep link: closing leaves #guides")
    s.no_errors(errors, "tabs/history/modal")
    ctx.close()


def test_calendar(s, browser, base):
    ctx, page, errors, _ = open_page(browser, base)
    page.click('#cal-filters .fbtn[data-cat="herb"]')
    s.check(page.locator(".cal-row").count() == 4, "calendar: herb filter")
    page.click('#cal-filters .fbtn[data-view="list"]')
    s.check(page.locator(".cal-trow").count() == 4, "calendar: list view")
    page.click('#cal-filters .fbtn[data-view="chart"]')
    page.click('#cal-filters .fbtn[data-cat="all"]')
    s.check(page.locator(".cal-row .mband").count() == 31 * 4, "calendar: month bands on every row")
    summary = page.evaluate("document.querySelector('.cal-track .sr-only').textContent")
    s.check(summary.startswith("Start seeds indoors Apr 10 to Apr 25"), f"calendar: screen-reader summary per row ({summary[:50]})")
    page.hover('.cal-bar[data-plant="tomato"]')
    s.check(page.evaluate("getComputedStyle(tip).display") == "block" and "Tomato" in page.evaluate("tip.textContent"), "calendar: bar tooltip")
    page.click('.cal-bar[data-plant="peony"]')
    s.check(page.evaluate("document.getElementById('modal-title').textContent") == "Peony", "calendar: bar click opens the guide")
    page.keyboard.press("Escape")
    page.focus('.cal-name[data-open="dill"]')
    page.keyboard.press("Enter")
    s.check(page.evaluate("document.getElementById('modal-title').textContent") == "Dill", "calendar: Enter on a plant name opens it")
    page.keyboard.press("Escape")
    s.check(page.evaluate("document.querySelector('.tabs').scrollHeight <= document.querySelector('.tabs').clientHeight"), "layout: tab strip has no vertical overflow (Windows scrollbar artifact)")
    s.no_errors(errors, "calendar")
    ctx.close()


def test_guides_favorites_notes(s, browser, base):
    ctx, page, errors, _ = open_page(browser, base)
    page.click("#tab-guides")
    page.fill("#guide-search", "pickle")
    s.check(page.locator(".pcard").count() == 1 and page.evaluate("document.querySelector('.pcard').dataset.open") == "dill", "guides: search reaches tips")
    s.check(page.evaluate("document.getElementById('guide-count').textContent") == "1 match", "guides: result count")
    page.fill("#guide-search", "")
    page.focus('.star[data-fav="basil"]')
    page.keyboard.press("Enter")
    s.check(page.evaluate("document.activeElement.dataset.fav") == "basil", "guides: focus stays on the star after the grid re-renders")
    s.check(page.evaluate("document.activeElement.getAttribute('aria-pressed')") == "true", "guides: star exposes aria-pressed")
    s.check("basil" in json.loads(page.evaluate("localStorage.getItem('wg:favs')")), "guides: favorite persisted")
    s.check("(1)" in page.evaluate("document.querySelector('#guide-filters .fbtn[data-cat=fav]').textContent"), "guides: favorite count")
    page.click('.cardbtn[data-open="basil"]')
    s.check(page.evaluate("document.getElementById('modal-star').getAttribute('aria-pressed')") == "true", "modal: save button reflects the star")
    page.fill("#plant-note", "Genovese by the south fence")
    page.keyboard.press("Escape")
    page.reload()
    page.wait_for_function("() => document.querySelectorAll('.pcard').length === 31")
    page.evaluate("() => openModal('basil', false)")
    s.check(page.evaluate("document.getElementById('plant-note').value") == "Genovese by the south fence", "modal: notes persist across reloads")
    s.check(page.evaluate("document.querySelector('.star[data-fav=basil]').classList.contains('on')"), "guides: star persists across reloads")
    s.no_errors(errors, "guides")
    ctx.close()


def test_weather(s, browser, base):
    ctx, page, errors, calls = open_page(browser, base, low=34)
    page.click("#tab-weather")
    s.check(page.locator(".wx-card.frosty").count() == 1, "weather: frost-risk night flagged")
    s.check(page.evaluate("document.querySelector('.wx-status h3').textContent") == "Frost watch — protect and pick", "weather: September frost advice")
    s.check(page.locator(".wx-card .cond.rain").count() == 1, "weather: rain chance shown")
    s.check("peony" in page.evaluate("document.getElementById('wx-windows').textContent").lower(), "weather: open planting windows listed")
    first_points = calls["points"]
    page.reload()
    page.wait_for_function("() => document.querySelectorAll('.wx-card').length === 14")
    s.check(calls["points"] == first_points, "weather: cached forecast URL skips the /points lookup")
    calls["mode"] = "fail"
    page.reload()
    page.wait_for_function("() => document.querySelector('.wx-note, .wx-error')")
    s.check(page.locator(".wx-note").count() == 1 and page.locator(".wx-card").count() == 14, "weather: falls back to the last forecast when NWS fails")
    page.evaluate("() => localStorage.removeItem('wg:nws-last')")
    page.reload()
    page.click("#tab-weather")
    page.wait_for_function("() => document.querySelector('.wx-error')")
    s.check(page.locator("#wx-retry").count() == 1, "weather: error state offers a retry")
    calls["mode"] = "ok"
    page.click("#wx-retry")
    page.wait_for_function("() => document.querySelectorAll('.wx-card').length === 14")
    s.check(True, "weather: retry recovers")
    s.no_errors(errors, "weather", allow=("Failed to load resource",))
    ctx.close()


SEASONS = [
    (central(2027, 1, 15), 48, "The garden is asleep", "time to order seeds"),
    (central(2027, 3, 20), 48, "Seed-starting season", "seed-starting season"),
    (central(2027, 4, 20), 48, "Early season — hardy crops only", "until Wausau's average last frost"),
    (central(2027, 5, 20), 50, "Clear to plant frost-tender crops", "Day 6 of the frost-free season"),
    (central(2027, 5, 20), 34, "Frost risk in the forecast — hold off", "Day 6 of the frost-free season"),
    (central(2027, 7, 20), 34, "Unseasonable frost risk", "frost-free season"),
    (central(2026, 10, 10), 48, "Late season — harvest and prep for frost", "garlic, bulbs"),
    (central(2026, 11, 10), 48, "The garden is asleep", "until next spring's average last frost"),
]


def test_seasons(s, browser, base):
    for when, low, head, pulse in SEASONS:
        tag = when.strftime("%b %d %Y") + f" low {low}"
        ctx, page, errors, _ = open_page(browser, base, when=when, low=low)
        got_head = page.evaluate("document.querySelector('.wx-status h3').textContent")
        got_pulse = page.evaluate("document.getElementById('season-pulse').textContent")
        s.check(got_head == head, f"seasons/{tag}: advice '{got_head}'")
        s.check(pulse in got_pulse, f"seasons/{tag}: pulse '{got_pulse}'")
        month = when.month
        if month in (1, 11):
            s.check("First up next spring" in page.evaluate("document.getElementById('wx-windows').textContent"), f"seasons/{tag}: off-season windows")
            s.check(page.evaluate("getComputedStyle(document.getElementById('legend-today')).display") == "none", f"seasons/{tag}: no today marker")
        if month == 3:
            body = page.evaluate("document.querySelector('.wx-status p').textContent")
            s.check("petunia" in body and "parsley" in body, f"seasons/{tag}: indoor starts named from the database")
        if month == 1:
            s.check("Not yet" in page.evaluate("document.getElementById('wx-tiles').textContent"), f"seasons/{tag}: pre-season tiles")
        if month in (10, 11):
            s.check("Done" in page.evaluate("document.getElementById('wx-tiles').textContent"), f"seasons/{tag}: post-season tiles")
        s.no_errors(errors, f"seasons/{tag}")
        ctx.close()


def test_tasks(s, browser, base):
    ctx, page, errors, _ = open_page(browser, base)
    page.click("#tab-weather")
    page.click("#wx-tasks .task >> nth=0")
    deco = page.evaluate("getComputedStyle(document.querySelector('#wx-tasks .task.done > span')).textDecorationLine")
    s.check("line-through" in deco, "tasks: a checked task is struck through")
    s.check(page.evaluate("document.getElementById('task-count').textContent") == "1 of 3 done", "tasks: count")
    page.reload()
    page.wait_for_function("() => document.querySelector('#wx-tasks input')")
    s.check(page.evaluate("document.querySelector('#wx-tasks input').checked"), "tasks: checked state persists")
    s.no_errors(errors, "tasks")
    ctx.close()


def test_reminders(s, browser, base):
    ctx, page, errors, _ = open_page(browser, base)
    page.evaluate("() => { FAVS.add('spinach'); FAVS.add('peony'); saveFavs(); }")
    with page.expect_download() as dl:
        page.click("#ics-btn")
    download = dl.value
    s.check(download.suggested_filename == "wausau-grower-my-plants.ics", "reminders: download named")
    raw = Path(download.path()).read_bytes()
    text = raw.decode("utf-8")
    uids = re.findall(r"UID:([^\r\n]+)", text)
    s.check(len(uids) == len(set(uids)), f"reminders: every event has its own UID ({uids})")
    starts = dict(zip(uids, re.findall(r"DTSTART;VALUE=DATE:(\d{8})", text)))
    s.check(starts.get("wg-spinach-0-2027@wausaupilotandreview.com") == "20270415"
            and starts.get("wg-spinach-2-2027@wausaupilotandreview.com") == "20270815", "reminders: both spinach sowings kept")
    s.check(starts.get("wg-peony-0-closes-2026@wausaupilotandreview.com") == "20261012", "reminders: nudge before an open window closes")
    s.check(starts.get("wg-peony-0-2027@wausaupilotandreview.com") == "20270915", "reminders: open window's next opening")
    s.check(starts.get("wg-last-frost-2027@wausaupilotandreview.com") == "20270515"
            and starts.get("wg-first-frost-2026@wausaupilotandreview.com") == "20261001", "reminders: frost dates")
    lines = text.split("\r\n")
    s.check(all(len(l.encode("utf-8")) <= 75 for l in lines) and "\n" not in text.replace("\r\n", ""), "reminders: RFC 5545 folding + CRLF")
    s.no_errors(errors, "reminders")
    ctx.close()


def test_community(s, browser, base):
    ctx, page, errors, _ = open_page(browser, base)
    page.click("#tab-community")
    like = page.locator('.likebtn[data-like="p1"]')
    before = int(like.locator(".likes").text_content())
    like.click()
    s.check(like.get_attribute("aria-pressed") == "true" and int(like.locator(".likes").text_content()) == before + 1, "community: like toggles on")
    like.click()
    s.check(int(like.locator(".likes").text_content()) == before, "community: like toggles off")
    page.locator('.cform[data-post="p1"] input').fill("Lovely!")
    page.locator('.cform[data-post="p1"] button').click()
    page.click("#share-btn")
    form = page.locator("#share-form")
    form.get_by_label("Your name").fill("Pat M.")
    form.get_by_label("Neighborhood").fill("Weston")
    form.get_by_label("Caption").fill("First dahlias of the year.")
    form.locator("button[type=submit]").click()
    s.check(page.locator(".post").count() == 7 and page.evaluate("document.querySelector('.post .who').textContent") == "Pat M.", "community: shared post appears")
    s.check("Lovely!" in page.locator('.post[data-post="p1"] .comments').text_content(), "community: comments survive a re-render")
    page.click("#share-btn")
    page.evaluate("() => { document.getElementById('sh-hp').value = 'spam'; }")
    form.get_by_label("Your name").fill("Bot")
    form.get_by_label("Neighborhood").fill("x")
    form.get_by_label("Caption").fill("buy now")
    form.locator("button[type=submit]").click()
    s.check(page.locator(".post").count() == 7, "community: honeypot drops bot submissions")
    s.no_errors(errors, "community")
    ctx.close()


def test_ask(s, browser, base):
    ctx, page, errors, _ = open_page(browser, base)
    page.click("#tab-ask")
    page.fill("#ask-q", "Why are my tomato leaves curling?")
    page.fill("#ask-name", "Pat M.")
    page.fill("#ask-email", "pat@example.org")
    page.click("#ask-form button[type=submit]")
    s.check(page.locator("#ask-form .form-msg.ok").count() == 1, "ask: prototype submission confirms")
    page.click("#ask-again")
    s.check(page.evaluate("document.activeElement.id") == "ask-q", "ask: 'Ask another question' restores the form")

    posted = []
    cors = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Allow-Methods": "POST"}

    def endpoint(route):
        if route.request.method == "POST":
            posted.append(route.request.post_data_json)
        route.fulfill(status=200, headers=cors, content_type="application/json", body="{}")
    page.route("https://example.test/ask", endpoint)
    page.evaluate("() => { CONFIG.askEndpoint = 'https://example.test/ask'; renderAsk(); }")
    page.fill("#ask-q", "When do I plant garlic?")
    page.fill("#ask-name", "Pat M.")
    page.fill("#ask-email", "pat@example.org")
    page.click("#ask-form button[type=submit]")
    page.wait_for_function("() => document.querySelector('#ask-form .form-msg.ok')")
    body = posted[0] if posted else {}
    s.check(body.get("question") == "When do I plant garlic?" and body.get("source") == "wausau-grower" and "submittedAt" in body, f"ask: endpoint receives the question with metadata ({list(body)})")
    page.click("#ask-again")
    page.evaluate("() => { document.getElementById('ask-hp').value = 'spam'; }")
    page.fill("#ask-q", "spam")
    page.fill("#ask-name", "Bot")
    page.fill("#ask-email", "bot@example.org")
    page.click("#ask-form button[type=submit]")
    s.check(len(posted) == 1, "ask: honeypot never reaches the endpoint")

    page.click("#ask-again")
    page.evaluate("""() => {
      CONFIG.askEndpoint = null; CONFIG.askEmail = 'help@example.org'; renderAsk();
      window.__mailto = null;
      const orig = HTMLAnchorElement.prototype.click;
      HTMLAnchorElement.prototype.click = function () { if (this.href.startsWith('mailto:')) { window.__mailto = this.href; return; } return orig.call(this); };
    }""")
    page.fill("#ask-q", "Hostas and deer?")
    page.fill("#ask-name", "Pat M.")
    page.fill("#ask-email", "pat@example.org")
    page.click("#ask-form button[type=submit]")
    mailto = page.evaluate("window.__mailto") or ""
    s.check(mailto.startswith("mailto:help@example.org?subject=") and "Hostas%20and%20deer" in mailto, "ask: email mode opens a pre-filled message")
    s.check(page.locator("#ask-copy").count() == 1, "ask: email mode offers a copy fallback")

    page.evaluate("() => { CONFIG.askEmail = null; CONFIG.prototype = false; renderAsk(); }")
    s.check("Questions open soon" in page.locator("#ask-form").text_content(), "ask: launch mode with nowhere to send hides the form")
    s.no_errors(errors, "ask")
    ctx.close()


def test_launch_mode(s, browser, base):
    ctx, page, errors, _ = open_page(browser, base)
    page.evaluate("() => { CONFIG.prototype = false; applyPrototype(); renderPosts(); }")
    q = page.evaluate
    s.check(q("document.getElementById('kicker-proto').hidden") and q("document.getElementById('demo-note').hidden"), "launch: prototype labels hidden")
    s.check(q("document.getElementById('share-btn').hidden"), "launch: share button hidden with no submitEndpoint")
    s.check(q("document.querySelectorAll('.post').length") == 0 and "be the first" in q("document.getElementById('posts').textContent"), "launch: demo posts never shown as real")
    s.check(q("document.getElementById('foot-about').textContent") == "The Wausau Grower is a reader tool from Wausau Pilot & Review.", "launch: footer copy")
    s.no_errors(errors, "launch")
    ctx.close()


def test_storage_tamper(s, browser, base):
    bad = {"wg:favs": "5", "wg:likes": '{"a":1}', "wg:note:tomato": "[1,2]", "wg:tasks:2026-9": '"oops"',
           "wg:visit": '"not-a-date"', "wg:nws-url": '{"url":"javascript:alert(1)","at":1}',
           "wg:nws-last": '{"periods":"x"}', "wg:hint-star": "{{{"}
    ctx, page, errors, calls = open_page(browser, base, storage=bad)
    s.check(page.evaluate("document.querySelectorAll('.pcard').length") == 31, "tamper: page renders with corrupted storage")
    s.check(page.evaluate("document.querySelectorAll('.wx-card').length") == 14 and calls["points"] >= 1, "tamper: bad cached forecast URL is ignored")
    page.evaluate("() => openModal('tomato', false)")
    s.check(page.evaluate("document.getElementById('plant-note').value") == "", "tamper: non-string note ignored")
    s.no_errors(errors, "tamper")
    ctx.close()


def test_embedded(s, browser, base):
    ctx = browser.new_context(color_scheme="dark", timezone_id=TZ, viewport={"width": 1100, "height": 900})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console.error: {m.text}") if m.type == "error" else None)
    page.clock.set_fixed_time(FIXED)
    stub_network(page, FIXED)
    page.set_content(f"""<!doctype html><html><body style="margin:0;background:#fff;font-family:sans-serif">
      <h1>Ask a Master Gardener</h1><p style="height:400px">Article text.</p>
      <iframe id="wausau-grower" src="{base}/index.html" style="width:100%;height:900px;border:0"
              allow="clipboard-write; web-share"></iframe><p>After the embed.</p>
      <script>
        window.__msgs = [];
        addEventListener('message', e => {{
          if (!e.data || e.data.id !== 'wausau-grower') return;
          __msgs.push(e.data);
          if (e.data.type === 'wpr-embed-height') document.getElementById('wausau-grower').style.height = e.data.height + 'px';
        }});
      </script></body></html>""")
    page.wait_for_function("() => __msgs.some(m => m.type === 'wpr-embed-height')")
    frame = next(f for f in page.frames if f.url.startswith(base))
    frame.wait_for_function("() => document.querySelectorAll('.pcard').length === 31")
    s.check(frame.evaluate("document.documentElement.classList.contains('force-light')"), "embed: forced light inside a light host")
    s.check(frame.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(247, 247, 247)", "embed: light palette despite a dark OS")
    s.check(frame.evaluate("getComputedStyle(document.getElementById('bookmark-btn')).display") == "none", "embed: bookmark button hidden")
    frame.click("#tab-guides")
    page.wait_for_function("() => parseInt(document.getElementById('wausau-grower').style.height) > 2000")
    star = frame.locator('.star[data-fav="lilac"]')
    star.click()
    star_top = frame.evaluate("document.querySelector('.star[data-fav=lilac]').getBoundingClientRect().top + scrollY")
    toast_top = frame.evaluate("document.getElementById('toast').getBoundingClientRect().top + scrollY")
    s.check(0 < toast_top - star_top < 160, f"embed: toast appears beside the control (star {star_top:.0f}, toast {toast_top:.0f})")
    card = frame.locator('.cardbtn[data-open="lilac"]')
    card.click()
    geo = frame.evaluate("""() => { const m = document.getElementById('modal').getBoundingClientRect();
      const c = document.querySelector('.pcard[data-open=lilac]').getBoundingClientRect();
      return { modalTop: m.top + scrollY, modalBottom: m.bottom + scrollY, cardTop: c.top + scrollY,
               nested: document.getElementById('modal-back').scrollHeight > document.getElementById('modal-back').clientHeight + 2 }; }""")
    s.check(abs(geo["modalTop"] - geo["cardTop"]) < 300, f"embed: dialog opens beside the clicked card ({geo})")
    s.check(not geo["nested"], "embed: no nested scrollbar inside the dialog overlay")
    page.wait_for_function(f"() => __msgs.filter(m => m.type === 'wpr-embed-height').pop().height >= {int(geo['modalBottom'])}")
    s.check(True, "embed: iframe grows to fit the dialog")
    frame.click("[data-ask]")
    page.wait_for_function("() => __msgs.some(m => m.type === 'wpr-embed-scroll')")
    s.check(True, "embed: asks the host to scroll when a link switches tabs")
    s.check(page.evaluate("__msgs.some(m => m.type === 'wpr-embed-event' && m.name === 'plant_open')"), "embed: analytics events reach the host")
    s.no_errors(errors, "embed")
    ctx.close()
    ctx = browser.new_context(color_scheme="dark", timezone_id=TZ)
    page = ctx.new_page()
    stub_network(page, FIXED)
    page.set_content(f'<iframe src="{base}/index.html?theme=auto" style="width:900px;height:600px"></iframe>')
    page.wait_for_timeout(300)
    frame = next(f for f in page.frames if f.url.startswith(base))
    frame.wait_for_function("() => document.body")
    s.check(not frame.evaluate("document.documentElement.classList.contains('force-light')"), "embed: ?theme=auto follows the reader's theme")
    ctx.close()


def test_mobile(s, browser, base):
    ctx, page, errors, _ = open_page(browser, base, viewport={"width": 375, "height": 812}, mobile=True)
    q = page.evaluate
    s.check(q("document.documentElement.scrollWidth <= innerWidth"), "mobile: no sideways page scroll")
    s.check(q("document.querySelector('.tabs').scrollHeight <= document.querySelector('.tabs').clientHeight"), "mobile: tab strip has no vertical overflow")
    geo = q("""() => { const w = document.querySelector('.cal-scroll'), n = document.querySelector('.cal-name').getBoundingClientRect(),
      r = w.getBoundingClientRect(), l = document.querySelector('.legend').getBoundingClientRect();
      return { scrolled: w.scrollLeft, nameLeft: n.left, scrollerLeft: r.left, legendLeft: l.left, legendRight: l.right }; }""")
    s.check(geo["scrolled"] > 0, "mobile: calendar scrolls to today")
    s.check(abs(geo["nameLeft"] - geo["scrollerLeft"]) <= 1, f"mobile: plant names pinned to the scroller's edge ({geo})")
    s.check(geo["legendLeft"] >= 0 and geo["legendRight"] <= 375, f"mobile: legend stays on screen ({geo})")
    page.goto(page.url.split("#")[0] + "#ask")
    page.wait_for_function("() => currentPanel === 'ask'")
    tab = q("() => { const t = document.getElementById('tab-ask').getBoundingClientRect(), b = document.querySelector('.tabs').getBoundingClientRect(); return t.left >= b.left - 1 && t.right <= b.right + 1; }")
    s.check(tab, "mobile: the active tab is scrolled into view")
    s.no_errors(errors, "mobile")
    ctx.close()


def test_print(s, browser, base):
    ctx, page, errors, _ = open_page(browser, base, path="/index.html?demo")
    page.emulate_media(media="print")
    q = page.evaluate
    s.check(q("getComputedStyle(document.querySelector('.tabs')).display") == "none" and q("getComputedStyle(document.getElementById('calendar')).display") == "block", "print: calendar only")
    s.check(q("getComputedStyle(document.querySelector('.demo-ribbon')).display") == "none", "print: preview ribbon hidden")
    pdf = page.pdf(format="Letter", landscape=True, print_background=True)
    pages = len(re.findall(rb"/Type\s*/Page[^s]", pdf))
    s.check(pages == 1, f"print: the fridge calendar fits one page ({pages} pages)")
    s.no_errors(errors, "print")
    ctx.close()


TESTS = [test_boot, test_contrast, test_tabs_history_modal, test_calendar, test_guides_favorites_notes,
         test_weather, test_seasons, test_tasks, test_reminders, test_community, test_ask, test_launch_mode,
         test_storage_tamper, test_embedded, test_mobile, test_print]


def main():
    # Windows consoles default to cp1252; failure messages carry arrows and dashes
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    httpd, base = serve()
    suite = Suite()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless="--headed" not in sys.argv)
        for t in TESTS:
            if only and not any(o in t.__name__ for o in only):
                continue
            print(f"- {t.__name__}")
            try:
                t(suite, browser, base)
            except Exception as e:  # a crash is a failure, not an abort of the whole run
                suite.fails.append(f"{t.__name__} crashed: {e!r}"[:400])
                print("    CRASH", repr(e)[:400])
        browser.close()
    httpd.shutdown()
    print(f"\n{suite.passed} checks passed, {len(suite.fails)} failed")
    for f in suite.fails:
        print("  FAIL", f)
    sys.exit(1 if suite.fails else 0)


if __name__ == "__main__":
    main()
