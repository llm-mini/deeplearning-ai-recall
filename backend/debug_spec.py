"""
One-off diagnostic (read-only): reveal how a specialization lists its member
courses on learn.deeplearning.ai, so the scraper can iterate them.

Uses the saved session (storage_state.json). Opens the spec, enters a lesson,
then clicks the top 'Course N' switcher and dumps every menu item (tag, class,
href-or-null, data-*, text). Also dumps the 'Week N' switcher. Writes
debug_spec.json + prints a summary.

Run:
    cd backend
    python debug_spec.py "<spec-url>"
"""

import asyncio
import json
import sys

from playwright.async_api import async_playwright

from services.transcriber import goto_safe, kill_modals, resolve_spec_entry

STATE_FILE = "storage_state.json"
VIEWPORT = {"width": 1600, "height": 1000}

# Dump the open menu/popover items: anything that looks like a clickable row.
DUMP_OPEN_MENU_JS = r"""
() => {
  // Collect visible candidate rows across the whole document (menus often
  // render in a portal outside the button).
  const vis = [...document.querySelectorAll('a, button, li, [role=menuitem], [role=option], div')]
    .filter(n => n.offsetParent && (n.innerText || '').trim());
  const rows = vis.map(n => {
    const attrs = {};
    for (const a of n.attributes) {
      if (a.name.startsWith('data-') || a.name === 'role') attrs[a.name] = a.value;
    }
    return {
      tag: n.tagName,
      cls: (n.className || '').toString().slice(0, 120),
      href: n.getAttribute ? (n.getAttribute('href') || null) : null,
      attrs,
      text: (n.innerText || '').trim().slice(0, 160),
    };
  });
  // Keep rows that mention "Course N" or "Week N" (menu entries), de-dupe by text.
  const seen = new Set();
  const out = [];
  for (const r of rows) {
    if (!/\b(course|week|module)\s*\d/i.test(r.text)) continue;
    const key = r.text + '|' + (r.href || '');
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(r);
  }
  return out.slice(0, 60);
}
"""

# Find header controls whose text looks like "Course 1" / "Week 1".
SWITCHER_JS = r"""
(kind) => {
  const re = new RegExp('^' + kind + '\\s*\\d', 'i');
  const cand = [...document.querySelectorAll('button, a, summary, [role=button], div, span')]
    .filter(n => n.offsetParent && re.test((n.innerText || '').trim()));
  // Prefer the smallest matching element (the actual control, not a wrapper).
  cand.sort((a, b) => (a.innerText || '').length - (b.innerText || '').length);
  return cand.length;
}
"""


async def dump_switcher(page, kind):
    """Click the 'Course'/'Week' switcher and dump the opened menu items."""
    label = kind.capitalize()
    print(f"\n=== {label} switcher ===")
    count = await page.evaluate(SWITCHER_JS, kind)
    print(f"  candidate controls matching '^{label} \\d': {count}")
    if not count:
        return {"kind": kind, "found": False, "items": []}

    # Click the smallest matching control.
    locator = page.locator(
        f"button:text-matches('^{label}\\\\s*\\\\d', 'i'), "
        f"a:text-matches('^{label}\\\\s*\\\\d', 'i'), "
        f"summary:text-matches('^{label}\\\\s*\\\\d', 'i'), "
        f"[role=button]:text-matches('^{label}\\\\s*\\\\d', 'i')"
    ).first
    opened = False
    try:
        await locator.click(timeout=5000)
        opened = True
    except Exception as e:  # noqa: BLE001
        print(f"  click failed: {type(e).__name__}: {e}")

    await page.wait_for_timeout(1200)
    items = await page.evaluate(DUMP_OPEN_MENU_JS)
    print(f"  opened={opened}  menu rows captured: {len(items)}")
    for it in items:
        href = it["href"] if it["href"] else "(no href)"
        print(f"    <{it['tag']} class='{it['cls'][:40]}'> href={href}")
        print(f"       text: {it['text']!r}  attrs={it['attrs']}")

    # Close the menu (Escape) so the next switcher isn't obscured.
    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)
    except Exception:
        pass
    return {"kind": kind, "found": True, "opened": opened, "items": items}


async def main():
    if len(sys.argv) < 2:
        print('usage: python debug_spec.py "<spec-url>"')
        sys.exit(1)
    url = sys.argv[1]

    result = {"input_url": url}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(storage_state=STATE_FILE, viewport=VIEWPORT)
        page = await ctx.new_page()
        try:
            await resolve_spec_entry(page, url)
            await kill_modals(page)
            await page.wait_for_timeout(2000)
            result["landed_url"] = page.url
            try:
                result["title"] = await page.title()
            except Exception:
                result["title"] = ""
            print(f"landed: {result['landed_url']}")
            print(f"title : {result['title']}")

            result["course_switcher"] = await dump_switcher(page, "course")
            result["week_switcher"] = await dump_switcher(page, "week")

            # Extra: all anchors referencing specializations/courses (fallback signal).
            anchors = await page.evaluate(
                r"""() => {
                  const seen = new Set(); const out = [];
                  document.querySelectorAll("a[href*='/specializations/'], a[href*='/courses/']").forEach(a => {
                    const href = a.href.split('#')[0];
                    if (seen.has(href)) return; seen.add(href);
                    out.push({ text: (a.textContent||'').trim().slice(0,80), href });
                  });
                  return out.slice(0, 80);
                }"""
            )
            result["anchors"] = anchors
            print(f"\n=== anchors (/specializations|/courses): {len(anchors)} ===")
            for a in anchors[:40]:
                print(f"    {a['text']!r} -> {a['href']}")
        finally:
            await ctx.close()
            await browser.close()

    with open("debug_spec.json", "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)
    print("\nwrote debug_spec.json")


if __name__ == "__main__":
    asyncio.run(main())
