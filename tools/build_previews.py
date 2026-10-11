"""Build the link previews: one small page and picture per plant guide, plus the tool's own picture.

A link to a guide inside the tool (index.html#plant/garlic) previews like the tool itself, because link previews read
the page's tags and ignore everything after the #. So each guide also gets a page of its own, plant/<id>.html, with
its own title, description and picture (plant/img/<id>.jpg); it forwards readers to the guide at once. The guide's
Share button sends that page (CONFIG.plantPages). The tool's own preview picture is og-image.jpg.

Everything is drawn and worded by the tool itself, in a browser: the same drawings, the same planting windows and the
same date format as the guides. Run it again after changing a plant, its windows or the art, and commit the results;
the smoke test fails while any page is out of date.

Usage:  python tools/build_previews.py [--base https://rowanflynnpilot.github.io/wausau-grower/]
Needs:  pip install playwright && python -m playwright install chromium (and a network connection for the web fonts)
"""
import argparse, html, http.server, json, os, re, sys, threading
from functools import partial
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BASE = "https://rowanflynnpilot.github.io/wausau-grower/"

# one guide's preview: the drawing on the left, framed close (its sky and ground run to the edges), the plant and its
# Wausau planting windows on the right, and WPR's seal at the foot. In the tool's own light colours and type. Long
# names, Latin names and lists shrink until everything fits above the foot.
CARD = r"""(id) => {
  const p = PLANT_BY_ID[id], defs = document.getElementById('art-defs').outerHTML, seal = window.__seal || (window.__seal = document.querySelector('.brand .seal').src);
  const rows = seqBars(p).map(b => `<div class="w"><i style="background:${BAR_TYPES[b.t].color}"></i><b style="color:${BAR_TYPES[b.t].text}">${barLabel(p, b)}</b><span>${fmt(b.s)} – ${fmt(b.e)}</span></div>`).join('');
  document.body.className = '';
  document.body.innerHTML = defs + `<div id="og">
    <div class="pic">${artSVG(p, '', true).replace('viewBox="0 0 320 180"', 'viewBox="47 -62 226 274"')}</div>
    <div class="txt">
      <div class="kick">When to plant in Wausau · Zone 4b/5a</div>
      <h1>${p.name}</h1>
      <div class="lat">${p.latin}</div>
      <div class="ws">${rows}</div>
      <p class="say">${(p.desc.match(/^.*?[.!?](?=\s|$)/) || [p.desc])[0]}</p>
      <div class="foot"><img src="${seal}" alt=""><span><b>The Wausau Grower</b>Wausau Pilot &amp; Review</span></div>
    </div></div>`;
  return document.fonts.ready.then(() => {
    const txt = document.querySelector('#og .txt'), h = txt.querySelector('h1'), lat = txt.querySelector('.lat'), ws = txt.querySelector('.ws');
    const over = () => txt.scrollHeight > txt.clientHeight + 1;
    // the guide's first sentence fills the room under the dates, and gives way first when there's none
    const say = txt.querySelector('.say');
    if (over() || say.getBoundingClientRect().height > 3 * 1.4 * parseFloat(getComputedStyle(say).fontSize) + 2) say.remove();
    for (let size = 92; size > 50 && (h.scrollWidth > h.clientWidth + 1 || h.getBoundingClientRect().height > size * 2.1); size -= 4) h.style.fontSize = size + 'px';
    for (let size = 25; size > 17 && lat.getBoundingClientRect().height > size * 1.45 * 2.05; size -= 1) lat.style.fontSize = size + 'px';
    for (let size = parseFloat(getComputedStyle(h).fontSize); size > 50 && over(); size -= 4) h.style.fontSize = size + 'px';
    for (let size = 23; size > 18 && over(); size -= 1) ws.style.fontSize = size + 'px';
    return !over();
  });
}"""
# the tool's own preview: its title over a row of five drawings on one bed, as in the promo video's cover. The row is one
# drawing, so a wide plant (the pumpkin's vine) reaches past its neighbour's space instead of being cut at a cell's edge.
MAIN = r"""() => {
  const defs = document.getElementById('art-defs').outerHTML, seal = window.__seal || (window.__seal = document.querySelector('.brand .seal').src);
  const ids = ['tomato', 'zinnia', 'sunflower', 'kale', 'pumpkin'], at = i => 95 + 190 * i;
  const row = `<svg class="plant-art" viewBox="-30 -40 1010 278" preserveAspectRatio="xMidYMax meet" aria-hidden="true">
    <rect x="-200" y="-200" width="1400" height="600" fill="var(--sky-veg)"/>
    <ellipse cx="475" cy="236" rx="1100" ry="84" fill="var(--art-soil)" opacity="0.6"/>
    <rect x="-200" y="190" width="1400" height="200" fill="var(--art-soil)"/>
    ${ids.map((id, i) => `<ellipse cx="${at(i)}" cy="196" rx="125" ry="52" fill="var(--art-soil)"/>`).join('')}
    ${ids.map((id, i) => `<g transform="translate(${at(i) - 160} 0)">${(ART[id] || ART.generic)()}</g>`).join('')}</svg>`;
  document.body.className = '';
  document.body.innerHTML = defs + `<div id="og" class="main">
    <div class="head"><div class="kick"><img src="${seal}" alt="">A reader tool from Wausau Pilot &amp; Review</div>
      <h1>The Wausau <span>Grower</span></h1>
      <p>What to plant, when to plant it, and how to keep it alive in central Wisconsin.</p></div>
    <div class="row">${row}</div></div>`;
  return document.fonts.ready.then(() => true);
}"""
STYLE = """
  html, body { margin: 0; background: #fff; }
  #og { width: 1200px; height: 630px; display: grid; grid-template-columns: 520px 680px; overflow: hidden; position: relative;
    font-family: var(--font-ui); color: var(--ink-1); background: var(--surface-1); }
  #og::before { content: ""; position: absolute; left: 0; right: 0; top: 0; height: 8px; background: var(--accent); z-index: 2; }
  #og .pic { position: relative; overflow: hidden; border-right: 1px solid var(--border); }
  #og .pic .plant-art, #og .cell .plant-art { width: 100%; height: 100%; }
  #og .txt { display: flex; flex-direction: column; padding: 60px 56px 40px 52px; min-width: 0; overflow: hidden; }
  #og .kick { font-family: var(--font-head); font-weight: 600; font-size: 24px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--accent); }
  #og h1 { font-family: var(--font-head); font-weight: 700; font-size: 92px; line-height: 1.02; letter-spacing: 0.01em; text-transform: uppercase;
    margin: 10px 0 8px; overflow-wrap: normal; }
  #og .lat { font-family: var(--font-serif); font-style: italic; font-size: 25px; line-height: 1.45; color: var(--ink-muted); }
  #og .ws { margin: 28px 0 24px; display: grid; gap: 12px; font-size: 23px; }
  #og .w { display: flex; flex-wrap: wrap; align-items: baseline; column-gap: 12px; line-height: 1.3; }
  #og .w i { width: 0.78em; height: 0.78em; border-radius: 0.22em; align-self: center; }
  #og .w b { font-weight: 700; }
  #og .w span { font-weight: 600; color: var(--ink-1); font-variant-numeric: tabular-nums; white-space: nowrap; }
  #og .say { font-family: var(--font-serif); font-style: italic; font-size: 24px; line-height: 1.4; color: var(--ink-2); margin: 0 0 20px; }
  /* the foot stays readable when a feed shows the card at 500px: the names at 24-26px, on two lines beside the seal */
  #og .foot { margin-top: auto; display: flex; align-items: center; gap: 16px; font-size: 22px; line-height: 1.2; color: var(--ink-2); white-space: nowrap; }
  #og .foot img { width: 58px; height: 58px; border-radius: 50%; }
  #og .foot span { display: flex; flex-direction: column; }
  #og .foot b { font-family: var(--font-head); font-weight: 600; font-size: 26px; letter-spacing: 0.02em; color: var(--ink-1); }
  #og.main { display: block; background: var(--sky-veg); }
  #og.main .head { position: relative; z-index: 1; padding: 54px 70px 0; }
  #og.main .kick { display: flex; align-items: center; gap: 14px; }
  #og.main .kick img { width: 46px; height: 46px; border-radius: 50%; }
  #og.main h1 { font-size: 112px; margin: 12px 0 8px; }
  #og.main h1 span { color: var(--accent); }
  #og.main p { font-family: var(--font-serif); font-style: italic; font-size: 28px; line-height: 1.45; color: var(--ink-2); margin: 0; max-width: 1000px; }
  #og.main .row { position: absolute; left: 0; right: 0; bottom: 0; height: 330px; }
  #og.main .row .plant-art { display: block; width: 100%; height: 100%; }
"""


