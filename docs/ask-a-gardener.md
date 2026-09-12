# Ask a Gardener — sponsorship offer
*A question desk inside The Wausau Grower, presented by one local organization*

---

## What it is

A fifth tab on The Wausau Grower where readers type a gardening question and get an answer from a local expert by email. The sponsor's name (or logo) sits at the top of the tab as "Presented by," every one of the 31 plant guides carries an "Ask a gardener about tomatoes →" link that opens the form pre-filled, and the best questions and answers run in Wausau Pilot & Review's existing *Ask a Master Gardener* column — so the sponsor is credited on the site and in the tool.

**Preview it with your name on it:** `https://rowanflynnpilot.github.io/wausau-grower/?demo=Your%20Organization#ask`

## Why the Marathon County Master Gardener Volunteers

They already answer the public's gardening questions — it is the program's core Extension mission — and they are already the column's namesake. This turns a print column into a year-round intake channel that:

- **Feeds the column.** Real reader questions, arriving on their own, with permission to publish (first name and neighborhood only) checked by default.
- **Recruits.** Every answer carries the group's name; the lockup links to their site with tracking, so they can see what the placement sends them.
- **Costs them nothing to run.** Questions arrive by email at whatever address they choose. No login, no software, no moderation queue on their side.

## What the sponsor gets

1. "Presented by" lockup on the Ask a Gardener tab — name or logo, one bold line and one muted line of tagline, an optional Directions chip.
2. A pre-filled "Ask a gardener" link inside all 31 plant guides.
3. Credit in the column when a question runs.
4. A tracked link (`utm_campaign=wausau-grower`) so click-through is reportable at renewal.
5. Optionally the masthead "Presented by" slot on the whole tool, sold separately or bundled.

## What WPR gets

A stream of hyperlocal reader questions for the column, a sponsorship that is brand-safe and mission-aligned, and a reason the tool is never stale in the months when nothing is being planted.

## How it works (technical)

One config object, no new infrastructure. `askSponsor` holds the lockup; `askEmail` routes questions to the sponsor's inbox (or `askEndpoint` to a form service or a WordPress route once volume justifies it). Switching a slot from open to sold is editing one line. Details in `deploy.md`.

## Pricing

Set by sales (Chris Weber, weber.chris@wausaupilotandreview.com). Suggested framing: an annual presenting sponsorship of the tab, with the whole-tool masthead slot as the upsell.
