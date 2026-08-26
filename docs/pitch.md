# The Wausau Grower
### A year-round garden companion for Wausau Pilot & Review readers
*Concept proposal — July 2026*

---

## The idea in one paragraph

A free, interactive gardening tool on wausaupilotandreview.com built specifically for Marathon County's growing conditions: a planting calendar tuned to our ~May 15 last frost and ~Oct 1 first frost, growing guides for the flowers, vegetables, and herbs that actually succeed in zone 4b/5a, live weather translated into plain-English garden decisions ("frost risk Thursday night — cover your tomatoes"), and a community showcase where readers post photos of what's growing and comment on each other's gardens. Generic gardening sites give advice calibrated for Ohio or Oregon; this gives advice calibrated for the 54401.

## Why it fits Wausau Pilot & Review

**It creates habitual, returning traffic.** News traffic is spiky — readers come when something happens. A garden tool is the opposite: the weather changes daily, the season advances weekly, and the question "can I plant yet?" gets asked in thousands of Wausau households every April through June. A reader who checks frost risk before transplanting in May is back for succession-sowing advice in July and frost warnings in September. It's a reason to open the site when there's no news.

**It serves the existing audience.** The Pilot & Review already runs Outdoors and Food/recipes coverage — gardening sits exactly at that intersection, and central Wisconsin's demographics skew heavily toward homeowners with yards and a strong gardening culture (this is potato-and-pickle country).

**The community section generates content, not just consumes it.** Reader photo submissions are free, hyperlocal, feel-good content — the peonies planted in 1974, the kid's nine-foot sunflower, the zucchini avalanche. Each strong submission can be promoted into a weekly "Garden of the Week" feature article, feeding the tool's audience back into the newsroom's content and vice versa.

**It's sponsorable without compromising editorial.** A garden tool is a natural, brand-safe home for local sponsors: garden centers, hardware stores, landscapers, the farmers market, seed companies. "The Wausau Grower, presented by [local nursery]" is an easy annual sell, and seasonal sponsorship (spring planting, fall cleanup) creates multiple inventory slots.

## What the tool does (v1, prototyped)

**1. Wausau Planting Calendar.** A visual timeline, March through October, for 31 plants: when to start seeds indoors, when to transplant, when to direct sow, and when to expect harvest or bloom — all anchored to Wausau's average frost dates, which are drawn on the chart alongside a live "today" marker. Filterable by vegetables, flowers, and herbs, with chart and list views and a one-page printable version.

**2. Plant Guides.** A card for every plant with sun, spacing, watering, days to maturity, difficulty, and a zone-4b/5a-specific tip written for our conditions — short-season tomato varieties, why basil can't go out before June, why fall is the right time to plant peonies and garlic, why frost makes kale sweeter.

**3. Weather & This Week in the Garden.** Live National Weather Service forecast for Wausau (free government API, no licensing cost) converted into decisions: a status banner ("Clear to plant frost-tender crops" / "Frost risk in the forecast — hold off"), frost-risk flags on any night forecast at or below 36°F, rain probability, season-progress stats, and a monthly task list that changes twelve times a year — a built-in reason the page is never stale. A "planting windows open right now" panel is generated automatically from the plant database, so the tool always answers the question that brings people back: *what can I plant this week?*

**4. Community Garden.** Reader photo posts with captions, likes, and comments, a working "Share your garden" submission form, and a "Garden of the Week" badge that feeds a natural weekly editorial feature. Launch version: submissions lightly moderated by staff before publishing (same workflow as letters or event submissions). This is the retention engine and the emotional core of the tool.

**5. Personalization and return visits.** Readers can star plants into a "My plants" list that filters both the calendar and the guides, and keep private per-plant notes ("Early Girl did great by the fence"). Both persist on the reader's device with no account needed. A "Welcome back" strip greets returning readers with what changed — which planting windows opened since their last visit, and which of *their* starred plants' windows are about to close. Every tab and plant guide has a bookmarkable link, and Bookmark/Share buttons plus a newsletter call-to-action are built into the page. Reader accounts (or the newsletter login) remain the v2 path to cross-device persistence — a soft on-ramp to registration.

## What exists today

A working single-file build with all four sections functional: the full calendar and 31 researched plant guides — each with its own hand-drawn illustration — live NWS integration with graceful error handling, per-device personalization, and the community section with demo posts and working like/comment interactions. The page carries full Pilot & Review branding: the typewriter seal, the masthead typography, and the newsroom's teal. It runs anywhere, has no dependencies, no build step, and supports light and dark mode. It is embeddable as-is.

## Path from prototype to launch

**Phase 1 — Embed (low effort).** Publish the tool as a standalone page on the site. Calendar, guides, and live weather work today. Community section stays in "coming soon" or demo mode. Cost: hosting is effectively zero; NWS data is free.

**Phase 2 — Real submissions (moderate effort).** Add a photo submission form (even a Google Form or email pipeline works at first) with staff moderation before posts appear. No accounts needed initially — name and neighborhood only. This keeps moderation load light and spam near zero.

**Phase 3 — Grow it (ongoing).** Garden of the Week editorial feature; seasonal push — email newsletter section ("This week in the garden") pulling from the monthly task engine; sponsor placement; expand the plant database based on what readers ask for; optional reader accounts and comment threads if volume justifies it.

## Content and data notes

Frost dates and hardiness zone come from the USDA 2023 Plant Hardiness Zone Map and regional climate normals (Wausau sits on the 4b/5a boundary; average last frost ~May 11–20, first frost ~Oct 1–10). Forecasts come from api.weather.gov — free, no API key, no rate concerns at this scale. Growing guidance was written for zone 4b/5a conditions; before launch it should get a review pass from a local authority — the Marathon County UW-Extension horticulture program or a master gardener volunteer would be the natural partner, and that partnership is itself a story.

## Risks and honest caveats

Moderation is the main operating cost — photo submissions need a human check before publishing, though volume will likely be modest and the workflow matches things the newsroom already does. Seasonality means traffic will crater in January; the monthly task engine (seed catalogs, indoor starts) and winter content soften but don't eliminate this. And gardening advice attracts opinions — expect passionate emails about the correct way to prune lilacs. That's engagement.

## The ask

Review the tool, kick the tires, and if it holds up: a green light for Phase 1 (publish as a beta page), an intro conversation with UW-Extension Marathon County for content review, and a decision on whether to shop the sponsorship before or after beta.