def esc(s):
    return html.escape(s, quote=True)


def plant_page(base, d):
    url = f"{base}plant/{d['id']}.html"
    title = f"When to plant {d['sentence']} in Wausau"
    desc = d["description"]
    target = f"../#plant/{d['id']}"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} — The Wausau Grower</title>
<!-- Built by tools/build_previews.py: a link preview for the {esc(d['name'])} guide, which forwards to it. Rebuild, don't edit. -->
<meta name="description" content="{esc(desc)}">
<meta name="robots" content="noindex, follow">
<link rel="canonical" href="{esc(url)}">
<meta property="og:type" content="article">
<meta property="og:site_name" content="Wausau Pilot &amp; Review">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{esc(url)}">
<meta property="og:image" content="{esc(base)}plant/img/{d['id']}.jpg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="{esc(d['alt'])}">
<meta name="twitter:card" content="summary_large_image">
<script>location.replace({json.dumps(target)}.replace('#', location.search + '#'));</script>
<style>body {{ font: 17px/1.5 system-ui, sans-serif; margin: 48px 24px; color: #111; }} a {{ color: #2b655d; font-weight: 600; }}</style>
</head>
<body>
<p><a href="{esc(target)}">Open the {esc(d['sentence'])} guide in The Wausau Grower</a></p>
</body>
</html>
"""


# what a page says, worked out by the tool: the guide's windows ("Plant cloves Oct 1 – Oct 25"), then its first sentence
FACTS = r"""() => PLANTS.map(p => {
  const ws = seqBars(p).map(b => `${barLabel(p, b)} ${fmt(b.s)} – ${fmt(b.e)}`);
  const first = (p.desc.match(/^.*?[.!?](?=\s|$)/) || [p.desc])[0];
  return { id: p.id, name: p.name, sentence: sentenceName(p), description: `${ws.join(' · ')}. ${first}`,
    alt: `A drawing of ${sentenceName(p)}, beside its Wausau planting dates: ${ws.join('; ')}` };
})"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE, help="where the tool is published, ending in /")
    ap.add_argument("--only", help="comma-separated plant ids (pictures only; pages are always all rebuilt)")
    args = ap.parse_args()
    base = args.base if args.base.endswith("/") else args.base + "/"
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    os.makedirs(os.path.join(ROOT, "plant", "img"), exist_ok=True)
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=ROOT))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    local = f"http://127.0.0.1:{httpd.server_address[1]}"
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1200, "height": 630}, device_scale_factor=1, color_scheme="light", reduced_motion="reduce")
        page = ctx.new_page()
        page.route("https://api.weather.gov/**", lambda r: r.abort())
        page.goto(local + "/index.html")
        page.wait_for_function("() => document.querySelectorAll('.pcard').length > 0")
        page.evaluate("document.fonts.ready")
        facts = page.evaluate(FACTS)
        ids = args.only.split(",") if args.only else [d["id"] for d in facts]
        page.add_style_tag(content=STYLE)
        for i, pid in enumerate(ids):
            if not page.evaluate(CARD, pid):
                print(f"\n  {pid}: still runs past the foot at the smallest sizes; check plant/img/{pid}.jpg")
            page.locator("#og").screenshot(path=os.path.join(ROOT, "plant", "img", f"{pid}.jpg"), type="jpeg", quality=88)
            print(f"  {i + 1}/{len(ids)} {pid}", end="\r")
        page.evaluate(MAIN)
        page.locator("#og").screenshot(path=os.path.join(ROOT, "og-image.jpg"), type="jpeg", quality=88)
        b.close()
    httpd.shutdown()
    for d in facts:
        with open(os.path.join(ROOT, "plant", f"{d['id']}.html"), "w", encoding="utf-8", newline="\n") as f:
            f.write(plant_page(base, d))
    # the tool's own preview points at its picture, wherever the tool is published
    ix = os.path.join(ROOT, "index.html")
    src = open(ix, encoding="utf-8").read()
    out = re.sub(r'<meta property="og:image" content="[^"]*">', f'<meta property="og:image" content="{base}og-image.jpg">', src, count=1)
    if out != src:
        open(ix, "w", encoding="utf-8", newline="").write(out)
    print(f"\n{len(ids)} pictures, {len(facts)} pages, og-image.jpg, for {base}")


if __name__ == "__main__":
    main()
