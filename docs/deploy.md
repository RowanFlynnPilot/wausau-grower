# The Wausau Grower — Deployment Guide
*How to take the tool to production on wausaupilotandreview.com*

---

## What you're deploying

One file: `index.html`. It is fully self-contained — no build step, no dependencies, no database, no server-side code. All CSS and JavaScript are inline; the plant and community illustrations are inline SVG, and the Pilot & Review typewriter seal is embedded as a data URI. External calls from the reader's browser: the National Weather Service API (api.weather.gov — free, keyless, CORS-enabled) and Google Fonts for Oswald/Merriweather (the page falls back to system serif/sans if fonts are unreachable).

Reader personalization (starred "My plants", per-plant notes, likes, last-visit tracking for the "Welcome back" strip) is stored in the reader's own browser via localStorage under `wg:*` keys — nothing is sent anywhere, nothing to host, and it degrades gracefully to session-only behavior in private browsing.

## Configuration

At the top of the second `<script>` block there is a single `CONFIG` object. It ships in fully functional demo mode. Things to set for production:

**`sponsor`** — the masthead sponsorship slot. Set to `{ name: 'Sponsor Name', url: 'https://…' }` and a "Presented by" credit appears under the deck with a `rel="sponsored"` link (which keeps Google happy). Leave `null` until the slot is sold; nothing renders.

**`submitEndpoint`** — where "Share your garden" submissions go. Leave `null` and the form posts locally in the reader's page only (demo behavior). Set it to any URL that accepts a JSON POST of `{who, where, kind, cap}` and the form instead submits for moderation and shows the reader a "thanks — pending review" message. Three realistic options, cheapest first:

1. **Formspree (or similar form service)** — five-minute setup, submissions arrive by email, free tier covers likely volume. Right answer for the beta.
2. **Google Apps Script web app** — submissions append to a Google Sheet the newsroom already knows how to use. Free, slightly more setup.
3. **WordPress REST route** — a tiny custom endpoint that creates a draft post in a "Garden submissions" category, so moderation is just the normal WordPress publish flow, and photo upload can be added at the same time. Right answer once the beta proves out.

Note the form doesn't yet upload actual photos (submissions are text + an illustration choice); photo upload arrives with option 3, since it needs real storage and moderation anyway.

**`newsletterUrl`** — where the "Join the newsletter" call-to-action buttons point. It defaults to the site homepage (which carries the signup form); point it at a dedicated signup page when one exists.

## Publishing options

**Standalone page (recommended for beta).** Serve the file at a clean URL like `/garden`. On WordPress/Newspack hosts, use a page template or ask the host to serve the file directly. Cleanest URL, full-viewport experience, easiest to share and to print. GitHub Pages on this repository also works as a beta host — the file is named `index.html` for exactly that reason.

**Iframe embed.** Drop the tool into a normal WordPress page if serving a raw HTML file is awkward. The page detects that it is embedded and posts its own height to the parent using the standard WPR widget message (`{type: "wpr-embed-height", id: "wausau-grower", height}`), so the iframe can grow and shrink with the content instead of sitting at a fixed height. Paste this once in the host page (a Custom HTML block works):

```html
<iframe id="wausau-grower" src="/garden" title="The Wausau Grower"
        style="width:100%;min-height:900px;border:0" loading="lazy"></iframe>
<script>
window.addEventListener('message', function (e) {
  if (e.data && e.data.type === 'wpr-embed-height' && e.data.id === 'wausau-grower') {
    document.getElementById('wausau-grower').style.height = e.data.height + 'px';
  }
});
</script>
```

While embedded, the Bookmark button hides itself (readers should bookmark the article, not the frame), the Share button shares the host article's URL, and plant-guide dialogs open next to the spot the reader clicked rather than at the top of the frame. Deep links still work by putting the hash on the iframe `src` (`/garden#weather`). Printing is better from the standalone URL, so keep that public for the fridge calendar.

## Pre-launch checklist

Content: have the plant timings reviewed by the Marathon County Master Gardener Volunteers or UW-Extension (they will likely have notes on a handful of dates — that's the point, and the partnership is announceable). Start them at `sources.html`, which lists every source and flags the one remaining open date (the first spinach sowing) after our own two-pass accuracy review; when the review is done, update the "Review status" notice on that page. Deploy `sources.html` next to `index.html` — the tool links to it from the footer and from every plant guide. Verify the frost-date copy matches whatever source the newsroom wants to cite.

Technical: set `CONFIG.submitEndpoint` (or consciously launch with the form in demo mode and the demo-note visible); test the page on the site's actual domain — the NWS fetch should just work, but confirm the weather tab loads; run one print test (Print button on the calendar tab → one landscape page); check the page on a phone.

Editorial: seed the Community Garden with 5–10 real posts before launch (staff gardens, a call-out in the newsletter) so it doesn't launch empty; replace the demo posts in the `POSTS` array with the real ones; decide the Garden of the Week cadence and who picks it.

Analytics: add the site's existing analytics snippet into `<head>` — tab clicks can be tracked later; pageviews are enough to judge the beta.

## Operating notes

The NWS API occasionally has brief outages; the page retries a failed request once, then shows a visible "forecast unavailable" state with a Try-again button while everything date-based (calendar, guides, season stats, monthly tasks) keeps working. There is nothing to restart. Each section boots independently, so a problem in one (a corrupted browser-storage value, say) is logged to the console and never blanks the others. When someone edits the `PLANTS` array, the page checks it on load and warns in the browser console about duplicate ids, dates outside March–October, or a plant without an illustration (which falls back to a generic sprout). The monthly task list (now a checkable checklist — checked state is remembered per month on the reader's device, and resets naturally when the month changes) and the "planting windows open right now" panel update themselves from the reader's clock and the plant database — no editorial maintenance required. The plant database is a single JavaScript array (`PLANTS`); adding a plant is copying one entry and editing it, and it automatically appears in the calendar, guides, and open-windows panel.

## What's deliberately not in v1

Reader accounts (starred "My plants" lists, notes, and likes persist per-device via localStorage, but don't follow the reader across devices until accounts exist), photo uploads (see submission options above), comment persistence (comments on demo posts are in-page only), and push/email frost alerts. The last one is the strongest v2 candidate: a weekly "This week in the garden" newsletter section driven by the same task engine, and a frost-warning email in May and September, would deepen the retention loop considerably.
