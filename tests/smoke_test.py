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
    s.check(q("document.querySelectorAll('.cal-row').length") == n, "boot: a calendar row for every plant")
    s.check(q("[...document.querySelectorAll('.cal-group-h')].map(h => h.firstChild.textContent.trim()).join('|')") == "Vegetables|Herbs|Flowers",
            "boot: the calendar is grouped by category")
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
    s.check(page.locator(".cal-row .mband").count() == page.evaluate("PLANTS.length") * 4, "calendar: month bands on every row")
    s.check(page.locator(".cal-months:not(.rep)").count() == 3, "calendar: each category group has its own month axis")
    reps = page.evaluate("""() => [...document.querySelectorAll('.cal-group')].map(g => [g.dataset.cat, g.querySelectorAll('.cal-row').length,
      g.querySelectorAll('.cal-months.rep').length, g.querySelectorAll('.cal-key').length])""")
    want = [[c, n, max(0, (n - 4) // 12), (1 if i else 0) + max(0, (n - 4) // 12)] for i, (c, n, _, _) in enumerate(reps)]
    s.check(reps == want and sum(r[2] for r in reps) > 0, f"calendar: long groups repeat the months and color key every dozen rows ({reps})")
    s.check(page.locator(".cal-name small").count() == 0, "calendar: rows don't repeat their group's category label")
    s.check(page.evaluate("[...document.querySelectorAll('#cal-filters .seg .fbtn')].map(b => b.dataset.view).join()") == "chart,list",
            "calendar: Chart and List form one control")
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
    s.check("fetch" not in err.lower() and "responded" not in err and "api.weather.gov" not in err and "still accurate" in err,
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
    (central(2027, 5, 20), 50, "Clear to plant frost-tender crops", "Day 6 of the frost-free season"),
    (central(2027, 5, 20), 34, "Frost possible tonight — hold off on tender crops", "Day 6 of the frost-free season"),
    (central(2027, 7, 20), 34, "Frost possible tonight — cover tender crops", "frost-free season"),
    (central(2026, 10, 10), 48, "No frost this week — tender crops can stay out", "garlic, bulbs"),
    (central(2026, 10, 10), 34, "Frost possible tonight — cover and pick", "garlic, bulbs"),
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
            and frame.evaluate("getComputedStyle(document.getElementById('season-pulse')).display") != "none"
            and frame.evaluate("document.getElementById('cal-hint').hidden"),
            "embed: no deck under the article's headline; the season line leads, and the star tip stays out of the way")
    s.check(frame.evaluate("getComputedStyle(document.querySelector('.foot-actions [data-act=share]')).display") != "none"
            and frame.evaluate("getComputedStyle(document.querySelector('.foot-actions [data-act=bookmark]')).display") == "none",
            "embed: Share sits in the footer; Bookmark stays hidden")
    frame.click("[data-showall]")
    s.check(frame.evaluate("document.querySelectorAll('#cal-grid .cal-row').length === PLANTS.length")
            and frame.locator('#cal-filters .fbtn[data-cat="all"].on').count() == 1, "embed: Show all brings back every plant")
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
    s.check(q("document.querySelector('.tabs').scrollWidth <= document.querySelector('.tabs').clientWidth + 1"), "mobile: all five tabs fit without sideways scrolling")
    # heights depend on the face that loads (CI renders without Oswald), so check structure plus a loose budget
    mast = q("""[Math.round(document.querySelector('.masthead').getBoundingClientRect().height),
      getComputedStyle(document.querySelector('.zone-chip .zl')).display, getComputedStyle(document.querySelector('.zone-chip')).paddingTop]""")
    s.check(mast[0] <= 580 and mast[1] == "none" and mast[2] == "0px", f"mobile: the zone boxes collapse to one quiet line and the masthead leaves room for the tool ({mast})")
    first = q("""() => { const c = document.querySelector('#cal-filters .chips'), t = document.querySelector('#cal-filters .cal-tools');
      const vis = sel => getComputedStyle(document.querySelector(sel)).display !== 'none';
      return { chipRows: Math.round(c.getBoundingClientRect().height), scrolls: c.scrollWidth > c.clientWidth,
               toolsTop: Math.round(t.getBoundingClientRect().top - c.getBoundingClientRect().bottom), toolsH: Math.round(t.getBoundingClientRect().height),
               mast: vis('.mast-actions'), foot: vis('.foot-actions'), longIntro: vis('#panel-calendar .sub .t-long'), shortIntro: vis('#panel-calendar .sub .t-short') }; }""")
    s.check(first["chipRows"] <= 56 and first["scrolls"] and first["toolsTop"] >= -1,
            f"mobile: the category chips take one row that scrolls sideways, the chart tools below it ({first})")
    s.check(not first["mast"] and first["foot"], f"mobile: Bookmark and Share move from the masthead to the footer ({first})")
    s.check(not first["longIntro"] and first["shortIntro"], "mobile: the calendar's intro is one line")
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
    crop: `${p.cat === 'flower' ? 'Bloom' : 'Harvest'} ${of(p, 'h')}`, src: p.src }]));
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
                   "Gloriosa", "Lantana", "Rhododendron", "Vinca", "Ipomoea", "Abrus", "Atropa", "Hyoscyamus", "Cicuta", "Conium"]


