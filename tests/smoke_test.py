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


def nws_name(now, i):
    """period names as NWS writes them: Today, Tonight, Friday, Friday Night, ..."""
    if i < 2:
        return ("Today", "Tonight")[i]
    day = (now + timedelta(days=i // 2)).strftime("%A")
    return day if i % 2 == 0 else f"{day} Night"


def forecast_fixture(now, low):
    start = now.replace(minute=0, second=0, microsecond=0)
    periods = []
    for i in range(14):
        s = start + timedelta(hours=12 * i)
        day = i % 2 == 0
        periods.append({
            "number": i + 1, "name": nws_name(now, i), "isDaytime": day,
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
    n = q("PLANTS.length")
    s.check(n >= 76, f"boot: the full plant list loads ({n} plants)")
    s.check(q("document.querySelectorAll('.pcard').length") == n, "boot: a card for every plant")
    s.check(q("document.querySelectorAll('.cal-group:not(.pin) .cal-row').length") == n, "boot: a calendar row for every plant")
    s.check(q("[...document.querySelectorAll('.cal-group:not(.pin) .cal-group-h')].map(h => h.firstChild.textContent.trim()).join('|')") == "Vegetables|Herbs|Flowers",
            "boot: the calendar is grouped by category")
    pin = q("""() => { const g = document.querySelector('#cal-grid .cal-group.pin'); return g && { head: (h => { const c = h.cloneNode(true); c.querySelectorAll('.sr-only').forEach(x => x.remove()); return c.textContent.trim(); })(g.querySelector('.cal-group-h')), sr: (g.querySelector('.cal-group-h .sr-only') || {}).textContent,
      ids: [...g.querySelectorAll('.cal-name')].map(e => e.dataset.open).join(), due: [...g.querySelectorAll('.cal-due')].map(e => e.textContent).join('|'),
      first: document.querySelector('#cal-grid .cal-group') === g }; }""")
    s.check(pin and pin["first"] and pin["head"] == "Open now 2" and pin["sr"] == ", each also listed in its group below" and pin["ids"] == "solomons-seal,peony"
            and pin["due"] == ", open now: Plant through Sep 25|, open now: Plant through Oct 15",
            f"boot: what's open now leads the full calendar, closing soonest first, each with its deadline ({pin})")
    s.check(q("document.querySelectorAll('.tab').length") == 5 and q("document.querySelectorAll('.tab:not([hidden])').length") == 4,
            "boot: launch mode shows 4 tabs (Community waits for a photo endpoint)")
    s.check(q("document.querySelectorAll('.post').length") == 0, "boot: launch mode never shows the demo posts")
    s.check(q("document.getElementById('dateline-date').textContent") == "Thursday, September 24, 2026", "boot: dateline")
    s.check("Day 133 of the frost-free season" in q("document.getElementById('season-pulse').textContent"), "boot: season pulse")
    s.check(q("document.querySelectorAll('.wx-card').length") == 7, "boot: 7 daily forecast cards")
    s.check(q("document.getElementById('kicker-proto').hidden") is True and q("CONFIG.askEmail") == "aamgncwi@gmail.com"
            and q("!!document.querySelector('#ask-form textarea')"), "boot: launch mode: no prototype label, and Ask questions go to the Master Gardeners")
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
        fails += audit(page, "#panel-calendar")
        page.evaluate("() => { calView = 'chart'; renderCalendar(); openModal('broccoli'); }")
        fails += audit(page, "#modal")
        page.evaluate("() => { closeModalUI(); openModal('daylily'); }")  # Safety line
        fails += audit(page, "#modal")
        page.evaluate("() => { closeModalUI(); document.getElementById('share-btn').click(); }")
        fails += audit(page, "#modal")
        page.evaluate("() => { closeModalUI(); CONFIG.sponsorUpsell = true; renderMastSponsor(); renderAsk(); showPanel('ask', false); }")
        fails += audit(page, "header") + audit(page, "#panel-ask")
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
    s.check(page.locator("#tab-community").is_hidden(), "tabs: Community stays hidden at launch")
    for tab in ("guides", "weather", "ask", "calendar"):
        page.click(f"#tab-{tab}")
        s.check(page.evaluate("document.querySelector('.panel.active').id") == "panel-" + tab and page.url.endswith("#" + tab), f"tabs: {tab} panel + hash")
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

    page.goto(base + "/index.html?deeplink=2#weather")
    s.check(page.evaluate("currentPanel") == "weather" and page.evaluate("scrollY") == 0, "deep link: #weather opens the tab without jumping past the masthead")
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
    herbs = page.evaluate("PLANTS.filter(p => p.cat === 'herb').length")
    page.click('#cal-filters .fbtn[data-cat="herb"]')
    s.check(page.locator(".cal-row").count() == herbs and page.locator(".cal-group-h").count() == 0,
            "calendar: herb filter (one category, no group headings)")
    page.click('#cal-filters .fbtn[data-view="list"]')
    s.check(page.locator(".cal-trow").count() == herbs, "calendar: list view")
    page.click('#cal-filters .fbtn[data-view="chart"]')
    page.click('#cal-filters .fbtn[data-cat="all"]')
    s.check(page.locator(".cal-group:not(.pin) .cal-row .mband").count() == page.evaluate("PLANTS.length") * 4, "calendar: month bands on every row")
    s.check(page.locator(".cal-group:not(.pin) .cal-months:not(.rep)").count() == 3 and page.locator(".cal-group.pin .cal-months").count() == 1,
            "calendar: each group, the open-now one included, has its own month axis")
    reps = page.evaluate("""() => [...document.querySelectorAll('.cal-group:not(.pin)')].map(g => [g.dataset.cat, g.querySelectorAll('.cal-row').length,
      g.querySelectorAll('.cal-months.rep').length, g.querySelectorAll('.cal-key').length])""")
    want = [[c, n, max(0, (n - 4) // 12), (1 if i else 0) + max(0, (n - 4) // 12)] for i, (c, n, _, _) in enumerate(reps)]
    s.check(reps == want and sum(r[2] for r in reps) > 0, f"calendar: long groups repeat the months and color key every dozen rows ({reps})")
    s.check(page.locator(".cal-name small").count() == 0, "calendar: rows don't repeat their group's category label")
    s.check(page.evaluate("[...document.querySelectorAll('#cal-filters .seg .fbtn')].map(b => b.dataset.view).join()") == "chart,list",
            "calendar: Chart and List form one control")
    summary = page.evaluate("document.querySelector('.cal-group:not(.pin) .cal-track .sr-only').textContent")
    s.check(summary.startswith("Next window: start indoors opens Apr 10 next year. Start seeds indoors Apr 10 to Apr 25"),
            f"calendar: screen-reader summary per row, status first ({summary[:80]})")
    page.hover('.cal-bar[data-plant="tomato"]')
    s.check(page.evaluate("getComputedStyle(tip).display") == "block" and "Tomato" in page.evaluate("tip.textContent"), "calendar: bar tooltip")
    page.click('.cal-bar[data-plant="peony"]')
    s.check(page.evaluate("document.getElementById('modal-title').textContent") == "Peony", "calendar: bar click opens the guide")
    page.keyboard.press("Escape")
    page.focus('.cal-name[data-open="dill"]')
    page.keyboard.press("Enter")
    s.check(page.evaluate("document.getElementById('modal-title').textContent") == "Dill", "calendar: Enter on a plant name opens it")
    mini = page.evaluate("""() => ({ bars: document.querySelectorAll('#modal .mini-cal .cal-bar').length, want: PLANTS.find(p => p.id === 'dill').bars.length,
      key: document.querySelector('#modal .mc-key').textContent, first: document.querySelector('#modal .mini-cal').compareDocumentPosition(document.querySelector('#modal .spec-grid')) & 4 })""")
    s.check(mini["bars"] == mini["want"] and "Direct sow outdoors" in mini["key"] and mini["first"], f"guide: a timeline of the plant's windows leads the guide ({mini})")
    page.keyboard.press("Escape")
    s.check(page.evaluate("document.querySelector('.tabs').scrollHeight <= document.querySelector('.tabs').clientHeight"), "layout: tab strip has no vertical overflow (Windows scrollbar artifact)")
    s.no_errors(errors, "calendar")
    ctx.close()


def test_guides_favorites_notes(s, browser, base):
    ctx, page, errors, _ = open_page(browser, base)
    page.click("#tab-guides")
    page.fill("#guide-search", "pickle")
    page.evaluate("window.__tomatoCard = document.querySelector('.pcard[data-open=tomato]')")
    s.check(page.locator(".pcard:not([hidden])").count() == 1 and page.evaluate("document.querySelector('.pcard:not([hidden])').dataset.open") == "dill", "guides: search reaches tips")
    s.check(page.evaluate("document.contains(window.__tomatoCard)"), "guides: searching filters the cards in place instead of rebuilding them")
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
    page.click("#modal .notes summary")
    page.fill("#plant-note", "Genovese by the south fence")
    page.keyboard.press("Escape")
    page.reload()
    page.wait_for_function("() => document.querySelectorAll('.pcard').length === PLANTS.length")
    page.evaluate("() => openModal('basil', false)")
    s.check(page.evaluate("document.getElementById('plant-note').value") == "Genovese by the south fence", "modal: notes persist across reloads")
    s.check(page.evaluate("document.querySelector('.star[data-fav=basil]').classList.contains('on')"), "guides: star persists across reloads")
    s.no_errors(errors, "guides")
    ctx.close()


def test_weather(s, browser, base):
    ctx, page, errors, calls = open_page(browser, base, low=34)
    page.click("#tab-weather")
    s.check(page.locator(".wx-card.frosty").count() == 1 and page.locator(".wx-card.cold-low").count() == 1, "weather: frost-risk night flagged, its low in red")
    s.check(page.evaluate("document.querySelector('.wx-status h3').textContent.replace(/\\u00a0/g, ' ')") == "Frost possible tonight — cover and pick", "weather: September frost advice names the night")
    s.check(page.locator(".wx-card .rain").count() == 1, "weather: rain chance shown")
    s.check("frost risk" in page.locator(".wx-card.frosty").text_content(), "weather: frost card labeled, not color alone")
    s.check(page.evaluate("document.querySelector('.wx-card .temp').textContent.replace(/\\s+/g, ' ').trim()") == "High 70°, low 34°", "weather: high and overnight low on one card")
    pairs = page.evaluate("""() => dailyCards([{name:'Tonight',isDaytime:false,temp:40,pop:null,cond:'Clear'},
      {name:'Friday',isDaytime:true,temp:70,pop:null,cond:'Sunny'},{name:'Friday Night',isDaytime:false,temp:50,pop:null,cond:'Clear'}])
      .map(d => [d.name, !!d.day, !!d.night])""")
    s.check(pairs == [["Tonight", False, True], ["Friday", True, True]], f"weather: evening fetch starts with a lone Tonight card ({pairs})")
    s.check("peony" in page.evaluate("document.getElementById('wx-windows').textContent").lower(), "weather: open planting windows listed")
    first_points = calls["points"]
    page.reload()
    page.wait_for_function("() => document.querySelectorAll('.wx-card').length === 7")
    s.check(calls["points"] == first_points, "weather: cached forecast URL skips the /points lookup")
    calls["mode"] = "fail"
    page.reload()
    page.wait_for_function("() => document.querySelector('.wx-note, .wx-error')")
    s.check(page.locator(".wx-note").count() == 1 and page.locator(".wx-card").count() == 7, "weather: falls back to the last forecast when NWS fails")
    note = page.locator(".wx-note").text_content()
    s.check("fetch" not in note.lower() and "responded" not in note and "(" not in note, f"weather: the fallback note explains itself in plain language ({note[:90]})")
    page.evaluate("() => localStorage.removeItem('wg:nws-last')")
    page.reload()
    page.click("#tab-weather")
    page.wait_for_function("() => document.querySelector('.wx-error')")
    s.check(page.locator("#wx-retry").count() == 1, "weather: error state offers a retry")
    err = page.locator(".wx-error").text_content()
    s.check("fetch" not in err.lower() and "responded" not in err and "api.weather.gov" not in err and "goes by the date alone" in err,
            f"weather: the error explains itself in plain language ({err[:90]})")
    calls["mode"] = "ok"
    page.click("#wx-retry")
    page.wait_for_function("() => document.querySelectorAll('.wx-card').length === 7")
    s.check(True, "weather: retry recovers")
    s.no_errors(errors, "weather", allow=("Failed to load resource",))
    ctx.close()


SEASONS = [
    (central(2027, 1, 15), 48, "The garden is asleep", "time to order seeds"),
    (central(2027, 3, 20), 48, "Seed-starting season", "seed-starting season"),
    (central(2027, 4, 20), 48, "Early season — hardy crops only", "until Wausau's ~May 15 last-frost date"),
    (central(2027, 5, 20), 50, "Past the frost date — tender crops from May 25", "Day 6 of the frost-free season"),
    (central(2027, 6, 3), 50, "Clear to plant frost-tender crops", "of the frost-free season"),
    (central(2027, 5, 20), 34, "Frost possible tonight — hold off on tender crops", "Frost possible tonight (34°F): hold off on tender crops"),
    (central(2027, 7, 20), 34, "Frost possible tonight — cover tender crops", "Frost possible tonight (34°F): cover tender plants"),
    (central(2026, 9, 24), 48, "Late season — harvest and prep for frost", "of the frost-free season"),
    (central(2026, 10, 10), 48, "No frost this week — tender crops can stay out", "still open for planting: garlic and peony"),
    (central(2026, 10, 28), 48, "No frost this week — tender crops can stay out", "this year’s planting windows have closed"),
    (central(2026, 10, 10), 34, "Frost possible tonight — cover and pick", "Frost possible tonight (34°F): cover tender plants"),
    (central(2026, 11, 10), 48, "The garden is asleep", "until next spring's ~May 15 last-frost date"),
]


def test_seasons(s, browser, base):
    for when, low, head, pulse in SEASONS:
        tag = when.strftime("%b %d %Y") + f" low {low}"
        ctx, page, errors, _ = open_page(browser, base, when=when, low=low)
        got_head = page.evaluate("document.querySelector('.wx-status h3').textContent.replace(/\\u00a0/g, ' ')")
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
        body = page.evaluate("document.querySelector('.wx-status p').textContent")
        s.check("bulbs" not in body + got_pulse, f"seasons/{tag}: no promise of bulbs the guides don't cover")
        if month == 9:
            s.check("Still open for planting: peony and Solomon’s seal." in body, f"seasons/{tag}: fall advice names what's open ({body[-80:]})")
        if month == 10 and low > 36:
            want = "Still open for planting: garlic and peony." if when.day == 10 else None
            s.check((want in body) if want else "Still open" not in body, f"seasons/{tag}: fall advice names what's open, or nothing once it closes")
        if month == 10:
            tiles = page.evaluate("document.getElementById('wx-tiles').textContent")
            s.check("Coldest night ahead" in tiles and f"{low}°F" in tiles and ("Frost possible" in tiles) == (low <= 36),
                    f"seasons/{tag}: October tiles follow the forecast")
            if low > 36:
                s.check(f"{low}°F" in page.evaluate("document.querySelector('.wx-status p').textContent"), f"seasons/{tag}: advice names the coldest night")
        if month in (5, 7) and low <= 36:
            s.check("Frost possible" in page.evaluate("document.getElementById('wx-tiles').textContent"), f"seasons/{tag}: a frost week puts the frost rows in the box")
        if month == 11:
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
            and starts.get("wg-spinach-2-2027@wausaupilotandreview.com") == "20270801", "reminders: both spinach sowings kept")
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
    page.evaluate("() => { CONFIG.prototype = true; applyPrototype(); renderPosts(); }")   # the demo is prototype-only
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
    s.check(page.evaluate("CONFIG.askEmail") == "aamgncwi@gmail.com" and page.locator("#ask-form textarea").count() == 1
            and "Prototype" not in page.locator("#ask-fine").text_content(), "ask: launch mode routes questions to the Master Gardeners' inbox")
    hand = page.evaluate("""() => ({ btn: document.querySelector('#ask-form button[type=submit]').textContent, req: document.getElementById('ask-email').required,
      label: document.querySelector('label[for=ask-email]').textContent, how: document.getElementById('ask-how').hidden ? '' : document.getElementById('ask-how').textContent })""")
    s.check(hand["btn"] == "Email my question" and not hand["req"] and "optional" in hand["label"]
            and "opens your email app" in hand["how"] and "aamgncwi@gmail.com" in hand["how"],
            f"ask: before the tap, the form says it opens the reader's email app, and the reply address is optional ({hand})")
    page.evaluate("() => { CONFIG.prototype = true; CONFIG.askEmail = null; renderAsk(); }")
    s.check(page.evaluate("[document.querySelector('#ask-form button[type=submit]').textContent, document.getElementById('ask-email').required, document.getElementById('ask-how').hidden]")
            == ["Send my question", True, True], "ask: with a server (or in the prototype) the form sends, and needs an email for the reply")
    page.fill("#ask-q", "Why are my tomato leaves curling?")
    page.fill("#ask-name", "Pat M.")
    page.fill("#ask-email", "pat@example.org")
    page.click("#ask-form button[type=submit]")
    s.check(page.locator("#ask-form .form-msg.ok").count() == 1, "ask: prototype submission confirms")
    s.check(page.evaluate("document.activeElement.matches('#ask-form .form-msg.ok')"), "ask: focus moves to the confirmation, not the page body")
    page.click("#ask-again")
    s.check(page.evaluate("document.activeElement.id") == "ask-q", "ask: 'Ask another question' restores the form")
    s.check(page.evaluate("[document.getElementById('ask-name').value, document.getElementById('ask-q').value]") == ["Pat M.", ""],
            "ask: a new question keeps the reader's name and email")

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
    page.fill("#ask-email", "")
    page.click("#ask-form button[type=submit]")
    mailto = page.evaluate("window.__mailto") or ""
    s.check(mailto.startswith("mailto:help@example.org?subject=") and "Hostas%20and%20deer" in mailto and "Reply%20to" not in mailto,
            "ask: email mode opens a pre-filled message, with no reply line when the email is left blank")
    s.check(page.locator("#ask-copy").count() == 1, "ask: email mode offers a copy fallback")
    s.check(page.locator("#ask-again").text_content() == "Back to the form", "ask: after the hand-off, the button goes back rather than starting over")
    page.click("#ask-again")
    s.check(page.evaluate("document.getElementById('ask-q').value") == "Hostas and deer?", "ask: going back keeps the question, since nothing was sent")

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
    s.check(q("document.querySelectorAll('.post').length") == 0, "launch: demo posts never shown as real")
    s.check(q("document.getElementById('tab-community').hidden"), "launch: Community tab hidden while readers can't send photos")
    q("() => { location.hash = '#community'; }")
    page.wait_for_timeout(100)
    s.check(q("currentPanel") != "community", "launch: a #community link doesn't open the hidden tab")
    q("""() => { CONFIG.submitEndpoint = 'https://example.test/share'; applyPrototype();
      POSTS.push({ id: 'r1', who: 'Pat M.', where: 'Weston', when: 'Jun 2', plant: 'peony', hue1: '#e87ba4', hue2: '#8f3060', cap: 'First peony.', likes: 3, comments: [] });
      renderPosts(); }""")
    s.check(not q("document.getElementById('tab-community').hidden") and not q("document.getElementById('share-btn').hidden"),
            "launch: Community returns once photos can be sent")
    s.check(q("document.querySelectorAll('.post').length") == 1 and q("document.querySelectorAll('.post .likebtn, .post .cform').length") == 0,
            "launch: real posts show without likes or comments (no backend)")
    s.check(q("document.getElementById('foot-about').textContent") == "The Wausau Grower is a reader tool from Wausau Pilot & Review.", "launch: footer copy")
    s.no_errors(errors, "launch")
    ctx.close()


def test_storage_tamper(s, browser, base):
    bad = {"wg:favs": "5", "wg:likes": '{"a":1}', "wg:note:tomato": "[1,2]", "wg:tasks:2026-9": '"oops"',
           "wg:visit": '"not-a-date"', "wg:nws-url": '{"url":"javascript:alert(1)","at":1}',
           "wg:nws-last": '{"periods":"x"}', "wg:hint-star": "{{{"}
    ctx, page, errors, calls = open_page(browser, base, storage=bad)
    s.check(page.evaluate("document.querySelectorAll('.pcard').length === PLANTS.length"), "tamper: page renders with corrupted storage")
    s.check(page.evaluate("document.querySelectorAll('.wx-card').length") == 7 and calls["points"] >= 1, "tamper: bad cached forecast URL is ignored")
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
    frame.wait_for_function("() => document.querySelectorAll('.pcard').length === PLANTS.length")
    s.check(frame.evaluate("document.documentElement.classList.contains('force-light')"), "embed: forced light inside a light host")
    s.check(frame.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(247, 247, 247)", "embed: light palette despite a dark OS")
    s.check(frame.evaluate("getComputedStyle(document.getElementById('bookmark-btn')).display") == "none", "embed: bookmark button hidden")
    s.check(frame.evaluate("['.brandbar', '.rule-double', '.dateline', '.kicker'].every(sel => getComputedStyle(document.querySelector(sel)).display === 'none')")
            and frame.evaluate("getComputedStyle(document.querySelector('.masthead h1')).display") != "none",
            "embed: the article's own masthead isn't repeated (tool title stays)")
    short = frame.evaluate("document.querySelectorAll('#cal-grid .cal-row, #cal-grid .cal-trow').length")
    s.check(0 < short < frame.evaluate("PLANTS.length") and frame.locator('#cal-filters .fbtn[data-cat="short"].on').count() == 1,
            f"embed: the calendar opens on a short list ({short} plants)")
    s.check(frame.evaluate("document.querySelector('.cal-wrap').classList.contains('is-list')") == (short <= 4)
            and frame.evaluate("document.querySelector('#cal-filters .fbtn[data-view=list]').classList.contains('on')") == (short <= 4),
            f"embed: four or fewer plants show as a list, more as the chart ({short})")
    s.check(frame.evaluate("getComputedStyle(document.querySelector('.deck')).display") == "none"
            and frame.evaluate("getComputedStyle(document.getElementById('season-pulse')).display") != "none",
            "embed: no deck under the article's headline; the season line leads")
    s.check(frame.evaluate("getComputedStyle(document.getElementById('print-btn')).display") == "none"
            and frame.evaluate("document.getElementById('ics-btn').hidden"),
            "embed: no Print, and Reminders waits until something is starred")
    s.check(frame.evaluate("getComputedStyle(document.querySelector('.foot-actions [data-act=share]')).display") != "none"
            and frame.evaluate("getComputedStyle(document.querySelector('.foot-actions [data-act=bookmark]')).display") == "none",
            "embed: Share sits in the footer; Bookmark stays hidden")
    frame.click("[data-showall]")
    s.check(frame.evaluate("document.querySelectorAll('#cal-grid .cal-group:not(.pin) .cal-row').length === PLANTS.length")
            and frame.locator('#cal-filters .fbtn[data-cat="all"].on').count() == 1, "embed: Show all brings back every plant")
    frame.click("#tab-guides")
    page.wait_for_function("() => parseInt(document.getElementById('wausau-grower').style.height) > 2000")
    star = frame.locator('.star[data-fav="lilac"]')
    star.click()
    s.check(not frame.evaluate("document.getElementById('ics-btn').hidden"), "embed: Reminders appears once a plant is starred")
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
    s.check(frame.evaluate("getComputedStyle(document.querySelector('#modal .tip-close')).display") != "none",
            "embed: a second close sits after the tip, since nothing can pin the top one")
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
    s.check(q("document.querySelector('.tabs').scrollWidth <= document.querySelector('.tabs').clientWidth + 1"), "mobile: all five tabs fit without sideways scrolling")
    # heights depend on the face that loads (CI renders without Oswald), so check structure plus a loose budget
    mast = q("""[Math.round(document.querySelector('.masthead').getBoundingClientRect().height),
      getComputedStyle(document.querySelector('.zone-chip .zl')).display, getComputedStyle(document.querySelector('.zone-chip')).paddingTop]""")
    s.check(mast[0] <= 580 and mast[1] == "none" and mast[2] == "0px", f"mobile: the zone boxes collapse to one quiet line and the masthead leaves room for the tool ({mast})")
    s.check(q("document.querySelector('#cal-filters .fbtn[data-cat=short]').classList.contains('on')")
            and q("getComputedStyle(document.querySelector('#cal-filters .fbtn[data-cat=now]')).display") == "none",
            "mobile: a standalone phone opens the calendar on Now & next, as embeds do")
    page.click('#cal-filters .fbtn[data-cat="all"]')
    first = q("""() => { const c = document.querySelector('#cal-filters .chips'), t = document.querySelector('#cal-filters .cal-tools');
      const vis = sel => getComputedStyle(document.querySelector(sel)).display !== 'none';
      return { chipRows: Math.round(c.getBoundingClientRect().height), scrolls: c.scrollWidth > c.clientWidth,
               toolsTop: Math.round(t.getBoundingClientRect().top - c.getBoundingClientRect().bottom), toolsH: Math.round(t.getBoundingClientRect().height),
               mast: vis('.mast-actions'), foot: vis('.foot-actions'), longIntro: vis('#panel-calendar .sub .t-long'), shortIntro: vis('#panel-calendar .sub .t-short') }; }""")
    s.check(first["chipRows"] <= 56 and first["scrolls"] and first["toolsH"] == 0,
            f"mobile: the category chips take the full row and scroll sideways; the chart tools move into the calendar card ({first})")
    s.check(not first["mast"] and first["foot"], f"mobile: Bookmark and Share move from the masthead to the footer ({first})")
    s.check(not first["longIntro"] and first["shortIntro"], "mobile: the calendar's intro is one line")
    top = q("""() => { const vis = sel => getComputedStyle(document.querySelector(sel)).display !== 'none', lg = document.querySelector('.legend');
      return { deck: vis('.deck'), legendH: Math.round(lg.getBoundingClientRect().height), legendScrolls: lg.scrollWidth > lg.clientWidth,
               firstBar: Math.round(document.querySelector('#cal-grid .cal-row').getBoundingClientRect().top + scrollY) }; }""")
    s.check(not top["deck"] and top["legendH"] <= 34 and top["legendScrolls"] and top["firstBar"] <= 780,
            f"mobile: no deck or star tip on a phone, a one-line color key, and the first chart row near the top ({top})")
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
    read = q("""() => { showPanel('calendar', false); const fs = sel => parseFloat(getComputedStyle(document.querySelector(sel)).fontSize);
      return { sub: fs('#panel-calendar .sub'), search: fs('#guide-search'), ask: fs('#ask-q'), name: fs('#ask-name'),
               pill: Math.round(document.querySelector('#cal-filters .fbtn[data-cat="all"]').getBoundingClientRect().height),
               row: Math.round(document.querySelector('.cal-row').getBoundingClientRect().height) }; }""")
    s.check(read["sub"] >= 16 and min(read["search"], read["ask"], read["name"]) >= 16,
            f"mobile: 16px reading text and form fields, so iOS doesn't zoom ({read})")
    s.check(read["pill"] >= 44 and read["row"] >= 44, f"mobile: buttons and calendar rows are at least 44px tall ({read})")
    audit13 = q("""() => {
      const out = new Set();
      const scan = root => { for (const el of root.querySelectorAll('*')) {
        if (!el.offsetParent && getComputedStyle(el).position !== 'fixed') continue;
        if (el.closest('.dateline, .fb-tag, .sr-only, .hp, .print-note')) continue;
        const own = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
        if (own && parseFloat(getComputedStyle(el).fontSize) < 13) out.add(`${el.className || el.tagName} "${el.textContent.trim().slice(0, 24)}" ${getComputedStyle(el).fontSize}`);
      } };
      for (const id of ['calendar', 'guides', 'weather', 'ask']) { showPanel(id, false); scan(document.body); }
      openModal('tomato', false); scan(document.getElementById('modal'));
      const save = document.getElementById('modal-star').getBoundingClientRect(), close = document.getElementById('modal-close').getBoundingClientRect();
      closeModalUI();
      return { small: [...out], targets: [Math.round(save.height), Math.round(close.width), Math.round(close.height)] };
    }""")
    s.check(not audit13["small"], f"mobile: no text under 13px outside WPR's flag ({audit13['small'][:5]})")
    s.check(min(audit13["targets"]) >= 44, f"mobile: guide buttons are at least 44px ({audit13['targets']})")
    s.no_errors(errors, "mobile")
    ctx.close()


def test_print(s, browser, base):
    ctx, page, errors, _ = open_page(browser, base, path="/index.html?demo")
    page.emulate_media(media="print")
    q = page.evaluate
    s.check(q("getComputedStyle(document.querySelector('.tabs')).display") == "none" and q("getComputedStyle(document.getElementById('panel-calendar')).display") == "block", "print: calendar only")
    s.check(q("getComputedStyle(document.querySelector('.demo-ribbon')).display") == "none", "print: preview ribbon hidden")
    count = lambda: len(re.findall(rb"/Type\s*/Page[^s]", page.pdf(format="Letter", landscape=True, print_background=True)))
    pages = count()
    s.check(pages == 2, f"print: the full calendar prints on two pages, vegetables and herbs, then flowers ({pages} pages)")
    for cat in ("veg", "herb", "flower"):
        page.emulate_media(media="screen")
        page.click(f'#cal-filters .fbtn[data-cat="{cat}"]')
        page.emulate_media(media="print")
        pages = count()
        s.check(pages == 1, f"print: the {cat} calendar fits one page ({pages} pages)")
    page.emulate_media(media="print", color_scheme="dark")
    ink = q("[getComputedStyle(document.documentElement).getPropertyValue('--ink-1').trim(), getComputedStyle(document.querySelector('.cal-wrap')).backgroundColor]")
    s.check(ink[0] == "#111111" and ink[1] in ("rgb(255, 255, 255)", "rgba(0, 0, 0, 0)"), f"print: a dark-mode reader still prints the light calendar ({ink})")
    page.emulate_media(media="screen", color_scheme="dark")
    s.check(q("getComputedStyle(document.querySelector('.brand .seal')).borderRadius") == "50%", "dark mode: the seal is clipped to its circle, no white tile")
    s.no_errors(errors, "print")
    ctx.close()


def test_polish_v17(s, browser, base):
    ctx, page, errors, _ = open_page(browser, base)
    q = page.evaluate
    s.check(q("document.querySelector('#guide-filters .fbtn[data-cat=now]').textContent") == "Open now (2)" and q("document.querySelectorAll('#guide-filters .fbtn[data-cat=now] svg').length") == 1,
            "open now: count on Sep 24 (peony, solomons-seal), with a drawn sprout")
    page.click('#cal-filters .fbtn[data-cat="now"]')
    s.check(q("[...document.querySelectorAll('.cal-name')].map(e => e.dataset.open).sort().join(',')") == "peony,solomons-seal", "open now: calendar filter")
    page.click("#tab-guides")
    page.click('#guide-filters .fbtn[data-cat="now"]')
    s.check(page.locator(".pcard:not([hidden])").count() == 2, "open now: guides filter")
    page.click('#guide-filters .fbtn[data-cat="all"]')
    page.fill("#guide-search", "zzz")
    s.check("zzz" in page.locator("#guide-cards .empty").text_content(), "search: empty state echoes the query")
    page.click("#clear-search")
    s.check(page.locator(".pcard:not([hidden])").count() == q("PLANTS.length") and q("document.activeElement.id") == "guide-search", "search: clear link resets and refocuses")
    page.click('.cardbtn[data-open="kale"]')
    page.click("#modal-star")
    page.click("#modal .notes summary")
    page.fill("#plant-note", "Winterbor by the fence")
    q("() => { window.__shared = null; Object.defineProperty(navigator, 'share', { configurable: true, value: d => { window.__shared = d; return Promise.resolve(); } }); }")
    page.click("#modal-share")
    shared = q("window.__shared") or {}
    s.check(shared.get("url", "").endswith("#plant/kale") and "Kale" in shared.get("title", ""), f"share: a single guide shares its own deep link ({shared.get('url')})")
    page.click("#modal-close")
    page.wait_for_function("() => !isModalOpen()")
    s.check(q("document.activeElement.dataset.open") == "kale", "modal: focus returns to the card even after starring re-rendered the grid")
    s.check("Your notes" in page.locator('.pcard[data-open="kale"] .meta').text_content(), "guides: cards flag plants with notes")
    page.goto(base + "/index.html?x=2#plant/not-a-plant")
    page.wait_for_function("() => currentPanel === 'guides'")
    s.check(not q("isModalOpen()") and q("getComputedStyle(document.getElementById('toast')).display") == "block", "deep link: unknown plant lands on the guides with a note")
    s.no_errors(errors, "polish v1.7")
    ctx.close()


# The sources page's cross-check table is built from PLANTS; these checks keep the two from drifting apart.
WINDOWS_JS = """() => {
  const M = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const rng = b => b.s[0] === b.e[0] ? `${M[b.s[0]]} ${b.s[1]}–${b.e[1]}` : `${M[b.s[0]]} ${b.s[1]}–${M[b.e[0]]} ${b.e[1]}`;
  const of = (p, t) => p.bars.filter(b => b.t === t).map(rng).join(', ');
  return Object.fromEntries(PLANTS.map(p => [p.id, {
    plant: [['i', 'Indoors'], ['t', 'Plant'], ['s', 'Sow']].filter(([t]) => of(p, t)).map(([t, l]) => `${l} ${of(p, t)}`).join(' · '),
    crop: `${p.hLabel || (p.cat === 'flower' ? 'Bloom' : 'Harvest')} ${of(p, 'h')}`, src: p.src }]));
}"""
ROWS_JS = """() => Object.fromEntries([...document.querySelectorAll('tr[data-plant]')].map(r => [r.dataset.plant, {
  id: r.id, src: r.dataset.src, plant: r.querySelector('.w-plant').textContent.trim(),
  crop: r.querySelector('.w-crop').textContent.trim(), status: r.querySelector('.status').textContent.trim(),
  listed: (r.dataset.src || '').split(' ').every(k => document.getElementById('src-' + k)) }]))"""


def test_sources_page(s, browser, base):
    ctx, page, errors, _ = open_page(browser, base)
    want = page.evaluate(WINDOWS_JS)
    q = page.evaluate
    s.check(q("document.getElementById('plant-count').textContent") == str(q("PLANTS.length")), "guides: the intro's plant count matches the data")
    credit = "Plant list expanded with suggestions from the North Central Wisconsin Master Gardeners Association."
    s.check(credit in q("document.querySelector('#panel-guides .credit-line').textContent") and credit in q("document.querySelector('footer').textContent"),
            "credit: the guides and footer credit the Master Gardeners for the plant suggestions")
    page.evaluate("location.hash = '#plant/hosta'")
    page.wait_for_function("() => isModalOpen()")
    link = page.locator(".srcline a")
    s.check(link.get_attribute("href") == "sources.html#check-hosta"
            and "University of Minnesota Extension" in page.locator(".srcline").text_content(),
            "sources: a guide names its sources and links to its own cross-check row")
    thin = [pid for pid, w in want.items() if len((w["src"] or "").split()) < 2]
    s.check(not thin, f"sources: every plant cites at least two institutions ({thin})")
    page.goto(base + "/sources.html")
    rows = page.evaluate(ROWS_JS)
    s.check(sorted(rows) == sorted(want), f"sources: one cross-check row per plant ({len(rows)} rows)")
    drift = [f"{pid}: page '{rows[pid]['plant']} / {rows[pid]['crop']}', tool '{w['plant']} / {w['crop']}'"
             for pid, w in want.items() if pid in rows and (rows[pid]["plant"], rows[pid]["crop"]) != (w["plant"], w["crop"])]
    s.check(not drift, "sources: the cross-check table matches the tool's dates" + ("; " + "; ".join(drift[:3]) if drift else ""))
    srcs = [pid for pid, w in want.items() if pid in rows and rows[pid]["src"] != w["src"]]
    s.check(not srcs, f"sources: each row names the same institutions as the plant's src ({srcs})")
    s.check(all(r["id"] == "check-" + pid and r["listed"] for pid, r in rows.items()),
            "sources: rows are linkable and every institution is in the source list")
    open_items = [pid for pid, r in rows.items() if not r["status"].startswith(("Confirmed", "Changed"))]
    s.check(not open_items, f"sources: every window is confirmed ({open_items})")
    s.check("Not yet reviewed" not in page.locator("main").text_content(), "sources: no pending-review notice")
    fails = audit(page, "main")
    ctx.close()
    ctx, page, _, _ = open_page(browser, base, scheme="dark")
    page.goto(base + "/sources.html#check-spinach")
    fails += audit(page, "main")
    s.check(not fails, "sources: WCAG AA contrast, light and dark" + (f" — {fmt_fails(fails)}" if fails else ""))
    s.no_errors(errors, "sources")
    ctx.close()


# Flowers, herbs, and natives that NC State Extension rates High or the University of California's toxic-plant list
# rates Class 1 stay out of the tool (vegetables excepted; sources.html#safety). These genera were ruled out on
# October 3, 2026, or are classic garden poisons on those lists; the check keeps them out of later batches.
EXCLUDED_GENERA = ["Datura", "Brugmansia", "Nicotiana", "Podophyllum", "Sanguinaria", "Lupinus", "Aconitum", "Digitalis",
                   "Delphinium", "Consolida", "Convallaria", "Colchicum", "Helleborus", "Ricinus", "Nerium", "Taxus", "Daphne",
                   "Gloriosa", "Lantana", "Rhododendron", "Vinca", "Ipomoea", "Abrus", "Atropa", "Hyoscyamus", "Cicuta", "Conium",
                   "Actaea", "Cimicifuga"]
# NC State rates Japanese anemone High (its current page, Eriocapitella x hybrida); other anemones rate lower
EXCLUDED_SPECIES = [r"(Anemone|Eriocapitella)\s*[×x]\s*hybrida"]


def test_safety(s, browser, base):
    ctx, page, errors, _ = open_page(browser, base)
    q = page.evaluate
    plants = q("PLANTS.map(p => ({ id: p.id, name: p.name, latin: p.latin, cat: p.cat, warn: p.warn || '' }))")
    banned = [p["id"] for p in plants if p["cat"] != "veg" and any(re.search(rf"\b{g}\b", p["latin"]) for g in EXCLUDED_GENERA)]
    s.check(not banned, f"safety: no flower, herb, or native from a genus rated High or Class 1 ({banned})")
    banned_sp = [p["id"] for p in plants if p["cat"] != "veg" and any(re.search(x, p["latin"]) for x in EXCLUDED_SPECIES)]
    s.check(not banned_sp, f"safety: no species rated High on its own (Japanese anemone) ({banned_sp})")
    warned = [p for p in plants if p["warn"]]
    s.check(len(warned) >= 7, f"safety: guides for hazardous plants carry a Safety line ({len(warned)})")
    page.evaluate("location.hash = '#plant/daylily'")
    page.wait_for_function("() => isModalOpen()")
    box = page.locator("#modal .warnbox")
    s.check(box.count() == 1 and "cats" in box.text_content() and box.locator("a").get_attribute("href") == "sources.html#safety",
            "safety: the daylily guide warns cat owners and links to the safety section")
    q("() => { closeModalUI(); openModal('zinnia'); }")
    s.check(page.locator("#modal .warnbox").count() == 0, "safety: guides without a hazard show no Safety line")
    page.goto(base + "/sources.html#safety")
    rows = q("[...document.querySelectorAll('table.safety tbody tr:not(.grp) th')].map(th => th.textContent.trim().toLowerCase())")
    missing = [p["id"] for p in warned if not any(r.startswith(p["name"].lower()) for r in rows)]
    s.check(not missing, f"safety: every Safety line has a row in the sources page's safety table ({missing})")
    s.no_errors(errors, "safety")
    ctx.close()

def test_small_fixes(s, browser, base):
    ctx, page, errors, _ = open_page(browser, base)
    q = page.evaluate
    page.keyboard.press("Tab")
    s.check(q("document.activeElement.id") == "skip-link" and q("document.getElementById('skip-link').getBoundingClientRect().top") >= 0,
            "a11y: the first Tab lands on a skip link, and it shows on screen")
    page.keyboard.press("Enter")
    s.check(q("document.activeElement === document.querySelector('.panel.active h2')"), "a11y: the skip link moves focus to the open section")
    s.check(q("""[...document.querySelectorAll('#cal-filters .fbtn, #guide-filters .fbtn, #bookmark-btn')].every(b =>
      [...b.childNodes].every(n => n.nodeType !== 3 || !/[\\u2600-\\u27BF\\u{1F300}-\\u{1FAFF}]/u.test(n.textContent)))"""),
            "a11y: emoji and stars in buttons are hidden from screen readers")
    s.check(q("document.getElementById('cal-hint') === null"), "reminders: the star tip lives in the empty My plants view, not above the calendar")
    page.click(".zone-about summary")
    body = q("document.querySelector('.zone-about').open ? document.querySelector('.za-body').textContent : ''")
    s.check("May 21" in body and q("document.querySelector('.za-body a').getAttribute('href')") == "sources.html#climate",
            "zone: tapping 'What these mean' explains the dates and links to the sources")
    cols = q("() => ['veg', 'flower', 'herb'].map(c => getComputedStyle(document.querySelector('.pcard .cat.' + c)).color)")
    bars = q("""() => ['--t-indoor', '--t-plant', '--t-sow', '--t-harvest'].map(v => { const d = document.createElement('span');
      d.style.color = `var(${v})`; document.body.appendChild(d); const c = getComputedStyle(d).color; d.remove(); return c; })""")
    s.check(len(set(cols)) == 1 and cols[0] not in bars, f"guides: category labels don't reuse the calendar's colors ({cols[0]})")
    s.no_errors(errors, "small fixes")
    ctx.close()
    ctx, page, errors, _ = open_page(browser, base, viewport={"width": 375, "height": 812}, mobile=True)
    q = page.evaluate
    q("() => openModal('tomato', false)")
    q("() => { const b = document.getElementById('modal-back'); b.scrollTop = b.scrollHeight; }")
    page.wait_for_timeout(150)
    top = q("document.getElementById('modal-close').getBoundingClientRect().top")
    s.check(0 <= top <= 40, f"phone: the guide's close button stays on screen while scrolling ({top:.0f}px from the top)")
    page.click(".mclose[data-close-modal]")
    s.check(not q("isModalOpen()"), "phone: a close button waits at the end of the guide")
    s.no_errors(errors, "small fixes / phone")
    ctx.close()

def test_newspaper(s, browser, base):
    ctx, page, errors, _ = open_page(browser, base)
    q = page.evaluate
    # calendar: NOAA's frost-risk band behind every row, solid where frost is near-certain
    band = q("""() => { const b = document.querySelectorAll('#cal-grid .cal-row')[0].querySelectorAll('.frost-band');
      const sp = b[0].style, fa = b[1].style;
      return { n: document.querySelectorAll('#cal-grid .cal-group:not(.pin) .cal-row .frost-band').length, rows: PLANTS.length,
               spring: [parseFloat(sp.left), parseFloat(sp.width), parseFloat(sp.getPropertyValue('--solid'))],
               fall: [parseFloat(fa.left), parseFloat(fa.width)], want: [pct(4, 24), pct(5, 21), pct(9, 21)] }; }""")
    s.check(band["n"] == band["rows"] * 2, f"calendar: a frost-risk band in spring and fall on every row ({band['n']})")
    s.check(band["spring"][0] == 0 and abs(band["spring"][1] - band["want"][1]) < 0.01 and abs(band["spring"][2] - band["want"][0] / band["want"][1] * 100) < 0.1
            and abs(band["fall"][0] - band["want"][2]) < 0.01, f"calendar: the bands follow NOAA's 9-in-10 and 1-in-10 dates ({band})")
    s.check("Frost risk" in q("document.querySelector('.legend').textContent"), "calendar: the legend names the frost-risk band")
    page.locator("#cal-grid .cal-row .frost-band.fall").first.hover()
    s.check("Oct 18" in q("tip.textContent"), "calendar: hovering the band gives NOAA's odds")
    # weather: a dated front with a headline, lede, and frost box; drawn forecast icons
    page.click("#tab-weather")
    front = q("""() => ({ date: document.getElementById('front-date').textContent, h: document.querySelector('#panel-weather h2').textContent,
      head: !!document.querySelector('.front .wx-status h3'), box: document.querySelector('#wx-tiles h4').textContent,
      rows: document.querySelectorAll('#wx-tiles .wxr').length, svgs: document.querySelectorAll('.wx-card .wxi svg').length,
      text: [...document.querySelectorAll('.wx-card .wxi')].map(e => e.textContent).join('') })""")
    s.check(front["date"] == "Thursday, September 24, 2026" and front["h"] == "This Week in the Garden" and front["head"],
            f"weather: a dated 'This Week in the Garden' front ({front['date']})")
    s.check(front["box"] == "Frost outlook" and front["rows"] == 3, "weather: the front's Frost outlook box")
    s.check(front["svgs"] == 7 and front["text"] == "", "weather: forecast icons are drawn, not emoji")
    # NWS words count, and a dawn frost belongs to the night before it: "Friday: Patchy Frost then
    # Sunny" after a 37°F night means cover tonight (the Oct 4, 2026 forecast had this shape)
    def frost_case(periods):
        q(f"() => renderForecast({periods}, new Date().toISOString())")
        return q("""() => ({ head: document.querySelector('.wx-status h3').textContent.replace(/\\u00a0/g, ' '), body: document.querySelector('.wx-status p').textContent,
          box: document.getElementById('wx-tiles').textContent,
          cards: [...document.querySelectorAll('.wx-card')].map(c => [c.querySelector('.day').textContent, c.classList.contains('frosty'), c.classList.contains('cold-low')]),
          tags: [...document.querySelectorAll('.wx-card')].map(c => ((c.querySelector('.frost') || c.querySelector('.dawn') || {}).textContent || '').replace(/^ · /, '')),
          emoji: /❄/.test(document.getElementById('wx-forecast').textContent), flakes: document.querySelectorAll('.wx-card .frost svg').length })""")
    dawn = frost_case("""[{ name: 'Today', isDaytime: true, temp: 65, cond: 'Sunny', pop: null },
      { name: 'Tonight', isDaytime: false, temp: 37, cond: 'Mostly Clear', pop: null },
      { name: 'Friday', isDaytime: true, temp: 63, cond: 'Patchy Frost then Sunny', pop: null },
      { name: 'Friday Night', isDaytime: false, temp: 45, cond: 'Clear', pop: null }]""")
    s.check(dawn["head"] == "Frost possible tonight — cover and pick"
            and "patchy frost early Friday morning, after tonight's low of 37°F" in dawn["body"] and "plants tonight" in dawn["body"]
            and "coldest low" not in dawn["body"], f"weather: a dawn frost is tied to the night before it ({dawn['body'][:110]})")
    s.check(dawn["cards"] == [["Today", True, True], ["Friday", False, False]],
            f"weather: the flag and the red low go on the card holding that night ({dawn['cards']})")
    s.check("NWS expects patchy frost early Friday morning" in dawn["box"], "weather: the Frost outlook box says when")
    s.check(not dawn["emoji"] and dawn["flakes"] == 1, "weather: the frost tag's snowflake is drawn, not an emoji")
    s.check(dawn["tags"] == ["frost by Friday dawn", "frost at dawn: cover the night before"],
            f"weather: the night card says which dawn; the day card says to cover the night before ({dawn['tags']})")
    s.check(q("document.querySelector('.wx-status h3').textContent.includes('\\u00a0—')"), "weather: the headline's dash stays with the word before it")
    lone = frost_case("""[{ name: 'Tonight', isDaytime: false, temp: 37, cond: 'Mostly Clear', pop: null },
      { name: 'Friday', isDaytime: true, temp: 63, cond: 'Patchy Frost then Sunny', pop: null },
      { name: 'Friday Night', isDaytime: false, temp: 45, cond: 'Clear', pop: null }]""")
    s.check(lone["cards"][:2] == [["Tonight", True, True], ["Friday", False, False]] and lone["tags"][0] == "frost by Friday dawn",
            f"weather: an evening fetch flags the lone Tonight card ({lone['cards']}, {lone['tags']})")
    later = frost_case("""[{ name: 'Today', isDaytime: true, temp: 60, cond: 'Sunny', pop: null },
      { name: 'Tonight', isDaytime: false, temp: 30, cond: 'Clear', pop: null },
      { name: 'Friday', isDaytime: true, temp: 58, cond: 'Sunny', pop: null },
      { name: 'Friday Night', isDaytime: false, temp: 38, cond: 'Patchy Frost', pop: null }]""")
    s.check(later["head"] == "Frost possible tonight — cover and pick" and "patchy frost Friday night, with a low of 38°F" in later["body"]
            and "coldest low in the forecast is 30°F tonight" in later["body"] and "plants tonight" in later["body"]
            and later["cards"] == [["Today", True, True], ["Friday", True, True]],
            f"weather: the headline names the first night to protect; both risky nights are flagged ({later['body'][:120]})")
    s.check(later["tags"] == ["frost risk", "frost risk"], f"weather: frost on a cold or frosty night keeps the plain tag ({later['tags']})")
    morning = frost_case("""[{ name: 'Today', isDaytime: true, temp: 55, cond: 'Areas of Frost then Sunny', pop: null },
      { name: 'Tonight', isDaytime: false, temp: 41, cond: 'Clear', pop: null }]""")
    s.check(morning["head"] == "Frost possible this morning — cover and pick" and "areas of frost this morning" in morning["body"]
            and morning["cards"] == [["Today", True, False]], f"weather: a frost this morning flags today ({morning['body'][:100]})")
    # Ask: published column answers replace the examples, with bylines
    q("""() => { COLUMN.push({ q: 'Can I still plant garlic?', a: 'Yes, through October.', asker: 'Pat, Weston',
      answeredBy: 'a Master Gardener volunteer', date: 'Oct 12, 2026', url: 'https://wausaupilotandreview.com/' }); renderAsk(); }""")
    col = q("() => [document.querySelector('.ask-side h3').textContent, document.querySelector('#ask-samples .qa .by').textContent]")
    s.check(col[0] == "From the Ask a Master Gardener column" and "Answered by a Master Gardener volunteer" in col[1] and "Read the column" in col[1],
            f"ask: published column answers show with bylines ({col[1][:80]})")
    q("() => { COLUMN.length = 0; renderAsk(); }")
    s.check(q("document.querySelector('.ask-side h3').textContent") == "Example answers", "ask: without column answers, the examples are labeled as examples")
    s.no_errors(errors, "newspaper")
    ctx.close()

def test_desktop_type(s, browser, base):
    # the month row tags its lines; on Oct 4 "Today" and the ~Oct 1 frost tag sit on opposite sides
    ctx, page, errors, _ = open_page(browser, base, when=central(2026, 10, 4), viewport={"width": 1280, "height": 900})
    q = page.evaluate
    tags = q("""() => { const row = document.querySelector('#cal-grid .cal-months'), r = e => e.getBoundingClientRect();
      const t = row.querySelector('.am.today'), f = row.querySelectorAll('.am.frost')[1];
      const tl = document.querySelector('#cal-grid .cal-row .today-line'), fl = document.querySelector('#cal-grid .cal-row .frost-line[data-frost=first]');
      return { today: t.textContent, frost: f.textContent, tSide: t.classList.contains('r'), fSide: f.classList.contains('l'),
               gap: r(t).left - r(f).right, tFromLine: r(t).left - r(tl).left, fFromLine: r(fl).left - r(f).right,
               rows: document.querySelectorAll('#cal-grid .cal-months .axis-marks').length, months: document.querySelectorAll('#cal-grid .cal-months').length }; }""")
    s.check(tags["today"] == "Today" and tags["frost"] == "~Oct 1 frost" and tags["tSide"] and tags["fSide"]
            and tags["gap"] > 0 and -1 <= tags["tFromLine"] <= 8 and -1 <= tags["fFromLine"] <= 8 and tags["rows"] == tags["months"],
            f"calendar: the month row tags Today and the frost line, on opposite sides of a 3-day gap ({tags})")
    # no screen text under 13px on desktop either, except WPR's flag
    page.click('#cal-filters .fbtn[data-view="list"]')
    small = q("""() => {
      const out = new Set();
      const scan = root => { for (const el of root.querySelectorAll('*')) {
        if (!el.offsetParent && getComputedStyle(el).position !== 'fixed') continue;
        if (el.closest('.dateline, .tagline, .fb-tag, .sr-only, .hp, .print-note')) continue;
        const own = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
        if (own && parseFloat(getComputedStyle(el).fontSize) < 13) out.add(`${el.className || el.tagName} "${el.textContent.trim().slice(0, 24)}" ${getComputedStyle(el).fontSize}`);
      } };
      for (const id of ['calendar', 'guides', 'weather', 'ask']) { showPanel(id, false); scan(document.body); }
      openModal('tomato', false); scan(document.getElementById('modal')); closeModalUI();
      return [...out]; }""")
    s.check(not small, f"desktop: no label under 13px outside WPR's flag ({small[:6]})")
    s.check(q("parseFloat(getComputedStyle(document.querySelector('.cal-trow .nm')).fontSize)") == 14
            and q("parseFloat(getComputedStyle(document.querySelector('#panel-calendar .sub')).fontSize)") == 15,
            "desktop: plant names at 14px, reading text at 15px")
    q("() => openModal('tomato', false)")
    link = q("""() => { const a = getComputedStyle(document.querySelector('#modal .ask-link'));
      return [a.color, a.marginTop, a.fontWeight, a.textDecorationLine, a.textAlign, a.fontSize]; }""")
    s.check(link == ["rgb(43, 101, 93)", "12px", "700", "none", "left", "13px"],
            f"guide: the Ask a gardener link keeps its teal, bold, spaced style over .linkish ({link})")
    q("() => closeModalUI()")
    s.no_errors(errors, "desktop type")
    ctx.close()

def test_frost_alert(s, browser, base):
    # a spring frost night reaches past This Week: the season line, the tab, and "Plant now"
    ctx, page, errors, _ = open_page(browser, base, when=central(2027, 5, 28), low=34)
    q = page.evaluate
    pulse = q("document.getElementById('season-pulse').textContent")
    s.check(pulse.startswith("Frost possible tonight (34°F): hold off on tender crops") and "This Week" in pulse,
            f"frost alert: the season line becomes the warning ({pulse[:80]})")
    s.check(not q("document.querySelector('#tab-weather .tab-alert').hidden")
            and "frost possible tonight" in q("document.getElementById('tab-weather').textContent"),
            "frost alert: the This Week tab carries a frost mark, with words for screen readers")
    chips = q("() => ['tomato', 'hosta'].map(id => { const c = document.querySelector(`.pcard[data-open=${id}] .now-chip`); return c ? c.textContent : ''; })")
    s.check(chips == ["Wait: frost tonight", "Plant now"], f"frost alert: an after-frost window says wait; an earlier one doesn't ({chips})")
    s.check("wait: frost tonight" in q("document.getElementById('wx-windows').textContent"), "frost alert: This Week's open windows say wait too")
    page.click("#season-pulse [data-goto]")
    s.check(q("currentPanel") == "weather", "frost alert: the season line's link opens This Week")
    q("""() => renderForecast([{ name: 'Tonight', isDaytime: false, temp: 50, cond: 'Clear', pop: null },
      { name: 'Saturday', isDaytime: true, temp: 72, cond: 'Sunny', pop: null }, { name: 'Saturday Night', isDaytime: false, temp: 52, cond: 'Clear', pop: null }], new Date().toISOString())""")
    calm = q("""() => [document.getElementById('season-pulse').textContent, document.querySelector('#tab-weather .tab-alert').hidden,
      document.querySelector('.pcard[data-open=tomato] .now-chip').textContent]""")
    s.check(calm[0].startswith("Day ") and calm[1] and calm[2] == "Plant now", f"frost alert: a frost-free forecast puts it all back ({calm})")
    s.no_errors(errors, "frost alert")
    ctx.close()
    # inside an article too: the embed keeps the season line, so the warning leads there
    ctx = browser.new_context(timezone_id=TZ)
    page = ctx.new_page()
    stub_network(page, central(2026, 10, 10), low=34)
    page.set_content(f'<iframe src="{base}/index.html" style="width:375px;height:900px;border:0"></iframe>')
    frame = None
    for _ in range(50):
        frame = next((f for f in page.frames if f.url.startswith(base)), None)
        if frame:
            break
        page.wait_for_timeout(100)
    frame.wait_for_function("() => document.getElementById('season-pulse').classList.contains('alert')", timeout=10000)
    s.check(frame.evaluate("document.body.classList.contains('embedded')")
            and frame.evaluate("document.getElementById('season-pulse').textContent").startswith("Frost possible tonight (34°F): cover tender plants"),
            "frost alert: an article embed leads with the warning")
    ctx.close()

def test_pressed_states(s, browser, base):
    # filter chips and view buttons say which is on; the calendar says what it now shows
    ctx, page, errors, _ = open_page(browser, base)
    q = page.evaluate
    s.check(q("document.querySelector('#cal-filters .fbtn[data-cat=all]').getAttribute('aria-pressed')") == "true"
            and q("document.querySelector('#cal-filters .fbtn[data-cat=veg]').getAttribute('aria-pressed')") == "false"
            and q("document.querySelector('#cal-filters .fbtn[data-view=chart]').getAttribute('aria-pressed')") == "true"
            and q("document.getElementById('ics-btn').hasAttribute('aria-pressed')") is False,
            "a11y: chips and view buttons report aria-pressed; action buttons don't")
    page.click('#cal-filters .fbtn[data-cat="veg"]')
    veg = q("PLANTS.filter(p => p.cat === 'veg').length")
    live = q("document.getElementById('cal-live').textContent")
    s.check(q("document.querySelector('#cal-filters .fbtn[data-cat=veg]').getAttribute('aria-pressed')") == "true"
            and q("document.querySelector('#cal-filters .fbtn[data-cat=all]').getAttribute('aria-pressed')") == "false"
            and live == f"Showing {veg} vegetables.",
            f"a11y: choosing Vegetables updates the pressed state and announces it ({live})")
    page.click('#cal-filters .fbtn[data-view="list"]')
    s.check(q("document.getElementById('cal-live').textContent") == f"Showing {veg} vegetables, as a list."
            and q("document.querySelector('#cal-filters .fbtn[data-view=list]').getAttribute('aria-pressed')") == "true",
            "a11y: the List view is announced and pressed")
    page.click('#cal-filters .fbtn[data-cat="fav"]')
    s.check(q("document.getElementById('cal-live').textContent") == "No plants starred yet."
            and "Reminders" in q("document.querySelector('#cal-grid .empty').textContent"),
            "a11y: an empty My plants view says so, and explains starring and Reminders")
    page.click("#tab-guides")
    page.click('#guide-filters .fbtn[data-cat="herb"]')
    s.check(q("document.querySelector('#guide-filters .fbtn[data-cat=herb]').getAttribute('aria-pressed')") == "true"
            and q("document.querySelector('#guide-filters .fbtn[data-cat=all]').getAttribute('aria-pressed')") == "false",
            "a11y: guide chips report aria-pressed too")
    s.no_errors(errors, "pressed states")
    ctx.close()

def test_new_plants(s, browser, base):
    # the Oct 4 additions: drawn, sourced, and coleus shows a leaf-color season rather than a bloom window
    ctx, page, errors, _ = open_page(browser, base)
    q = page.evaluate
    ids = ["brunnera", "tall-sedum", "coleus"]
    s.check(q(f"{ids}.every(id => PLANT_BY_ID[id] && ART[id])") and q("PLANTS.length") == 79
            and q("document.getElementById('plant-count').textContent") == "79",
            "new plants: brunnera, tall sedum, and coleus are in, each with its own drawing (79 plants)")
    s.check(q("['bugbane', 'japanese-anemone', 'anemone', 'actaea'].every(id => !PLANT_BY_ID[id])"), "new plants: bugbane and Japanese anemone stay out")
    page.click('#cal-filters .fbtn[data-view="list"]')
    row = q("[...document.querySelectorAll('.cal-trow')].find(r => r.querySelector('.nm').dataset.open === 'coleus').textContent")
    s.check("Leaf color: Jun 1 – Sep 30" in row and "Harvest" not in row, f"new plants: coleus lists a leaf-color season ({row.strip()[:90]})")
    q("() => openModal('coleus', false)")
    key = q("document.querySelector('#modal .mc-key').textContent")
    s.check("Leaf color" in key and "Harvest" not in key, "new plants: the coleus guide's timeline says Leaf color")
    s.no_errors(errors, "new plants")
    ctx.close()

def test_harvest_labels_and_bulbs(s, browser, base):
    # vegetables and herbs harvest, flowers bloom, and next year's harvest says so; plain-word "harden off";
    # bulb searches say bulbs aren't in the guides; names keep their proper nouns mid-sentence
    ctx, page, errors, _ = open_page(browser, base)
    q = page.evaluate
    page.click('#cal-filters .fbtn[data-view="list"]')
    rows = q("Object.fromEntries(['garlic', 'parsnip', 'tomato', 'peony', 'basil'].map(id => [id, [...document.querySelectorAll('.cal-trow')].find(r => r.querySelector('.nm').dataset.open === id).textContent.replace(/\\s+/g, ' ')]))")
    s.check("Harvest the following summer: Jul 10 – Jul 31" in rows["garlic"] and "bloom" not in rows["garlic"].lower(),
            f"labels: garlic's harvest is the following summer ({rows['garlic'][-60:]})")
    pr = rows["parsnip"]
    s.check("Harvest: Oct 10 – Oct 31" in pr and pr.find("Direct sow") < pr.find("Harvest the following spring"),
            f"labels: overwintered parsnips list after the sowing ({pr[-110:]})")
    s.check("Harvest: " in rows["tomato"] and "bloom" not in rows["tomato"].lower() and "Bloom: Jun 1 – Jun 25" in rows["peony"] and "Harvest: " in rows["basil"],
            "labels: vegetables and herbs harvest, flowers bloom")
    keys = {}
    for pid in ("tomato", "peony", "basil", "chives"):
        q(f"() => openModal('{pid}', false)")
        keys[pid] = q("[...document.querySelectorAll('#modal .spec .k')].map(k => k.textContent).join('|')")
        page.keyboard.press("Escape")
    s.check("Time to harvest" in keys["tomato"] and "Time to harvest" in keys["basil"] and "Season" in keys["peony"] and "Season" in keys["chives"]
            and not any("bloom" in v for v in keys.values()), f"labels: the guide's timing box says harvest or season ({keys})")
    s.check("a little longer each day" in q("PLANT_BY_ID.tomato.tip") and "a little longer each day" in q("MONTH_TASKS[5].join(' ')")
            and "harden off" not in q("MONTH_TASKS[8].join(' ')") and "bulbs" not in q("MONTH_TASKS[9].join(' ')"),
            "copy: harden off is explained where it's used; September tasks don't promise bulbs")
    names = q("['st-johns-wort', 'black-eyed-susan', 'brussels-sprouts', 'cuphea', 'solomons-seal', 'pepper'].map(id => sentenceName(PLANT_BY_ID[id]))")
    s.check(names == ["St. John’s wort", "black-eyed Susan", "Brussels sprouts", "cigar plant (Cuphea)", "Solomon’s seal", "bell pepper"],
            f"copy: plant names mid-sentence keep their proper nouns ({names})")
    q("() => showPanel('guides', false)")
    page.fill("#guide-search", "tulip")
    empty = q("() => { const e = document.querySelector('#guide-cards .empty'); return [e.hidden, e.textContent, !!e.querySelector('#clear-search'), Math.round(e.getBoundingClientRect().width)]; }")
    s.check(not empty[0] and "Spring bulbs like tulips and daffodils aren’t in the guides yet" in empty[1] and empty[2] and empty[3] > 600,
            f"search: tulip says spring bulbs aren't in the guides yet, across the grid ({empty[1][:70]}, {empty[3]}px)")
    page.fill("#guide-search", "bulb")
    shown = q("() => [document.querySelector('#guide-cards .empty').hidden, document.querySelectorAll('#guide-cards .pcard:not([hidden])').length]")
    s.check(not shown[0] and shown[1] > 0, f"search: bulb shows the note above the guides that mention bulbs ({shown})")
    page.fill("#guide-search", "")
    s.check(q("document.querySelector('#guide-cards .empty').hidden"), "search: clearing it hides the note")
    s.no_errors(errors, "harvest labels")
    ctx.close()
    # phone: the first-star toast spans the screen, is centered, and stays at least 6 s
    ctx, page, errors, _ = open_page(browser, base, viewport={"width": 375, "height": 812}, mobile=True)
    q = page.evaluate
    q("() => showPanel('guides', false)")
    q("() => toggleFav('garlic')")
    tb = q("() => { const r = document.getElementById('toast').getBoundingClientRect(); return [Math.round(r.left), Math.round(r.width), getComputedStyle(document.getElementById('toast')).transform, toastLeft]; }")
    s.check(tb[1] >= 330 and abs(tb[0] - (375 - tb[1]) / 2) <= 1 and tb[2] == "none" and tb[3] >= 6000,
            f"toast: on phones it spans the screen, centered, and stays at least 6 s ({tb})")
    page.dispatch_event("#toast", "pointerenter")
    held = q("() => toastLeft")
    page.dispatch_event("#toast", "pointerleave")
    s.check(held >= 1500 and q("getComputedStyle(document.getElementById('toast')).display") == "block", "toast: it holds while pressed, then counts down again")
    s.no_errors(errors, "toast")
    ctx.close()

def test_open_now_group(s, browser, base):
    # All leads with what's open now: taller pinned rows whose bands stay continuous, deadline lines, the
    # List too, "See all" past six, no group in other filters or in print
    ctx, page, errors, _ = open_page(browser, base)
    q = page.evaluate
    geo = q("""() => { const r = document.querySelector('#cal-grid .cal-row.pin'), t = r.querySelector('.cal-track'), m = r.querySelector('.mband'),
      reg = document.querySelector('#cal-grid .cal-group:not(.pin) .cal-row'), rr = r.getBoundingClientRect(), mb = m.getBoundingClientRect();
      return { row: Math.round(rr.height), band: Math.round(mb.height), regular: Math.round(reg.getBoundingClientRect().height),
               due: getComputedStyle(r.querySelector('.cal-due')).fontSize, label: r.querySelector('.cal-name').textContent.replace(/\\s+/g, ' ').trim() }; }""")
    s.check(geo["row"] == 44 and abs(geo["band"] - geo["row"]) <= 1 and geo["regular"] == 30 and geo["due"] == "13px" and geo["label"] == "Solomon’s Seal, open now: Plant through Sep 25",
            f"open now: pinned rows are 44px with bands to match; the name cell reads its deadline ({geo})")
    page.click('#cal-filters .fbtn[data-view="list"]')
    lst = q("() => { const g = document.querySelector('#cal-grid .cal-group.pin'); return g && [...g.querySelectorAll('.cal-trow')].map(r => r.textContent.replace(/\\s+/g, ' ').trim().slice(0, 60)); }")
    s.check(lst and len(lst) == 2 and lst[1].startswith("Peony, open now: Plant through Oct 15 Plant bare-root"), f"open now: the List leads with the same group ({lst})")
    page.click('#cal-filters .fbtn[data-view="chart"]')
    for cat in ("veg", "now", "fav"):
        page.click(f'#cal-filters .fbtn[data-cat="{cat}"]')
        s.check(q("!document.querySelector('#cal-grid .cal-group.pin')"), f"open now: no pinned group in the {cat} filter")
    page.click('#cal-filters .fbtn[data-cat="all"]')
    page.emulate_media(media="print")
    s.check(q("getComputedStyle(document.querySelector('#cal-grid .cal-group.pin')).display") == "none", "open now: the fridge printout leaves the group out")
    page.emulate_media(media="screen")
    s.no_errors(errors, "open now")
    ctx.close()
    # late May: more than six open, so the group shows the six closing soonest and links to the rest
    ctx, page, errors, _ = open_page(browser, base, when=central(2027, 5, 20))
    q = page.evaluate
    more = q("""() => { const g = document.querySelector('#cal-grid .cal-group.pin'), b = g.querySelector('[data-showcat]');
      return { rows: g.querySelectorAll('.cal-row').length, head: g.querySelector('.cal-group-h span').textContent, link: b && b.textContent,
               open: PLANTS.filter(p => openBarsFor(p).length).length }; }""")
    s.check(more["rows"] == 6 and more["open"] > 6 and more["head"] == f"6 of {more['open']}" and more["link"] == f"See all {more['open']} open now",
            f"open now: past six, the group links to every open window ({more})")
    last = q("() => [document.querySelector('#cal-grid .cal-group.pin .cal-due').textContent, getComputedStyle(document.querySelector('#cal-grid .cal-group.pin .cal-group-h span')).textTransform]")
    s.check(last == [", open now: Plant through today", "none"], f"open now: a window's last day says today, and the count isn't shouted ({last})")
    page.click("#cal-grid [data-showcat]")
    s.check(q("calCat") == "now" and q("document.querySelector('#cal-filters .fbtn[data-cat=now]').classList.contains('on')")
            and q("document.querySelectorAll('#cal-grid .cal-row').length") == more["open"], "open now: See all switches to the Open now filter")
    s.no_errors(errors, "open now (May)")
    ctx.close()

def test_keyboard_path(s, browser, base):
    # a way past the chart, names described by their status, a keyboard-reachable Mar–Jul cue, focus that
    # lands somewhere after a linked guide closes, and the teal ring on List names too
    ctx, page, errors, _ = open_page(browser, base)
    q = page.evaluate
    page.focus("#print-btn")
    page.keyboard.press("Tab")
    skip = q("() => { const a = document.activeElement, r = a.getBoundingClientRect(); return [a.id, a.textContent, Math.round(r.width), Math.round(r.height)]; }")
    s.check(skip[0] == "cal-skip" and skip[1] == "Skip past the calendar (79 plants)" and skip[2] > 150 and skip[3] >= 30,
            f"keyboard: right after the toolbar, a visible link skips the calendar ({skip})")
    page.keyboard.press("Enter")
    s.check(q("document.activeElement.id") == "page-foot" and q("location.hash") != "#page-foot" and q("currentPanel") == "calendar",
            "keyboard: the skip link lands on the footer and leaves the tab alone")
    page.keyboard.press("Tab")
    s.check(q("!!document.activeElement.closest('footer')"), "keyboard: the next Tab continues in the footer")
    sums = q("""() => Object.fromEntries([['garlic', '.cal-group:not(.pin) .cal-name[data-open=garlic]'], ['peony', '.cal-group:not(.pin) .cal-name[data-open=peony]'],
      ['pin', '.cal-group.pin .cal-name[data-open=peony]'], ['tomato', '.cal-group:not(.pin) .cal-name[data-open=tomato]']].map(([k, sel]) => {
        const n = document.querySelector(sel); return [k, document.getElementById(n.getAttribute('aria-describedby')).textContent]; }))""")
    s.check(sums["garlic"].startswith("Next window: plant cloves opens Oct 1. Plant cloves Oct 1 to Oct 25")
            and sums["peony"].startswith("Open now: plant bare-root divisions through Oct 15. Plant bare-root divisions Sep 15 to Oct 15")
            and sums["pin"].startswith("Plant bare-root divisions Sep 15 to Oct 15") and sums["tomato"].startswith("Next window: start indoors opens Apr 10 next year."),
            f"screen readers: each calendar name is described by its status, then its windows ({sums})")
    page.keyboard.press("Tab")
    q("() => document.querySelector('.cal-group:not(.pin) .cal-name').focus()")
    s.check(q("getComputedStyle(document.activeElement).outlineOffset") == "-2px", "keyboard: the chart's name ring sits inside the pinned column")
    page.click('#cal-filters .fbtn[data-view="list"]')
    lst = q("""() => { const n = document.querySelector('.cal-group:not(.pin) .cal-trow .nm[data-open=garlic]'), row = n.closest('.cal-trow');
      return [document.getElementById(n.getAttribute('aria-describedby')).textContent.slice(0, 40), [...row.querySelectorAll('.win')].every(w => w.getAttribute('aria-hidden') === 'true')]; }""")
    s.check(lst[0].startswith("Next window: plant cloves opens Oct 1.") and lst[1], f"screen readers: the List says the same, once ({lst})")
    page.keyboard.press("Tab")
    ring = q("""() => { document.querySelector('.cal-trow .nm').focus(); const t = document.createElement('i'); t.style.color = 'var(--accent)'; document.body.appendChild(t);
      const acc = getComputedStyle(t).color; t.remove(); const cs = getComputedStyle(document.activeElement); return [cs.outlineStyle, cs.outlineColor === acc]; }""")
    s.check(ring == ["solid", True], f"keyboard: List names get the teal focus ring ({ring})")
    page.click('#cal-filters .fbtn[data-cat="now"]')
    s.check(q("document.getElementById('cal-skip').hidden"), "keyboard: a short calendar needs no skip link")
    s.no_errors(errors, "keyboard path")
    ctx.close()
    # a guide opened from a link hands focus to the tab's heading when it closes
    ctx, page, errors, _ = open_page(browser, base, path="/index.html#plant/garlic")
    page.wait_for_function("() => isModalOpen()")
    page.keyboard.press("Escape")
    page.wait_for_function("() => !isModalOpen()")
    s.check(page.evaluate("document.activeElement.matches('.panel.active h2')"), "keyboard: closing a linked guide focuses the tab's heading")
    s.no_errors(errors, "keyboard path (deep link)")
    ctx.close()
    # phone: the Mar–Jul cue is a real button, the only one exposed in the month rows
    ctx, page, errors, _ = open_page(browser, base, viewport={"width": 375, "height": 812}, mobile=True)
    page.click('#cal-filters .fbtn[data-cat="all"]')
    page.wait_for_function("() => !document.querySelector('#cal-grid .cal-back').hidden")
    cue = page.evaluate("""() => { const b = document.querySelector('#cal-grid .cal-back'), m = b.closest('.cal-months');
      return { tag: b.tagName, label: b.textContent.includes('Scroll the chart back to March'), rowHidden: m.hasAttribute('aria-hidden'),
               cellsHidden: [...m.children].slice(1).every(c => c.getAttribute('aria-hidden') === 'true'),
               others: [...document.querySelectorAll('#cal-grid .cal-months')].slice(1).every(r => r.getAttribute('aria-hidden') === 'true' && r.querySelector('.cal-back').tagName === 'SPAN') }; }""")
    s.check(cue == {"tag": "BUTTON", "label": True, "rowHidden": False, "cellsHidden": True, "others": True}, f"keyboard: the first Mar–Jul cue is a named button; the rest stay decoration ({cue})")
    page.focus("#cal-grid button.cal-back")
    page.keyboard.press("Enter")
    page.wait_for_function("() => document.querySelector('.cal-scroll').scrollLeft < 2")
    s.check(page.evaluate("document.activeElement.matches('.cal-name')"), "keyboard: Enter on the cue scrolls back to March and keeps focus in the chart")
    s.no_errors(errors, "keyboard path (phone)")
    ctx.close()

def test_round6_fixes(s, browser, base):
    # guides close where you left them, Tab stays in an open guide, starred plants lead the pinned group,
    # Guides follow the season, phone List rows read as text, the Open now chip names its filter
    ctx, page, errors, _ = open_page(browser, base)
    q = page.evaluate
    page.click("#tab-guides")
    pid = q("() => { const c = document.querySelectorAll('#guide-cards .pcard')[30]; c.scrollIntoView({block: 'center'}); return c.dataset.open; }")
    # off-screen guide cards are laid out at an estimated height until they're drawn: let the jump settle first
    q("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
    page.wait_for_timeout(150)
    before = q("Math.round(scrollY)")
    card_top = lambda: q(f"Math.round(document.querySelector('.pcard[data-open=\"{pid}\"]').getBoundingClientRect().top)")
    top_before = card_top()
    s.check(before > 600 and q("location.hash") == "#guides", f"close: reader is well down the Guides tab ({before})")
    page.click(f'.pcard[data-open="{pid}"] .cardbtn')
    page.wait_for_function("() => isModalOpen()")
    s.check(q("document.getElementById('skip-link').inert") is True, "close: with a guide open, the page's skip link is out of the Tab order too")
    page.keyboard.press("Escape")
    page.wait_for_function("() => !isModalOpen()")
    page.wait_for_timeout(200)
    after, top_after = q("Math.round(scrollY)"), card_top()
    # what the reader sees: the card stays where it was on screen (the raw offset may differ by a few pixels when
    # off-screen cards above settle from their estimated height)
    s.check(abs(top_after - top_before) <= 4 and abs(after - before) <= 12 and q(f"!!document.activeElement.closest('.pcard[data-open=\"{pid}\"]')") and not q("document.getElementById('skip-link').inert"),
            f"close: closing a guide leaves the page where it was, focus on its card ({pid}: card top {top_before} -> {top_after}, scroll {before} -> {after})")
    order = q("[...document.querySelectorAll('#guide-cards .pcard')].slice(0, 3).map(c => c.dataset.open).join()")
    s.check(order == "solomons-seal,peony,garlic" and q("document.getElementById('guide-count').textContent") == "79 plants, soonest first",
            f"guides: cards follow the season, open now first, then the next to open ({order})")
    page.click('.pcard[data-open="peony"] .star')
    q("() => showPanel('calendar', false)")
    ids = q("[...document.querySelectorAll('#cal-grid .cal-group.pin .cal-name')].map(e => e.dataset.open).join()")
    s.check(ids == "peony,solomons-seal", f"open now: starred plants lead the pinned group ({ids})")
    s.no_errors(errors, "round 6 (desktop)")
    ctx.close()
    # phone: List windows read as text; See all lands on a visible, pressed Open now chip
    ctx, page, errors, _ = open_page(browser, base, when=central(2026, 10, 5), viewport={"width": 375, "height": 812}, mobile=True)
    q = page.evaluate
    win = q("""() => { const w = [...document.querySelectorAll('#cal-grid .cal-trow .win')].find(x => x.textContent.includes('Harvest the following summer'));
      const b = w.querySelector('b'); return [getComputedStyle(w).display, Math.round(b.getBoundingClientRect().height), b.getClientRects().length]; }""")
    s.check(win[0] == "block" and win[2] == 1 and win[1] <= 22, f"phone list: a window wraps as text and keeps its dates on one line ({win})")
    ctx.close()
    ctx, page, errors, _ = open_page(browser, base, when=central(2027, 5, 20), viewport={"width": 375, "height": 812}, mobile=True)
    q = page.evaluate
    page.click('#cal-filters .fbtn[data-cat="all"]')
    page.click("#cal-grid [data-showcat]")
    chip = q("() => { const c = document.querySelector('#cal-filters .fbtn[data-cat=now]'); return [getComputedStyle(c).display !== 'none', c.classList.contains('on'), c.getAttribute('aria-pressed')]; }")
    s.check(chip == [True, True, "true"], f"phone: See all lands on a visible, pressed Open now chip ({chip})")
    page.click('#cal-filters .fbtn[data-cat="all"]')
    s.check(q("getComputedStyle(document.querySelector('#cal-filters .fbtn[data-cat=now]')).display") == "none", "phone: the chip steps back once another filter is chosen")
    s.no_errors(errors, "round 6 (phone)")
    ctx.close()
    # a returning reader whose starred window closes today hears "closes today", not "closes in 0 days"
    ctx, page, errors, _ = open_page(browser, base, when=central(2026, 9, 25),
                                     storage={"wg:visit": json.dumps((FIXED - timedelta(days=30)).isoformat()), "wg:favs": json.dumps(["solomons-seal"])})
    welcome = page.evaluate("(document.querySelector('.welcome') || {}).textContent || ''")
    s.check("last day to plant" in welcome and "0 days" not in welcome, f"welcome: a window's last day says so ({' '.join(welcome.split())[-90:]})")
    ctx.close()

def test_phone_first_screen(s, browser, base):
    # late May on a phone: one toolbar row, a one-line note, folded zone facts, Reminders/Print in the footer,
    # and the first plant row in reach; a returning reader's note stays two lines with starred windows first
    may = central(2027, 5, 20)
    ctx, page, errors, _ = open_page(browser, base, when=may, viewport={"width": 375, "height": 812}, mobile=True)
    q = page.evaluate
    geo = q("""() => { const R = s => document.querySelector(s).getBoundingClientRect(), vis = s => getComputedStyle(document.querySelector(s)).display !== 'none';
      return { row: Math.round(R('#cal-grid .cal-row').top + scrollY), note: Math.round(R('.cal-short-note').height), noteText: document.querySelector('.cal-short-note .t-short').textContent,
               zone: Math.round(R('.zone-strip').height), chipsTop: Math.round(R('#cal-filters .chips').top), chipsW: Math.round(R('#cal-filters .chips').width),
               head: document.querySelector('.cal-head .cal-count').textContent, headSeg: !!document.querySelector('.cal-head .seg [data-view=list]'), tools: vis('#cal-filters .cal-tools'),
               toolbarActs: document.querySelector('#cal-filters .cal-acts').getClientRects().length > 0, footIcs: vis('.foot-actions [data-act=ics]'), footPrint: vis('.foot-actions [data-act=print]'),
               summary: document.querySelector('.zone-about summary').textContent }; }""")
    # CI renders without the web fonts, which makes the masthead taller: measure from the toolbar, plus a loose budget
    s.check(geo["row"] - geo["chipsTop"] <= 225 and geo["row"] <= 720 and geo["zone"] <= 46 and geo["chipsW"] >= 340 and not geo["tools"] and geo["headSeg"],
            f"phone: in late May the first plant row is on the first screen: one toolbar row, a one-line note, folded zone facts ({geo})")
    s.check(geo["noteText"] == "12 of 79 · open now or soon" and geo["head"] == geo["noteText"] and "Zone 4b / 5a and frost dates" in geo["summary"],
            f"phone: the note and the zone disclosure say what they hold ({geo['noteText']} / {geo['summary']})")
    s.check(not geo["toolbarActs"] and geo["footIcs"] and geo["footPrint"], f"phone: Reminders and Print move to the footer actions ({geo})")
    short = q("() => [document.querySelectorAll('#cal-grid .cal-group').length, document.querySelectorAll('#cal-grid .cal-group-h').length, (o => o.indexOf(false) < 0 || o.slice(o.indexOf(false)).every(x => !x))([...document.querySelectorAll('#cal-grid .cal-name')].map(e => openBarsFor(PLANT_BY_ID[e.dataset.open]).length > 0))]")
    s.check(short == [1, 0, True], f"phone: Now & next is one list with no category heads: open windows first, then what opens next ({short})")
    page.click('.foot-actions [data-act="ics"]')
    s.check("Star a few plants first" in q("document.getElementById('toast').textContent"), "phone: the footer's Reminders works like the toolbar's")
    page.click("#tab-weather")
    wx = q("""() => { const el = document.getElementById('wx-windows'), h4 = el.querySelector('h4');
      const openRows = [...el.querySelectorAll('.task')].filter(r => !h4 || (r.compareDocumentPosition(h4) & 4)).length;
      const soonRows = h4 ? [...el.querySelectorAll('h4 ~ .task')].length : 0;
      return { openRows, soonRows, see: (el.querySelector('[data-see-open]') || {}).textContent, more: (el.querySelector('[data-more-soon]') || {}).textContent,
               openPlants: PLANTS.filter(p => openBarsFor(p).length).length, h: Math.round(el.getBoundingClientRect().height) }; }""")
    s.check(wx["openRows"] == 6 and wx["soonRows"] == 4 and wx["see"] == f"See all {wx['openPlants']} open in the calendar →" and (wx["more"] or "").startswith("Show ")
            and wx["h"] < 1200, f"this week: six open windows and four opening, with ways to the rest ({wx})")
    page.click("#wx-windows [data-more-soon]")
    more = q("() => [document.querySelectorAll('#wx-windows h4 ~ .task').length, !!document.querySelector('#wx-windows [data-more-soon]'), document.activeElement.matches('#wx-windows h4 ~ .task [data-open]')]")
    s.check(more[0] > 4 and not more[1] and more[2], f"this week: Show more reveals every window opening soon and keeps focus there ({more})")
    page.click("#wx-windows [data-see-open]")
    s.check(q("currentPanel") == "calendar" and q("calCat") == "now" and q("document.querySelector('#cal-filters .fbtn[data-cat=now]').classList.contains('on')"),
            "this week: See all opens the calendar's Open now view")
    s.no_errors(errors, "phone first screen")
    ctx.close()
    # returning reader in late May with starred plants: urgent first, two lines on a phone
    ctx, page, errors, _ = open_page(browser, base, when=may, viewport={"width": 375, "height": 812}, mobile=True,
                                     storage={"wg:visit": json.dumps("2027-04-01T12:00:00"), "wg:favs": json.dumps(["broccoli", "pepper", "basil"])})
    w = page.evaluate("""() => { const t = document.querySelector('.welcome .wtext'), lh = parseFloat(getComputedStyle(t).lineHeight) || 22;
      return { text: t.textContent.replace(/\\s+/g, ' ').trim(), lines: Math.round(t.getBoundingClientRect().height / lh), row: Math.round(document.querySelector('#cal-grid .cal-row, #cal-grid .cal-trow').getBoundingClientRect().top + scrollY) }; }""")
    s.check(w["text"].startswith("Welcome back. ★ Broccoli — last day to plant") and w["lines"] <= 3,
            f"welcome: starred windows closing soon lead, and the note stays two lines on a phone ({w})")
    s.no_errors(errors, "phone first screen (returning)")
    ctx.close()
    # desktop keeps its toolbar Reminders and Print, and the footer copies stay hidden
    ctx, page, errors, _ = open_page(browser, base)
    q = page.evaluate
    d = q("() => ['#ics-btn', '#print-btn', '.foot-actions [data-act=ics]', '.zone-chip'].map(s => getComputedStyle(document.querySelector(s)).display !== 'none')")
    s.check(d == [True, True, False, False], f"desktop: Reminders and Print stay in the toolbar; the zone facts fold into the season line ({d})")
    ctx.close()

def test_round7_fixes(s, browser, base):
    # guides open at the top; focus returns to what was tapped; See all / Show all keep focus on the filter;
    # phone chips get their row back with Chart/List on the card's first line; Reminders carry alerts
    ctx, page, errors, _ = open_page(browser, base)
    q = page.evaluate
    page.click("#tab-guides")
    page.click('.pcard[data-open="garlic"] .art')
    page.wait_for_function("() => isModalOpen()")
    q("() => { document.getElementById('modal-back').scrollTop = 500; }")
    page.keyboard.press("Escape")
    page.wait_for_function("() => !isModalOpen()")
    s.check(q("document.activeElement.matches('.pcard[data-open=garlic] .cardbtn')"), "focus: a guide opened by tapping its card returns focus to that card")
    page.click('.pcard[data-open="peony"] .art')
    page.wait_for_function("() => isModalOpen()")
    s.check(q("document.getElementById('modal-back').scrollTop") == 0, "guides: the next guide opens at its top, not at the last one's scroll")
    page.keyboard.press("Escape")
    page.wait_for_function("() => !isModalOpen()")
    q("() => { FAVS.add('garlic'); }")
    ics = q("() => buildICS(new Date(2026, 8, 24)).ics")
    s.check(ics.count("BEGIN:VALARM") == ics.count("BEGIN:VEVENT") > 0 and "TRIGGER:-P2DT15H" in ics
            and "Garlic: plant cloves Oct 1 – Oct 25 in Wausau" in ics.replace("\r\n ", ""),
            "reminders: every event carries an alert, and descriptions read forward")
    q("() => { FAVS.delete('garlic'); }")
    s.no_errors(errors, "round 7 (desktop)")
    ctx.close()
    # late May: See all (pinned and This Week) keeps focus on the pressed chip
    may = central(2027, 5, 20)
    ctx, page, errors, _ = open_page(browser, base, when=may)
    q = page.evaluate
    page.click("#cal-grid [data-showcat]")
    s.check(q("document.activeElement.matches('#cal-filters .fbtn[data-cat=now].on')"), "focus: the pinned group's See all lands on the pressed Open now chip")
    page.click('#cal-filters .fbtn[data-cat="all"]')
    page.click("#tab-weather")
    page.click("#wx-windows [data-see-open]")
    s.check(q("currentPanel") == "calendar" and q("document.activeElement.matches('#cal-filters .fbtn[data-cat=now].on')"),
            "focus: This Week's See all opens the calendar with focus on the pressed chip")
    s.no_errors(errors, "round 7 (May)")
    ctx.close()
    # phone: chips keep the row; Chart/List on the card's first line; pressed chip in view; My plants moves up
    ctx, page, errors, _ = open_page(browser, base, when=may, viewport={"width": 375, "height": 812}, mobile=True)
    q = page.evaluate
    page.click('#cal-filters .fbtn[data-cat="all"]')
    page.click("#cal-grid [data-showcat]")
    chip = q("() => { const s = document.querySelector('#cal-filters .chips').getBoundingClientRect(), c = document.querySelector('#cal-filters .fbtn[data-cat=now]').getBoundingClientRect(); return [Math.round(c.left), Math.round(c.right), Math.round(s.right)]; }")
    s.check(chip[0] >= 0 and chip[1] <= chip[2], f"phone: after See all the pressed chip scrolls fully into view ({chip})")
    page.click('#cal-head [data-view="list"]')
    s.check(q("calView") == "list" and q("document.activeElement.matches('#cal-head [data-view=list].on')")
            and q("document.querySelector('#cal-head .cal-count').textContent") == "52 open now",
            "phone: the card's Chart/List switch works, keeps focus, and names what's shown")
    page.click('#cal-head [data-view="chart"]')
    q("() => showPanel('guides', false)")
    page.click('.pcard[data-open="tomato"] .star')
    order = q("[...document.querySelectorAll('#cal-filters .chips .fbtn')].map(b => b.dataset.cat).slice(0, 3).join()")
    s.check(order == "short,fav,all", f"phone: once something is starred, My plants sits beside Now & next ({order})")
    page.click('.pcard[data-open="tomato"] .star')
    order = q("[...document.querySelectorAll('#cal-filters .chips .fbtn')].map(b => b.dataset.cat).join()")
    s.check(order.endswith("herb,fav"), f"phone: with nothing starred, it goes back to the end ({order})")
    s.no_errors(errors, "round 7 (phone)")
    ctx.close()
    # phone today: Show all lands focus on the All chip
    ctx, page, errors, _ = open_page(browser, base, when=central(2026, 10, 5), viewport={"width": 375, "height": 812}, mobile=True)
    page.click("#cal-grid [data-showall]")
    s.check(page.evaluate("document.activeElement.matches('#cal-filters .fbtn[data-cat=all].on')"), "focus: Show all lands on the pressed All chip")
    s.no_errors(errors, "round 7 (phone today)")
    ctx.close()

def test_now_next_status(s, browser, base):
    # Now & next rows say where each plant stands; late May leaves room for what's next; the phone note owns up
    # to starred plants; frost nights refresh the descriptions; the phone chart keeps today across views
    may = central(2027, 5, 20)
    ctx, page, errors, _ = open_page(browser, base, when=may, viewport={"width": 375, "height": 812}, mobile=True)
    q = page.evaluate
    rows = q("[...document.querySelectorAll('#cal-grid .cal-row.stat')].map(r => [r.querySelector('.cal-name').dataset.open, r.querySelector('.cal-due').textContent])")
    through = [r for r in rows if "through" in r[1]]
    nxt = [r for r in rows if " from " in r[1]]
    s.check(len(rows) == 12 and len(through) == 8 and len(nxt) == 4 and ["tomato", ", next window: Plant from May 25"] in rows,
            f"now & next: eight open windows with deadlines, then four coming up, each with its step ({rows})")
    s1 = q("document.querySelector('.cal-scroll').scrollLeft")
    page.click('#cal-head [data-view="list"]')
    page.click('#cal-head [data-view="chart"]')
    s2 = q("document.querySelector('.cal-scroll').scrollLeft")
    s.check(s1 > 50 and abs(s2 - s1) <= 4, f"phone chart: a List round-trip brings the chart back to today ({s1} -> {s2})")
    s.no_errors(errors, "now & next (May)")
    ctx.close()
    # October phone, a starred tomato: the note says plus starred; My plants after the list opens at today
    ctx, page, errors, _ = open_page(browser, base, when=central(2026, 10, 5), viewport={"width": 375, "height": 812}, mobile=True,
                                     storage={"wg:favs": json.dumps(["tomato", "peony"])})
    q = page.evaluate
    head = q("document.querySelector('#cal-head .cal-count').textContent")
    s.check(head == "3 of 79 · open now, plus starred", f"now & next: the phone note owns up to starred plants ({head})")
    page.click('#cal-filters .fbtn[data-cat="fav"]')
    view = q("() => [calView, Math.round(document.querySelector('.cal-scroll').scrollLeft)]")
    s.check(view[0] == "chart" and view[1] > 50, f"phone chart: My plants opens at today, not on an empty March ({view})")
    s.no_errors(errors, "now & next (October)")
    ctx.close()
    # a spring frost night: descriptions and status lines follow the forecast once it lands
    ctx, page, errors, _ = open_page(browser, base, when=central(2027, 5, 28), low=34)
    q = page.evaluate
    page.wait_for_function("() => currentAlert")
    desc = q("document.getElementById('sum-tomato').textContent")
    s.check(desc.startswith("Open now, but wait: frost tonight."), f"frost night: the calendar's descriptions say wait, like the cards ({desc[:60]})")
    ctx.close()
    ctx, page, errors, _ = open_page(browser, base, when=central(2027, 5, 28), low=34, viewport={"width": 375, "height": 812}, mobile=True)
    q = page.evaluate
    page.wait_for_function("() => currentAlert")
    waits = q("[...document.querySelectorAll('#cal-grid .cal-row.stat .cal-due, #cal-grid .cal-trow .cal-due')].map(e => e.textContent).filter(t => t.includes('wait: frost tonight')).length")
    s.check(waits > 0, f"frost night: Now & next rows say wait too ({waits})")
    s.no_errors(errors, "now & next (frost night)")
    ctx.close()

def test_round8_fixes(s, browser, base):
    # This Week's spring headline speaks from the tender crops' windows and says "clear" only with a forecast; chart
    # rows run unbroken, axis tags never clip, the count leads the phone card; Ask fields autofill and ring teal;
    # small touch controls get 44px hit areas; frost lines reach 3:1
    ctx, page, errors, _ = open_page(browser, base, when=central(2027, 5, 20), low=50)
    q = page.evaluate
    body = q("document.querySelector('.wx-status p').textContent.replace(/\\u00a0/g, ' ')")
    s.check("Tomatoes, beans, squash, and zinnias go in May 25; basil and cucumbers Jun 1; peppers Jun 5." in body and "harden off" in body
            and "Transplant tomatoes" not in body, f"this week: before the tender windows open, the lede says when each goes in ({body})")
    bad = q("""() => { const bad = [];
      for (let d = new Date(2027, 4, 15, 12); d <= new Date(2027, 5, 30, 12); d.setDate(d.getDate() + 1)) {
        const day = new Date(d), a = adviceFor(day, 50), head = a.head.replace(/\\u00a0/g, ' ');
        const openPart = head.startsWith('Past the frost date') ? '' : a.body.split(' Next: ')[0];
        const anyOpen = TENDER_LEAD.some(([id]) => openBarsFor(PLANT_BY_ID[id], day).length > 0);
        for (const [id, word] of TENDER_LEAD) {
          const open = openBarsFor(PLANT_BY_ID[id], day).length > 0;
          if (open !== new RegExp('\\\\b' + word + '\\\\b', 'i').test(openPart)) bad.push(day.toDateString() + ': ' + word + (open ? ' open, not named' : ' named, not open'));
        }
        if (head.startsWith('Clear to plant') !== anyOpen) bad.push(day.toDateString() + ': ' + head);
      }
      return bad; }""")
    s.check(not bad, f"this week: from May 15 to Jun 30 the headline names exactly the tender crops whose windows are open ({bad[:4]})")
    jun = q("() => adviceFor(new Date(2027, 5, 3, 12), 50)")
    jun["body"] = jun["body"].replace("\u00a0", " ")
    s.check(jun["head"] == "Clear to plant frost-tender crops" and "Tomatoes and zinnias can go in through Jun 10; basil through Jun 15; cucumbers and squash through Jun 20; beans through Jul 1. Next: peppers Jun 5." in jun["body"],
            f"this week: in June the lede says what can go in, until when, and what's next ({jun['body']})")
    s.no_errors(errors, "round 8 (May)")
    ctx.close()
    # no forecast (NWS down, nothing cached): no "clear", and the error line doesn't vouch for a date-only headline
    ctx, page, errors, _ = open_page(browser, base, when=central(2027, 5, 28), nws="fail")
    q = page.evaluate
    head = q("document.querySelector('.wx-status h3').textContent.replace(/\\u00a0/g, ' ')")
    err = q("document.querySelector('.wx-error').textContent")
    s.check(head == "Time for tender crops — check tonight's low first" and "goes by the date alone, so check tonight’s low before setting out tender plants" in err,
            f"this week: without a forecast the front says to check tonight's low ({head} / {err[:120]})")
    s.no_errors(errors, "round 8 (no forecast)", allow=("Failed to load resource",))
    ctx.close()
    # phone, All: bands run each row's full height; no axis tag clipped under the names; the count leads the card
    ctx, page, errors, _ = open_page(browser, base, viewport={"width": 375, "height": 812}, mobile=True)
    q = page.evaluate
    page.click('#cal-filters .fbtn[data-cat="all"]')
    rows = q("""() => [...document.querySelectorAll('#cal-grid .cal-row')].slice(0, 30).map(r => {
      const b = r.getBoundingClientRect(), m = r.querySelector('.cal-track .mband').getBoundingClientRect();
      return [r.querySelector('.cal-name').dataset.open, Math.round(b.height), Math.round(m.height)]; })""")
    gaps = [r for r in rows if r[1] - r[2] > 2]
    s.check(len(rows) == 30 and not gaps, f"phone chart: month bands and frost lines run each row's full height, no dashes ({gaps[:4]})")
    tags = q("""() => { const lab = document.querySelector('#cal-grid .cal-months .rowlab').getBoundingClientRect(), w = document.querySelector('.cal-scroll').getBoundingClientRect();
      const all = [...document.querySelectorAll('#cal-grid .axis-marks .am')];
      const shown = all.filter(el => getComputedStyle(el).visibility !== 'hidden').map(el => el.getBoundingClientRect());
      return [shown.filter(r => r.width && (r.left < lab.right - 1 || r.right > w.right + 1)).length, all.length - shown.length]; }""")
    s.check(tags[0] == 0 and tags[1] > 0, f"phone chart: a frost tag under the pinned names steps aside instead of reading 'ay 15 frost' ({tags})")
    head = q("""() => { const h = document.getElementById('cal-head'), l = document.querySelector('.legend');
      return [h.hidden, getComputedStyle(h).display, h.getBoundingClientRect().bottom <= l.getBoundingClientRect().top, l.getAttribute('role'), l.getAttribute('aria-label')]; }""")
    s.check(head == [False, "flex", True, "group", "Color key"], f"phone: the count and Chart/List lead the card, above a named color key ({head})")
    s.check(q("getComputedStyle(document.querySelector('#cal-grid .frost-line')).opacity") == "0.8", "chart: frost lines drawn at 3:1")
    cue = q("""() => { const c = document.querySelector('#cal-grid .cal-back:not([hidden])'); if (!c) return null; window.scrollTo(0, c.getBoundingClientRect().top + scrollY - 300); const r = c.getBoundingClientRect();
      const hit = document.elementFromPoint(r.left + r.width / 2, r.bottom + 8); return [Math.round(r.height), !!(hit && hit.closest('.cal-back'))]; }""")
    s.check(cue and cue[1], f"phone chart: the Mar–Jul cue answers taps a finger's width around it ({cue})")
    page.click("#tab-weather")
    row = q("""() => { const t = document.querySelector('#wx-windows .task [data-open]').closest('.task'); window.scrollTo(0, t.getBoundingClientRect().top + scrollY - 300); const r = t.getBoundingClientRect();
      const hit = document.elementFromPoint(r.right - 12, r.top + r.height / 2); return [Math.round(r.height), hit && hit.closest('[data-open]') ? hit.closest('[data-open]').dataset.open : null]; }""")
    s.check(row[0] >= 44 and row[1], f"this week: a window row opens its guide from anywhere on the row ({row})")
    s.no_errors(errors, "round 8 (phone)")
    ctx.close()
    # 480px and up (large phones, 720px article embeds): a wider name column
    for width, want in ((375, 112), (600, 150)):
        ctx, page, errors, _ = open_page(browser, base, when=central(2027, 5, 20), viewport={"width": width, "height": 900}, mobile=True)
        got = page.evaluate("Math.round(document.querySelector('#cal-grid .cal-row .cal-name').getBoundingClientRect().width)")
        s.check(got == want, f"phone chart: the name column is {want}px at {width}px ({got})")
        ctx.close()
    # Ask: autocomplete, the optional field marked like the email, and the teal ring on fields
    ctx, page, errors, _ = open_page(browser, base, path="/index.html#ask")
    q = page.evaluate
    a = q("() => ['ask-name', 'ask-where', 'ask-email'].map(id => document.getElementById(id).autocomplete).concat(document.querySelector('label[for=ask-where]').textContent)")
    s.check(a == ["name", "address-level2", "email", "Neighborhood or town (optional)"], f"ask: fields autofill, and the optional one says so ({a})")
    page.focus("#ask-name")
    ring = q("""() => { const c = getComputedStyle(document.getElementById('ask-name')), t = document.createElement('i'); t.style.color = 'var(--accent)'; document.body.append(t);
      const accent = getComputedStyle(t).color; t.remove(); return [c.outlineStyle, c.outlineWidth, c.outlineColor === accent]; }""")
    s.check(ring == ["solid", "2px", True], f"ask: fields take the teal focus ring ({ring})")
    s.no_errors(errors, "round 8 (ask)")
    ctx.close()

def test_returning_reader(s, browser, base):
    # a returning reader's first screen: the note sits under the title in the deck's place (beside it on wide screens),
    # deadline first, whole, and frost-aware; the season line and the zone facts share a line; the phone count is short
    may = central(2027, 5, 20)
    rr = {"wg:visit": json.dumps("2027-04-01T12:00:00"), "wg:favs": json.dumps(["broccoli", "pepper", "basil"])}
    ctx, page, errors, _ = open_page(browser, base, when=may, viewport={"width": 1280, "height": 800}, storage=rr)
    q = page.evaluate
    d = q("""() => { const R = s => document.querySelector(s).getBoundingClientRect(), w = document.querySelector('#welcome .wtext');
      return { inMast: !!document.querySelector('.masthead .mast-title #welcome .welcome') && !document.querySelector('main .welcome'),
               deck: getComputedStyle(document.querySelector('.deck')).display, text: w.textContent.replace(/\\s+/g, ' ').trim(),
               beside: R('#welcome').left > R('.mast-title h1').right && R('#welcome').top < R('.mast-title h1').bottom,
               clamped: w.scrollHeight > w.clientHeight + 1, sameLine: Math.abs(R('.zone-about summary').top - R('#season-pulse').top) < 16,
               chips: getComputedStyle(document.querySelector('.zone-chip')).display, row: Math.round(R('#cal-grid .cal-row').top),
               toolbar: Math.round(R('#cal-filters').top), vh: innerHeight }; }""")
    s.check(d["inMast"] and d["deck"] == "none" and d["beside"] and not d["clamped"]
            and d["text"].startswith("Welcome back. ★ Broccoli — last day to plant · ") and d["text"].endswith("See what’s open →"),
            f"returning reader: the note sits beside the title in the deck's place, deadline first and whole ({d['text']})")
    s.check(d["sameLine"] and d["chips"] == "none", f"masthead: the season line and the zone facts share one line ({d})")
    s.check(d["row"] - d["toolbar"] <= 210 and d["row"] + 44 <= d["vh"], f"returning reader: plant rows on the first desktop screen ({d['row']})")
    page.click("#welcome .dismiss")
    s.check(q("document.getElementById('welcome').innerHTML === '' && getComputedStyle(document.querySelector('.deck')).display !== 'none'"),
            "returning reader: dismissing the note brings the deck back")
    s.no_errors(errors, "returning reader (desktop)")
    ctx.close()
    # a new reader on a wide screen: the deck sits beside the title
    ctx, page, errors, _ = open_page(browser, base, when=may, viewport={"width": 1280, "height": 800})
    side = page.evaluate("() => { const h = document.querySelector('.mast-title h1').getBoundingClientRect(), dk = document.querySelector('.deck').getBoundingClientRect(); return dk.left > h.right && dk.top < h.bottom; }")
    s.check(side, "masthead: on wide screens the deck sits beside the title")
    ctx.close()
    # phone: the whole deadline, the news left to This Week, the short count, no box between masthead and calendar
    ctx, page, errors, _ = open_page(browser, base, when=may, viewport={"width": 375, "height": 812}, mobile=True, storage=rr)
    ph = page.evaluate("""() => { const w = document.querySelector('#welcome .wtext');
      return { clamped: w.scrollHeight > w.clientHeight + 1, news: getComputedStyle(w.querySelector('.w-news')).display,
               visible: w.innerText.replace(/\\s+/g, ' ').trim(), count: document.getElementById('cal-count').textContent, inMain: !!document.querySelector('main .welcome') }; }""")
    s.check(not ph["clamped"] and ph["news"] == "none" and ph["visible"] == "Welcome back. ★ Broccoli — last day to plant. See what’s open →"
            and ph["count"] == "15 of 79 · open now or soon, plus starred" and not ph["inMain"],
            f"returning reader (phone): the whole deadline shows and the count is short ({ph})")
    s.no_errors(errors, "returning reader (phone)")
    ctx.close()
    # October: the season line names what's open, so the note gives the count
    ctx, page, errors, _ = open_page(browser, base, when=central(2026, 10, 5),
                                     storage={"wg:visit": json.dumps("2026-09-05T12:00:00"), "wg:favs": json.dumps(["tomato", "peony"])})
    oc = page.evaluate("() => [document.querySelector('#welcome .wtext').innerText.replace(/\\s+/g, ' ').trim(), document.getElementById('season-pulse').textContent]")
    s.check(oc[0] == "Welcome back. 2 planting windows have opened since your last visit. See what’s open →" and "garlic and peony" in oc[1],
            f"returning reader: in October the note doesn't repeat the season line's names ({oc})")
    ctx.close()
    # a spring frost night: a starred window closing this week says wait, as the calendar does
    ctx, page, errors, _ = open_page(browser, base, when=central(2027, 6, 5), low=34,
                                     storage={"wg:visit": json.dumps("2027-05-01T12:00:00"), "wg:favs": json.dumps(["tomato"])})
    page.wait_for_function("() => currentAlert")
    fr = page.evaluate("document.querySelector('#welcome .wtext').textContent.replace(/\\s+/g, ' ')")
    s.check("★ Tomato — wait: frost tonight" in fr, f"returning reader: on a spring frost night the note says wait ({fr[:80]})")
    s.no_errors(errors, "returning reader (frost)")
    ctx.close()

def test_newsletter_link(s, browser, base):
    # the newsletter box describes WPR's real newsletters and its button opens the signup page in a new tab,
    # standalone and inside an article embed (the tool's own analytics event fires too)
    signup = "https://wausaupilotandreview.com/sign-up/"
    ctx, page, errors, _ = open_page(browser, base, path="/index.html#weather")
    ctx.route("https://wausaupilotandreview.com/**", lambda r: r.fulfill(status=200, content_type="text/html", body="<title>Subscribe for free</title>"))
    q = page.evaluate
    box = q("""() => { const a = document.getElementById('cta-news-link');
      return { text: document.querySelector('#cta-news .txt').textContent, href: a.href, target: a.target, rel: a.rel, config: CONFIG.newsletterUrl }; }""")
    s.check(box["href"] == signup and box["config"] == signup and box["target"] == "_blank" and "noopener" in box["rel"],
            f"newsletter: the button points at WPR's signup page and opens a new tab ({box})")
    s.check("every morning, afternoon or both" in box["text"] and "Frost warnings" not in box["text"] and "This week in the garden" not in box["text"],
            f"newsletter: the box describes the real newsletters, not a garden email ({box['text']})")
    q("() => { window.dataLayer = []; }")
    with ctx.expect_page() as pop:
        page.click("#cta-news-link")
    tab = pop.value
    tab.wait_for_load_state()
    s.check(tab.url == signup and q("window.dataLayer.some(e => e.event === 'newsletter_click')"),
            f"newsletter: clicking opens the signup page and counts the click ({tab.url})")
    s.no_errors(errors, "newsletter (standalone)")
    ctx.close()
    ctx = browser.new_context(viewport={"width": 1100, "height": 900})
    ctx.route("https://wausaupilotandreview.com/**", lambda r: r.fulfill(status=200, content_type="text/html", body="<title>Subscribe for free</title>"))
    ctx.route("https://api.weather.gov/**", lambda r: r.abort())
    ctx.route("https://fonts.googleapis.com/**", lambda r: r.fulfill(status=200, content_type="text/css", body=""))
    page = ctx.new_page()
    page.set_content(f'<iframe src="{base}/index.html#weather" style="width:900px;height:1600px;border:0" allow="clipboard-write; web-share"></iframe>')
    frame = page.frame_locator("iframe")
    frame.locator("#cta-news-link").wait_for()
    with ctx.expect_page() as pop:
        frame.locator("#cta-news-link").click()
    tab = pop.value
    tab.wait_for_load_state()
    s.check(tab.url == signup, f"newsletter: inside an article embed the button opens the signup page in a new tab ({tab.url})")
    ctx.close()

def test_steady_load(s, browser, base):
    # v1.37: main and the footer stay unpainted until the boot has built the tool; guide cards are drawn only near
    # the screen; the chart's scroll handler measures before it writes and runs once a frame
    ctx, page, errors, _ = open_page(browser, base, viewport={"width": 375, "height": 812}, mobile=True)
    q = page.evaluate
    st = q("""() => { const vis = s => getComputedStyle(document.querySelector(s)).visibility;
      const after = [document.documentElement.classList.contains('booting'), vis('main'), vis('#page-foot')];
      document.documentElement.classList.add('booting');
      const during = [vis('main'), vis('#page-foot'), vis('.masthead'), vis('#modal-back')];
      document.documentElement.classList.remove('booting');
      return { after, during }; }""")
    s.check(st["after"] == [False, "visible", "visible"] and st["during"] == ["hidden", "hidden", "visible", "visible"],
            f"load: main and the footer wait for the boot; the masthead and dialog never do ({st})")
    head = q("document.head.innerHTML.includes(\"classList.add('booting')\") && document.head.innerHTML.includes(\"addEventListener('load'\")")
    s.check(head, "load: the head marks the page as booting, with the load event as a backstop")
    q("() => showPanel('guides', false)")
    cards = q("""() => { const c = [...document.querySelectorAll('#guide-cards .pcard')], g = document.getElementById('guide-cards').getBoundingClientRect();
      return { cv: getComputedStyle(c[0]).contentVisibility, fit: c.every(x => { const r = x.getBoundingClientRect(); return r.left >= g.left - 1 && r.right <= g.right + 1; }),
               twoUp: Math.round(c[0].getBoundingClientRect().top) === Math.round(c[1].getBoundingClientRect().top) }; }""")
    s.check(cards["cv"] == "auto" and cards["fit"] and cards["twoUp"], f"guides: cards are drawn only near the screen, still two across on a phone ({cards})")
    last = q("() => { const c = [...document.querySelectorAll('#guide-cards .pcard')].at(-1); c.scrollIntoView(); return Math.round(c.querySelector('.art').getBoundingClientRect().height); }")
    s.check(last > 50, f"guides: a card scrolled into view draws its art ({last}px)")
    q("() => showPanel('calendar', false)")
    page.click('#cal-filters .fbtn[data-cat="all"]')
    calls = q("""async () => { let n = 0; const orig = window.markHiddenMonths; window.markHiddenMonths = () => { n++; orig(); };
      const w = document.querySelector('.cal-scroll');
      for (let i = 1; i <= 12; i++) { w.scrollLeft = i * 15; w.dispatchEvent(new Event('scroll')); }
      await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
      window.markHiddenMonths = orig;
      return { n, cue: document.querySelector('#cal-grid .cal-back').hidden ? '' : document.querySelector('#cal-grid .cal-back .t').textContent }; }""")
    s.check(1 <= calls["n"] <= 2 and calls["cue"] != "", f"chart: a burst of scroll events updates the cue once a frame ({calls})")
    s.no_errors(errors, "steady load")
    ctx.close()

def test_keyboard_sr(s, browser, base):
    # v1.38: focus is never hidden or clipped, decorative arrows stay silent, and what changes out of sight is
    # announced (frost alerts, forecast retries, copies); dismissing the welcome note keeps focus
    ARROWS = """() => { const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT), bad = []; let n;
      while ((n = w.nextNode())) { const c = n.parentElement.closest('button, a'); if (n.nodeValue.includes('→') && c && !n.parentElement.closest('[aria-hidden="true"]')) bad.push(c.textContent.trim().slice(0, 40)); }
      return bad; }"""
    ctx, page, errors, _ = open_page(browser, base, viewport={"width": 1280, "height": 800})
    q = page.evaluate
    q("() => openModal('tomato', false)")
    page.wait_for_function("() => isModalOpen()")
    q("() => { const m = document.getElementById('modal-back'); m.scrollTop = m.scrollHeight; m.dispatchEvent(new Event('scroll')); }")
    page.wait_for_timeout(150)
    q("() => { const f = [...document.querySelectorAll('#modal button, #modal a, #modal summary, #modal textarea')].filter(e => e.getClientRects().length); f.at(-1).focus(); }")
    under = []
    for _ in range(14):
        page.keyboard.press("Shift+Tab")
        r = q("""() => { const a = document.activeElement, m = document.getElementById('modal'); if (!m.contains(a) || a.closest('.modal-bar') || !m.classList.contains('stuck')) return null;
          const bar = parseFloat(getComputedStyle(m).getPropertyValue('--bar-h')), r = a.getBoundingClientRect(); return r.top < bar - 1 ? [a.textContent.trim().slice(0, 30), Math.round(r.top)] : null; }""")
        if r:
            under.append(r)
    s.check(not under, f"keyboard: walking a long guide, focus never lands under its sticky close strip ({under})")
    s.check(not q(ARROWS), f"screen readers: arrows inside a guide are decoration ({q(ARROWS)})")
    page.keyboard.press("Escape")
    page.wait_for_function("() => !isModalOpen()")
    page.focus("#tab-calendar")
    page.keyboard.press("ArrowRight")
    tab = q("""() => { const t = document.activeElement, st = getComputedStyle(t), b = document.querySelector('.tabs').getBoundingClientRect(), r = t.getBoundingClientRect(), o = parseFloat(st.outlineOffset) + parseFloat(st.outlineWidth);
      return r.top - o >= b.top - 0.5 && r.bottom + o <= b.bottom + 0.5 && st.outlineStyle === 'solid'; }""")
    s.check(tab, "keyboard: a tab's focus ring sits inside the tab bar instead of being clipped by it")
    page.keyboard.press("ArrowLeft")
    q("() => showPanel('guides', false)")
    page.keyboard.press("Shift")
    q("() => document.querySelector('.pcard .cardbtn').focus()")
    s.check(q("getComputedStyle(document.querySelector('.pcard')).outlineStyle") == "solid", "keyboard: a focused card is ringed as a whole")
    q("() => showPanel('calendar', false)")
    page.focus("#print-btn")
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")
    s.check(q("document.activeElement.id === 'page-foot' && getComputedStyle(document.activeElement).outlineStyle === 'solid'"),
            "keyboard: Skip past the calendar lands on the footer with a visible ring")
    page.keyboard.press("Shift")
    rings = q("""() => ['.brand', '.zone-about summary'].map(s => { const e = document.querySelector(s); e.focus(); return getComputedStyle(e).outlineStyle; })""")
    s.check(rings == ["solid", "solid"], f"keyboard: the brand link and the zone toggle wear the teal ring, not the browser's ({rings})")
    s.check(q("document.querySelector('.cal-group.pin .cal-group-h .sr-only') !== null"), "screen readers: the pinned group says its plants also appear below")
    dirs = q("""() => { const d = document.createElement('div'); d.innerHTML = lockupHTML({ name: 'Test Nursery', url: 'https://example.org', address: '1 Main St, Wausau', tagline: 'Plants — Wausau' }, 'Presented by', 'ask');
      const a = d.querySelector('a.dir'); return !!a && !!a.getAttribute('href') && !a.closest('a.lockup-main') && !d.querySelector('[role=button]'); }""")
    s.check(dirs, "keyboard: a sponsor's Directions is a link of its own, not a button inside a link")
    s.no_errors(errors, "keyboard + screen readers (desktop)")
    ctx.close()
    # a spring frost night for a returning reader: the alert is announced once; the note's and windows' arrows stay silent
    ctx, page, errors, _ = open_page(browser, base, when=central(2027, 5, 28), low=34,
                                     storage={"wg:visit": json.dumps("2027-05-01T12:00:00"), "wg:favs": json.dumps(["broccoli"])})
    q = page.evaluate
    page.wait_for_function("() => document.getElementById('sr-live').textContent.length > 0")
    said = q("document.getElementById('sr-live').textContent")
    s.check(said.startswith("Frost possible tonight (34°F): hold off on tender crops"), f"screen readers: a frost alert that arrives with the forecast is announced ({said})")
    s.check(not q(ARROWS), f"screen readers: arrows in the masthead, note and windows are decoration ({q(ARROWS)})")
    page.keyboard.press("Shift")
    q("() => document.querySelector('#welcome .dismiss').focus()")
    page.keyboard.press("Enter")
    s.check(q("document.activeElement.classList.contains('deck')"), "keyboard: dismissing the welcome note moves focus to the deck that takes its place")
    s.no_errors(errors, "keyboard + screen readers (frost night)")
    ctx.close()
    # "Try again": focus and an announcement either way
    ctx, page, errors, calls = open_page(browser, base, nws="fail")
    ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=base)
    q = page.evaluate
    page.click("#tab-weather")
    page.focus("#wx-retry")
    page.keyboard.press("Enter")
    page.wait_for_function("() => document.getElementById('sr-live').textContent.startsWith('Live forecast unavailable')", timeout=15000)
    s.check(q("document.activeElement.id") == "wx-retry", "keyboard: a failed retry puts focus on the new Try again button")
    calls["mode"] = "ok"
    page.keyboard.press("Enter")
    page.wait_for_function("() => document.getElementById('sr-live').textContent === 'Forecast updated.'", timeout=15000)
    s.check(q("document.activeElement.classList.contains('wx-h')"), "keyboard: a successful retry puts focus on the forecast's heading")
    page.click("#tab-ask")
    page.click("#ask-copy-addr")
    page.wait_for_function("() => /copied|blocked|available/i.test(document.getElementById('sr-live').textContent)")
    s.check(True, "screen readers: copying the address is announced")
    s.no_errors(errors, "keyboard + screen readers (retry)", allow=("Failed to load resource",))
    ctx.close()
    # phone: the scrolling color key is a focus stop with the teal ring
    ctx, page, errors, _ = open_page(browser, base, when=central(2027, 5, 20), viewport={"width": 375, "height": 812}, mobile=True)
    page.focus('#cal-head [data-view="list"]')
    page.keyboard.press("Tab")
    key = page.evaluate("() => { const a = document.activeElement; return a.classList.contains('legend') ? [a.getAttribute('aria-label'), getComputedStyle(a).outlineStyle] : null; }")
    # browsers that make scroll areas keyboard-focusable stop on the key; when they do, it's named and ringed
    s.check(key in (None, ["Color key", "solid"]), f"keyboard: the phone's scrolling color key, when it takes focus, is named and wears the teal ring ({key})")
    ctx.close()

def test_planting_verbs(s, browser, base):
    # plants name their own planting step where "Transplant / plant out" isn't what you do
    ctx, page, errors, _ = open_page(browser, base)
    q = page.evaluate
    win = q("document.getElementById('wx-windows').textContent").replace("\n", " ")
    s.check("plant bare-root divisions through Oct 15" in " ".join(win.split()) and "plant cloves opens Oct 1" in " ".join(win.split()),
            "verbs: This Week says peonies go in as bare-root divisions and garlic as cloves")
    page.click('#cal-filters .fbtn[data-view="list"]')
    rows = q("Object.fromEntries([...document.querySelectorAll('.cal-trow')].map(r => [r.querySelector('.nm').dataset.open, r.textContent.replace(/\\s+/g, ' ')]))")
    s.check("Plant cloves: Oct 1 – Oct 25" in rows["garlic"] and "Plant seed potatoes: May 1 – May 25" in rows["potato"]
            and "Plant sets:" in rows["onion"] and "Transplant / plant out: May 25 – Jun 10" in rows["tomato"],
            "verbs: the List view names each plant's step, and transplants still say transplant")
    s.check("Plant or divide rhizomes" in rows["iris"] and "Plant or divide" in rows["hosta"] and "Transplant / plant out" in rows["columbine"],
            "verbs: perennials that are divided say so; columbine, which isn't, doesn't")
    q("() => openModal('garlic', false)")
    s.check("Plant cloves" in q("document.querySelector('#modal .mc-key').textContent"), "verbs: the garlic guide's timeline says Plant cloves")
    s.check("Plant cloves Oct 1 to Oct 25" in q("rowSummary(PLANT_BY_ID.garlic)"), "verbs: the screen-reader row summary uses the plant's step")
    s.no_errors(errors, "planting verbs")
    ctx.close()

def test_guide_ending(s, browser, base):
    # Save and Share sit under the timeline; notes fold into one line until there's something in them
    ctx, page, errors, _ = open_page(browser, base)
    q = page.evaluate
    q("() => openModal('tomato', false)")
    s.check(q("document.querySelector('#modal .mini-cal').nextElementSibling.matches('.m-acts')")
            and q("document.querySelector('#modal .m-acts #modal-star') !== null && document.querySelector('#modal .m-acts #modal-share') !== null"),
            "guide: Save and Share sit right under the timeline")
    s.check(not q("document.querySelector('#modal .notes').open") and q("document.querySelector('#modal .notes summary').textContent") == "Add a note about Tomato",
            "guide: with no note yet, notes are one 'Add a note' line")
    page.click("#modal .notes summary")
    try:
        page.wait_for_function("() => document.activeElement.id === 'plant-note'", timeout=3000)
    except Exception:
        pass
    s.check(q("document.querySelector('#modal .notes').open") and q("document.activeElement.id") == "plant-note", "guide: opening the notes puts the cursor in them")
    page.fill("#plant-note", "Early Girl, south bed")
    q("() => { closeModalUI(); openModal('tomato', false); }")
    s.check(q("document.querySelector('#modal .notes').open") and q("document.querySelector('#modal .notes summary').textContent") == "My notes on Tomato",
            "guide: a guide with a note opens with it showing")
    s.check(q("getComputedStyle(document.querySelector('#modal .tip-close')).display") == "none", "guide: the mid-guide close is for embeds only")
    s.no_errors(errors, "guide ending")
    ctx.close()
    # on a phone, once the picture scrolls away the close button sits in a solid strip, not over the text
    ctx, page, errors, _ = open_page(browser, base, viewport={"width": 375, "height": 812}, mobile=True)
    q = page.evaluate
    q("() => openModal('tomato', false)")
    s.check(not q("document.getElementById('modal').classList.contains('stuck')"), "guide: no strip while the picture is in view")
    q("() => { const t = document.querySelector('#modal .tipbox'); modalBack.scrollTop += t.getBoundingClientRect().top - 30; }")
    page.wait_for_function("() => document.getElementById('modal').classList.contains('stuck')")
    hit = q("""() => { const x = document.getElementById('modal-close').getBoundingClientRect();
      const el = document.elementFromPoint(x.left - 14, x.top + x.height / 2);
      return { strip: !!(el && el.closest('.modal-bar')), title: getComputedStyle(document.querySelector('#modal .bar-title')).display }; }""")
    s.check(hit["strip"] and hit["title"] != "none", f"guide: on a phone, text scrolls under a solid strip with the plant's name, not under the button ({hit})")
    s.no_errors(errors, "guide strip")
    ctx.close()

def test_readability(s, browser, base):
    # fields readers type in have a visible edge; the chart's light bars are deeper; card tags have room
    ctx, page, errors, _ = open_page(browser, base)
    q = page.evaluate
    edges = q("[getComputedStyle(document.getElementById('guide-search')).borderTopColor, getComputedStyle(document.getElementById('ask-q')).borderTopColor]")
    s.check(edges == ["rgb(138, 138, 138)", "rgb(138, 138, 138)"], f"fields: the search box and Ask fields have a 3:1 edge ({edges})")
    toks = q("['--c-plant', '--c-sow'].map(v => getComputedStyle(document.documentElement).getPropertyValue(v).trim())")
    s.check(toks == ["#c86a8d", "#c98900"], f"chart: the light-mode pink and amber bars are deeper ({toks})")
    cards = q("Object.fromEntries(['garlic', 'peony', 'tomato'].map(id => [id, document.querySelector(`.pcard[data-open=${id}] .when`).textContent.replace(/\\u00a0/g, ' ')]))")
    s.check(cards == {"garlic": "Plant cloves · Oct 1–25", "peony": "Plant bare-root divisions · through Oct 15", "tomato": "Start indoors · Apr 10–25 next year"},
            f"guides: each card leads with its next planting step in Wausau ({cards})")
    s.check(q("getComputedStyle(document.querySelector('.pcard[data-open=garlic] .when')).color") == "rgb(176, 52, 106)"
            and q("document.querySelector('.pcard[data-open=tomato] .meta').textContent.trim()") == "Moderate · Full sun",
            "guides: the step wears its bar's text color; difficulty and sun follow as one quiet line")
    order = q("[...document.querySelectorAll('#cal-filters .chips .fbtn')].map(b => b.dataset.cat).join()")
    s.check(order == "short,all,now,veg,flower,herb,fav", f"calendar: Open now sits right after All ({order})")
    zone = q("() => { const z = document.querySelector('.zone-strip'), c = document.querySelector('.zone-chip'); return [Math.round(z.getBoundingClientRect().height), getComputedStyle(c).backgroundColor]; }")
    s.check(zone[0] <= 40 and zone[1] == "rgba(0, 0, 0, 0)", f"masthead: the zone facts are one quiet line on desktop too ({zone})")
    ctx.close()
    # on a phone: 14px names, the whole cell as the tap target, and a cue for months off to the left
    ctx, page, errors, _ = open_page(browser, base, viewport={"width": 375, "height": 812}, mobile=True)
    q = page.evaluate
    q("() => showPanel('guides', false)")
    two = q("() => { const c = [...document.querySelectorAll('#guide-cards .pcard')].slice(0, 2).map(e => e.getBoundingClientRect()); return [Math.round(c[0].top), Math.round(c[1].top), Math.round(c[0].width)]; }")
    s.check(two[0] == two[1] and two[2] < 180, f"guides: phones show two cards across ({two})")
    wd = q("() => { const w = document.querySelector('.pcard[data-open=tomato] .when'); return [getComputedStyle(w.querySelector('.sep')).display, getComputedStyle(w.querySelector('.wd')).display]; }")
    s.check(wd == ["none", "block"], f"guides: on phones a card's dates take their own line ({wd})")
    q("() => showPanel('calendar', false)")
    page.click('#cal-filters .fbtn[data-cat="all"]')
    name = q("() => { const n = document.querySelector('#cal-grid .cal-name'), r = n.getBoundingClientRect(); return [getComputedStyle(n).fontSize, Math.round(r.height)]; }")
    s.check(name[0] == "14px" and name[1] >= 44, f"chart: phone plant names are 14px and the whole 44px cell is the target ({name})")
    page.wait_for_function("() => !document.querySelector('#cal-grid .cal-back').hidden")
    cue = q("document.querySelector('#cal-grid .cal-back').textContent")
    s.check(cue.startswith("Mar–") and q("document.querySelector('.cal-scroll').classList.contains('scrolled')"),
            f"chart: the pinned column names the months off to the left ({cue})")
    page.click("#cal-grid .cal-back")
    page.wait_for_function("() => document.querySelector('.cal-scroll').scrollLeft === 0")
    try:   # the cue updates on the next animation frame after the last scroll event
        page.wait_for_function("() => document.querySelector('#cal-grid .cal-back').hidden", timeout=2000)
        stepped = True
    except Exception:
        stepped = False
    s.check(stepped, "chart: tapping the cue scrolls back to March, and the cue steps aside")
    s.no_errors(errors, "readability")
    ctx.close()

TESTS = [test_boot, test_polish_v17, test_contrast, test_tabs_history_modal, test_calendar, test_guides_favorites_notes,
         test_weather, test_seasons, test_tasks, test_reminders, test_community, test_ask, test_launch_mode,
         test_storage_tamper, test_embedded, test_mobile, test_print, test_sources_page, test_safety, test_small_fixes, test_newspaper, test_desktop_type, test_frost_alert, test_pressed_states, test_new_plants, test_planting_verbs, test_guide_ending, test_readability, test_harvest_labels_and_bulbs, test_open_now_group, test_keyboard_path, test_round6_fixes, test_phone_first_screen, test_round7_fixes, test_now_next_status, test_round8_fixes, test_returning_reader, test_newsletter_link, test_steady_load, test_keyboard_sr]


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
