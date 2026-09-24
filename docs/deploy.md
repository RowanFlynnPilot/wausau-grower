# The Wausau Grower — Deployment Guide
*How to take the tool to production on wausaupilotandreview.com*

---

## What you're deploying

One file: `index.html`. It is fully self-contained — no build step, no dependencies, no database, no server-side code. All CSS and JavaScript are inline; the plant and community illustrations are inline SVG, and the Pilot & Review typewriter seal is embedded as a data URI. External calls from the reader's browser: the National Weather Service API (api.weather.gov — free, keyless, CORS-enabled) and Google Fonts for Oswald/Merriweather (the page falls back to system serif/sans if fonts are unreachable).

Reader personalization (starred "My plants", per-plant notes, likes, last-visit tracking for the "Welcome back" strip) is stored in the reader's own browser via localStorage under `wg:*` keys — nothing is sent anywhere, nothing to host, and it degrades gracefully to session-only behavior in private browsing.

## Configuration

At the top of the second `<script>` block there is a single `CONFIG` object. It ships in fully functional demo mode. Things to set for production:

**`prototype`** — `true` until launch. While it's on, the six demo posts show in the Community Garden, the masthead and footer say "prototype," and the Share and Ask forms can run in demo mode. Set it to `false` for launch: demo posts disappear (they are flagged `demo: true` in `POSTS`, so fake reader posts can never go live by accident), the prototype labels go away, and a form with nowhere to send — no `submitEndpoint`, or neither `askEndpoint` nor `askEmail` — is hidden rather than pretending to work. The page logs a console warning at load if launch mode is missing any of those.

**`sponsor`** — the masthead sponsorship slot. Set to `{ name: 'Sponsor Name', url: 'https://…' }` and a "Presented by" credit appears under the deck with a `rel="sponsored"` link (which keeps Google happy). Leave `null` until the slot is sold; nothing renders.

**`submitEndpoint`** — where "Share your garden" submissions go. Leave `null` and the form posts locally in the reader's page only (demo behavior). Set it to any URL that accepts a JSON POST of `{who, where, kind, cap}` and the form instead submits for moderation and shows the reader a "thanks — pending review" message. Three realistic options, cheapest first:

1. **Formspree (or similar form service)** — five-minute setup, submissions arrive by email, free tier covers likely volume. Right answer for the beta.
2. **Google Apps Script web app** — submissions append to a Google Sheet the newsroom already knows how to use. Free, slightly more setup.
3. **WordPress REST route** — a tiny custom endpoint that creates a draft post in a "Garden submissions" category, so moderation is just the normal WordPress publish flow, and photo upload can be added at the same time. Right answer once the beta proves out.

Note the form doesn't yet upload actual photos (submissions are text + an illustration choice); photo upload arrives with option 3, since it needs real storage and moderation anyway.

**`newsletterUrl`** — where the "Join the newsletter" call-to-action buttons point. It defaults to the site homepage (which carries the signup form); point it at a dedicated signup page when one exists.

**Ask a Gardener** (`askSponsor`, `askEndpoint`, `askEmail`) — the fifth tab. `askSponsor` is the organization that answers questions and sponsors the tab (`{ name, url, logo, tagline, address }`; the tagline splits at the em dash into a bold line and a muted line, and an address adds a Directions chip). Questions go to `askEndpoint` (POST JSON `{name, email, where, question, plant, publish}`) or, with no endpoint, open the reader's mail app addressed to `askEmail` — the Master Gardeners' help-desk address is the obvious value. With neither set the form runs in prototype mode and says so. **Sales preview:** add `?demo` (or `?demo=Organization%20Name`) to the URL and every open sponsor slot fills with a placeholder under a preview ribbon, so a prospect can see their name on the live page; sold slots are never overridden and ordinary readers never see it. See `docs/ask-a-gardener.md` for the offer.

## Publishing options

**Standalone page (recommended for beta).** Serve the file at a clean URL like `/garden`. On WordPress/Newspack hosts, use a page template or ask the host to serve the file directly. Cleanest URL, full-viewport experience, easiest to share and to print. GitHub Pages on this repository also works as a beta host — the file is named `index.html` for exactly that reason.

**Iframe embed.** Drop the tool into a normal WordPress page if serving a raw HTML file is awkward. The page detects that it is embedded and posts its own height to the parent using the standard WPR widget message (`{type: "wpr-embed-height", id: "wausau-grower", height}`), so the iframe can grow and shrink with the content instead of sitting at a fixed height. Paste this once in the host page (a Custom HTML block works):

```html
<iframe id="wausau-grower" src="/garden" title="The Wausau Grower"
        style="width:100%;min-height:900px;border:0" loading="lazy"
        allow="clipboard-write; web-share"></iframe>
<script>
window.addEventListener('message', function (e) {
  var f = document.getElementById('wausau-grower');
  if (!e.data || e.data.id !== 'wausau-grower') return;
  if (e.data.type === 'wpr-embed-height') f.style.height = e.data.height + 'px';
  // a link inside the tool switched tabs: bring the tool's tab bar into view
  if (e.data.type === 'wpr-embed-scroll')
    window.scrollTo({ top: f.getBoundingClientRect().top + window.scrollY + e.data.top - 12, behavior: 'smooth' });
  // optional: forward the tool's events to the article page's Google Analytics
  if (e.data.type === 'wpr-embed-event' && typeof window.gtag === 'function') window.gtag('event', e.data.name, e.data.params);
});
</script>
```