def test_safety(s, browser, base):
    ctx, page, errors, _ = open_page(browser, base)
    q = page.evaluate
    plants = q("PLANTS.map(p => ({ id: p.id, name: p.name, latin: p.latin, cat: p.cat, warn: p.warn || '' }))")
    banned = [p["id"] for p in plants if p["cat"] != "veg" and any(re.search(rf"\b{g}\b", p["latin"]) for g in EXCLUDED_GENERA)]
    s.check(not banned, f"safety: no flower, herb, or native from a genus rated High or Class 1 ({banned})")
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
    s.check(not q("document.getElementById('cal-hint').hidden") and "Reminders" in q("document.getElementById('cal-hint').textContent"),
            "reminders: the calendar explains starring before anyone taps Reminders")
    q("() => toggleFav('lilac')")
    s.check(q("document.getElementById('cal-hint').hidden"), "reminders: the hint steps aside once a plant is starred")
    q("() => toggleFav('lilac')")
    s.check(not q("document.getElementById('cal-hint').hidden"), "reminders: with no stars, the hint returns")
    page.click("#hint-x")
    s.check(q("document.getElementById('cal-hint').hidden") and q("localStorage.getItem('wg:hint-off')") == "true"
            and q("document.activeElement.matches('#cal-filters .fbtn.on')"), "reminders: 'Got it' hides the hint for good and keeps focus in the filters")
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
    page.click("[data-close-modal]")
    s.check(not q("isModalOpen()"), "phone: a close button waits at the end of the guide")
    s.no_errors(errors, "small fixes / phone")
    ctx.close()

def test_newspaper(s, browser, base):
    ctx, page, errors, _ = open_page(browser, base)
    q = page.evaluate
    # calendar: NOAA's frost-risk band behind every row, solid where frost is near-certain
    band = q("""() => { const b = document.querySelectorAll('#cal-grid .cal-row')[0].querySelectorAll('.frost-band');
      const sp = b[0].style, fa = b[1].style;
      return { n: document.querySelectorAll('#cal-grid .cal-row .frost-band').length, rows: PLANTS.length,
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
    s.check(q("document.querySelector('.wx-status h3').textContent.includes('\\u00a0—')"), "weather: the headline's dash stays with the word before it")
    lone = frost_case("""[{ name: 'Tonight', isDaytime: false, temp: 37, cond: 'Mostly Clear', pop: null },
      { name: 'Friday', isDaytime: true, temp: 63, cond: 'Patchy Frost then Sunny', pop: null },
      { name: 'Friday Night', isDaytime: false, temp: 45, cond: 'Clear', pop: null }]""")
    s.check(lone["cards"][:2] == [["Tonight", True, True], ["Friday", False, False]], f"weather: an evening fetch flags the lone Tonight card ({lone['cards']})")
    later = frost_case("""[{ name: 'Today', isDaytime: true, temp: 60, cond: 'Sunny', pop: null },
      { name: 'Tonight', isDaytime: false, temp: 30, cond: 'Clear', pop: null },
      { name: 'Friday', isDaytime: true, temp: 58, cond: 'Sunny', pop: null },
      { name: 'Friday Night', isDaytime: false, temp: 38, cond: 'Patchy Frost', pop: null }]""")
    s.check(later["head"] == "Frost possible tonight — cover and pick" and "patchy frost Friday night, with a low of 38°F" in later["body"]
            and "coldest low in the forecast is 30°F tonight" in later["body"] and "plants tonight" in later["body"]
            and later["cards"] == [["Today", True, True], ["Friday", True, True]],
            f"weather: the headline names the first night to protect; both risky nights are flagged ({later['body'][:120]})")
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

TESTS = [test_boot, test_polish_v17, test_contrast, test_tabs_history_modal, test_calendar, test_guides_favorites_notes,
         test_weather, test_seasons, test_tasks, test_reminders, test_community, test_ask, test_launch_mode,
         test_storage_tamper, test_embedded, test_mobile, test_print, test_sources_page, test_safety, test_small_fixes, test_newspaper]


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