Keep the `allow="clipboard-write; web-share"` attribute: browsers block the Share button and every "copy" action inside a cross-origin iframe without it. The two extra message types are optional (a host that ignores them loses nothing), but the scroll one matters on phones: without it, following "Ask a gardener about tomatoes" from deep inside a plant guide switches the tab while the reader is still scrolled far below it.

While embedded, the tool also adjusts itself: the Bookmark button hides (readers should bookmark the article, not the frame), Share uses the host article's URL when the browser reveals it and the tool's own URL otherwise, dialogs and pop-up messages appear next to the control the reader used rather than at the top or bottom of the frame, and the frame grows when a dialog needs the room. It renders in its light theme to match the article even when the reader's device is in dark mode; add `?theme=auto` to the iframe `src` to follow the reader's setting instead. Deep links still work by putting the hash on the iframe `src` (`/garden#weather`). Printing is better from the standalone URL, so keep that public for the fridge calendar.

## Pre-launch checklist

Content: have the plant timings reviewed by the Marathon County Master Gardener Volunteers or UW-Extension (they will likely have notes on a handful of dates — that's the point, and the partnership is announceable). Start them at `sources.html`, which lists every source and flags the one remaining open date (the first spinach sowing) after our own two-pass accuracy review; when the review is done, update the "Review status" notice on that page. Deploy `sources.html` next to `index.html` — the tool links to it from the footer and from every plant guide. Verify the frost-date copy matches whatever source the newsroom wants to cite.

Technical: set `CONFIG.prototype` to `false`, and set `CONFIG.submitEndpoint` and `askEndpoint` or `askEmail` (or accept that those forms stay hidden). Run `python tests/smoke_test.py` before publishing (see Testing below). Then test on the site's actual domain: confirm the weather tab loads, run one print test (Print button on the calendar tab, one landscape page), and check the page on a phone, inside the real article embed if that's the plan. Point a `<link rel="canonical">` at the production URL if the tool is also reachable at GitHub Pages, so search engines don't treat the two as duplicates.

Editorial: seed the Community Garden with 5–10 real posts before launch (staff gardens, a call-out in the newsletter) so it doesn't launch empty; replace the demo posts in the `POSTS` array with the real ones; decide the Garden of the Week cadence and who picks it.

Analytics: add the site's existing analytics snippet into `<head>` of the standalone page. The tool already reports its own events through that tag when it's present (`gtag`, or a `dataLayer` for Tag Manager), and when embedded it also posts them to the host page, which the snippet above can forward. Events, each with `tool: "wausau-grower"`: `tab_view` (panel), `plant_open` and `plant_star` (plant), `reminders_download`, `print_calendar`, `bookmark_prompt`, `share_page`, `share_garden` and `ask_submit` (mode), `ask_open`, `newsletter_click`, and `sponsor_click` (slot: `masthead` or `ask`). The per-slot sponsor clicks are the number a renewal conversation runs on.

## Operating notes

The NWS API occasionally has brief outages; the page retries a failed request once, then shows the last good forecast (kept on the reader's device for 12 hours) with a note saying when it's from, or, with nothing saved, a "forecast unavailable" state with a Try-again button. Everything date-based (calendar, guides, season stats, monthly tasks) keeps working either way. The forecast URL for Wausau's grid square is cached for 30 days, so a normal visit makes one request to NWS instead of two, and if NWS serves a forecast that hasn't been refreshed in 12 hours the page says so. A tab left open for days refreshes its date-based content when the reader comes back to it and reloads the forecast if it's more than an hour old. There is nothing to restart. Each section boots independently, so a problem in one (a corrupted browser-storage value, say) is logged to the console and never blanks the others. When someone edits the `PLANTS` array, the page checks it on load and warns in the browser console about duplicate ids, dates outside March–October, or a plant without an illustration (which falls back to a generic sprout). The monthly task list (now a checkable checklist — checked state is remembered per month on the reader's device, and resets naturally when the month changes) and the "planting windows open right now" panel update themselves from the reader's clock and the plant database — no editorial maintenance required. The plant database is a single JavaScript array (`PLANTS`); adding a plant is copying one entry and editing it, and it automatically appears in the calendar, guides, and open-windows panel.

## What's deliberately not in v1

Reader accounts (starred "My plants" lists, notes, and likes persist per-device via localStorage, but don't follow the reader across devices until accounts exist), photo uploads (see submission options above), comment persistence (comments on demo posts are in-page only), and push/email frost alerts. The last one is the strongest v2 candidate: a weekly "This week in the garden" newsletter section driven by the same task engine, and a frost-warning email in May and September, would deepen the retention loop considerably.

## Testing

`tests/smoke_test.py` is a regression suite (about 160 checks) that runs the tool in headless Chromium: every tab and deep link, the Back button closing a plant guide, keyboard navigation, favorites and notes surviving reloads, the calendar on a phone, the weather tab against stubbed NWS responses (success, frost, outage, cached fallback, retry), seasonal copy for eight dates across the year, the `.ics` export, both forms in every delivery mode, launch mode, corrupted browser storage, the iframe embed inside a host page, one-page printing, and a WCAG AA contrast audit of every visible piece of text in both light and dark mode (`tests/contrast-audit.js`). It needs no network access.

```bash
pip install playwright
python -m playwright install chromium
python tests/smoke_test.py
```

It also runs on every push through GitHub Actions (`.github/workflows/smoke.yml`). The `tests/` folder is development-only; it doesn't need to be deployed.
