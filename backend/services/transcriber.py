"""
Playwright-based course transcriber for deeplearning.ai.

Uses a logged-in session captured once by save_session.py (storage_state.json)
to drive a headless browser that enumerates lessons live and pulls real video
transcripts -- including gated/paid content. Ported from the standalone
D:\\LEARNING2\\CourseRevise\\transcribe_course_async.py, exposed as importable
async functions (no argv/main, no file output).

Two entry points:
  - resolve_specialization(url) -> [{slug, url}]  (fast, lists member courses)
  - scrape_single_course(url, on_progress, workers) -> dict with syllabus +
    per-lesson transcripts for ONE course (the fetch unit).
"""

import asyncio
import re
from urllib.parse import unquote

from playwright.async_api import TimeoutError as PWTimeout
from playwright.async_api import async_playwright

STATE_FILE = "storage_state.json"
VIEWPORT = {"width": 1600, "height": 1000}

TS_LINE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
DROP_LINES = {"transcript", "english", "hide transcript", "show transcript"}

# innerText of the smallest visible element holding >=4 timestamp cues.
TRANSCRIPT_JS = r"""
() => {
  const vis = [...document.querySelectorAll('*')].filter(n => n.offsetParent);
  const tsCount = t => (t.match(/(^|\n)\d{1,2}:\d{2}(\n|$)/g) || []).length;
  let best = null, bestLen = Infinity;
  for (const n of vis) {
    const t = n.innerText || '';
    if (tsCount(t) >= 4 && t.length < bestLen) { best = n; bestLen = t.length; }
  }
  return best ? best.innerText : '';
}
"""

# Lesson anchors in the left sidebar nav (real lessons carry a '.text-left').
# Returns href + the sidebar title text so lessons get readable names.
LESSON_ANCHORS_JS = r"""
() => {
  const side = document.querySelector('.drawer-side') || document.body;
  return [...side.querySelectorAll("a[href*='/lesson/']")]
    .filter(a => a.querySelector('.text-left'))
    .map((a) => ({
      href: a.href.split('?')[0].split('#')[0].replace(/\/$/, ''),
      title: (a.querySelector('.text-left').textContent || '').trim()
    }));
}
"""


# ---------------------------------------------------------------- pure helpers

def slugify(s):
    s = unquote(s)
    s = re.sub(r"[^0-9A-Za-zÀ-￿]+", "-", s.strip().lower()).strip("-")
    return s[:80] or "lesson"


def course_slug_from_url(url):
    m = re.search(r"/(?:courses|specializations)/([^/?#]+)", url)
    return m.group(1) if m else "course"


def prettify_slug(slug):
    return re.sub(r"[-_]+", " ", slug).strip().title() or "Course"


def is_quiz_lesson(lesson):
    """Quizzes carry no transcript and shouldn't appear in the outline."""
    t = (lesson.get("title") or "").lower()
    s = lesson.get("title_slug") or ""
    return "quiz" in t or "quiz" in s


def course_name_from_title(raw):
    """DLAI page titles look like 'Lesson - Course - DeepLearning.AI' (on a
    lesson page) or 'Course - DeepLearning.AI' (on a landing page). The course
    name is the last non-brand segment."""
    if not raw:
        return ""
    parts = [p.strip() for p in raw.split(" - ") if p.strip()]
    parts = [p for p in parts if p.lower() not in ("deeplearning.ai", "dlai")]
    return parts[-1] if parts else ""


def clean_transcript(raw):
    out = []
    for ln in (x.strip() for x in raw.splitlines()):
        if not ln or TS_LINE.match(ln) or ln.lower() in DROP_LINES:
            continue
        out.append(ln)
    return "\n".join(out).strip()


# ---------------------------------------------------------------- async helpers

async def goto_safe(page, url, settle=0):
    """Navigate tolerantly. DLAI/Next.js pages can hang on 'domcontentloaded',
    so commit on first response and settle with a fixed wait."""
    try:
        await page.goto(url, wait_until="commit", timeout=60000)
    except PWTimeout:
        pass
    if settle:
        await page.wait_for_timeout(settle)


async def kill_modals(page):
    await page.evaluate(
        "document.querySelectorAll('dialog').forEach(d => {"
        "  try { d.close(); } catch (e) {} d.remove(); });"
    )


async def page_lessons(page):
    """Ordered [{href, title}] of real lessons on the current page."""
    out, seen = [], set()
    for a in await page.evaluate(LESSON_ANCHORS_JS):
        h = a["href"]
        if "/lesson/" in h and h not in seen:
            seen.add(h)
            out.append({"href": h, "title": a.get("title", "")})
    return out


async def page_course_name(page):
    try:
        raw = (await page.title() or "").strip()
    except Exception:
        raw = ""
    return course_name_from_title(raw)


async def collect_week_entries(page):
    """Section entries for a course. Handles both 'Week N' and 'Module N'
    layouts. Returns (entries, label) where entries = [(num, href)] sorted and
    label is 'Week' or 'Module'."""
    dd = await page.evaluate(
        r"""() => {
          const out = [];
          document.querySelectorAll(".dropdown-content a[href*='/lesson/']").forEach(a => {
            const m = (a.textContent || '').trim().match(/(week|module)\s*(\d+)/i);
            if (m) out.push({ kind: m[1].toLowerCase(), wk: parseInt(m[2]),
                              href: a.href.split('?')[0].split('#')[0] });
          });
          return out;
        }"""
    )
    by_week, kinds = {}, []
    for d in dd:
        by_week.setdefault(d["wk"], d["href"].rstrip("/"))
        kinds.append(d["kind"])

    if not by_week:
        data = await page.evaluate(
            r"""() => {
              const out = [];
              document.querySelectorAll("a[href*='/lesson/']").forEach(a => {
                const href = a.href.split('?')[0].split('#')[0];
                const txt = (a.textContent || '').trim();
                let wk = null, kind = 'week';
                const m = href.match(/(week|module)[-_]?(\d+)/i);
                if (m) { kind = m[1].toLowerCase(); wk = parseInt(m[2]); }
                const m2 = txt.match(/^(week|module)\s*(\d+)/i);
                if (m2) { kind = m2[1].toLowerCase(); wk = parseInt(m2[2]); }
                if (wk) out.push({ kind, wk, href });
              });
              return out;
            }"""
        )
        for d in data:
            by_week.setdefault(d["wk"], d["href"].rstrip("/"))
            kinds.append(d["kind"])

    label = "Module" if kinds and kinds.count("module") >= kinds.count("week") else "Week"
    return [(k, by_week[k]) for k in sorted(by_week)], label


async def resolve_spec_entry(page, url):
    await goto_safe(page, url, settle=6000)
    if "learn.deeplearning.ai" not in page.url or "/lesson/" not in page.url:
        links = await page.eval_on_selector_all(
            "a[href*='/specializations/'][href*='/lesson']",
            "els => els.map(a => a.href)")
        if not links:
            links = await page.eval_on_selector_all(
                "a[href*='/lesson/']", "els => els.map(a => a.href)")
        if links:
            await goto_safe(page, links[0], settle=8000)
    return page.url


async def collect_specialization_courses(page):
    await kill_modals(page)
    data = await page.evaluate(
        r"""() => {
          const out = [];
          document.querySelectorAll(".dropdown-content a[href*='/specializations/'][href*='/lesson/']").forEach(a => {
            out.push({ href: a.href.split('?')[0].split('#')[0],
                       isWeek: /(week|module)\s*\d+/i.test((a.textContent || '')) });
          });
          return out;
        }"""
    )
    ordered, seen = [], set()
    for d in data:
        if d["isWeek"]:
            continue
        h = d["href"].rstrip("/")
        if h not in seen:
            seen.add(h)
            ordered.append(h)
    return ordered


async def grab_transcript(page, url):
    """Robust under concurrency: wait generously for the 'Show Transcript'
    control, reload once if it never appears, and retry the extraction. Only
    reports no-transcript after a reload still shows no button (avoids
    spuriously skipping real videos)."""
    btn = None
    appeared = False
    for _load in range(2):                            # initial load + 1 reload
        await goto_safe(page, url)
        btn = page.locator("button", has_text="Show Transcript").first
        for _ in range(15):                           # up to ~15s per load
            await page.wait_for_timeout(1000)
            if await btn.count() > 0:
                appeared = True
                break
        if appeared:
            break
    if not appeared:
        return None  # genuine non-video (reading / lab / quiz)

    for attempt in range(3):
        await kill_modals(page)
        try:
            await btn.click(timeout=8000)
            break
        except PWTimeout:
            if attempt == 2:
                return None
            await page.wait_for_timeout(1000)

    raw = ""
    for _ in range(5):
        await page.wait_for_timeout(1500)
        raw = await page.evaluate(TRANSCRIPT_JS)
        if raw:
            break
    return clean_transcript(raw) if raw else None


# ------------------------------------------------------------------ enumeration

async def enumerate_course(page, course_url):
    """Canonical ordered lessons for one course: [{week, href, title, title_slug}]
    plus has_weeks flag and the section label ('Week' | 'Module')."""
    await goto_safe(page, course_url, settle=7000)
    if "learn.deeplearning.ai" not in page.url or "/lesson/" not in page.url:
        first = await page.eval_on_selector_all(
            "a[href*='/lesson/']", "els => els.length ? els[0].href : null")
        if first:
            await goto_safe(page, first, settle=7000)
    await kill_modals(page)

    display_title = await page_course_name(page)
    entries, label = await collect_week_entries(page)
    lessons, seen = [], set()
    has_weeks = bool(entries)

    if not has_weeks:                                 # short course
        for l in await page_lessons(page):
            if l["href"] not in seen:
                seen.add(l["href"])
                lessons.append({"week": 0, "href": l["href"], "title": l["title"]})
    else:
        for wk, entry in entries:
            await goto_safe(page, entry, settle=5000)
            await kill_modals(page)
            for l in await page_lessons(page):
                if l["href"] not in seen:
                    seen.add(l["href"])
                    lessons.append({"week": wk, "href": l["href"], "title": l["title"]})

    for l in lessons:
        l["title_slug"] = slugify(l["href"].rstrip("/").split("/")[-1])
        if not l["title"]:
            l["title"] = prettify_slug(l["title_slug"])

    # Drop quizzes entirely -- no transcript, shouldn't show in the outline.
    lessons = [l for l in lessons if not is_quiz_lesson(l)]

    return lessons, has_weeks, display_title, label


# -------------------------------------------------------------------- syllabus

def build_syllabus(video_lessons, has_weeks, label="Week"):
    """Built from lessons that actually produced a transcript (videos only), so
    the outline never lists labs/readings/quizzes. Shape matches the frontend:
      - no weeks  -> flat ["Lesson Title", ...]
      - weeks     -> [{title:"<label> N", topics:[{title, lessons:[title]}]}]
    """
    if not has_weeks:
        return [l["title"] for l in video_lessons]

    by_week = {}
    for l in video_lessons:
        by_week.setdefault(l["week_num"] or 1, []).append(l["title"])
    modules = []
    for wk in sorted(by_week):
        topics = [{"title": t, "lessons": [t]} for t in by_week[wk]]
        modules.append({"title": f"{label} {wk}", "topics": topics})
    return modules


# -------------------------------------------------------------------- fetching

async def _worker(browser, queue, results, on_done):
    ctx = await browser.new_context(storage_state=STATE_FILE, viewport=VIEWPORT)
    page = await ctx.new_page()
    try:
        while True:
            try:
                href = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            text = None
            for attempt in range(2):
                try:
                    text = await grab_transcript(page, href)
                    break
                except Exception:
                    if attempt == 1:
                        text = None
                    else:
                        await page.wait_for_timeout(1000)
            results[href] = text
            if on_done:
                on_done(text)
    finally:
        await ctx.close()


# ------------------------------------------------------------------ entry points

# Member courses live in the top "Course N" switcher. Non-active members render
# with a "Course <N> - <pct>%" marker; the active one is the current page.
MEMBER_MARKERS_JS = r"""
() => {
  const out = []; const seen = new Set();
  document.querySelectorAll("a[href*='/lesson/']").forEach(a => {
    const txt = (a.textContent || '').replace(/\s+/g, ' ').trim();
    const m = txt.match(/Course\s*(\d+)\s*-\s*\d+\s*%/i);
    if (!m) return;
    const num = parseInt(m[1]);
    if (seen.has(num)) return;
    seen.add(num);
    out.push({ num, title: txt.slice(0, m.index).trim(),
               href: a.href.split('#')[0] });
  });
  return out;
}
"""


async def resolve_specialization(url):
    """Resolve a specialization into its ordered member courses, each identified
    by its entry-lesson URL. Returns {title, members:[{course_num, url}]}.

    Members are the entries of the top "Course N" switcher. Navigating a member's
    entry URL loads that course's own weeks/lessons, so each is scraped normally.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(storage_state=STATE_FILE, viewport=VIEWPORT)
        page = await ctx.new_page()
        try:
            await resolve_spec_entry(page, url)   # lands on the first course's first lesson
            await kill_modals(page)
            landed = page.url.split("#")[0]
            spec_title = await page_course_name(page)

            # Best-effort: open the course switcher so every member renders.
            try:
                sw = page.get_by_text(re.compile(r"^Course\s*\d+$")).first
                if await sw.count() > 0:
                    await sw.click(timeout=3000)
                    await page.wait_for_timeout(800)
            except Exception:
                pass

            markers = await page.evaluate(MEMBER_MARKERS_JS)

            # Fallback name for the active (current) course: its real slug is
            # exposed in a course-feedback link's `courseSlug` query param.
            active_slug = await page.evaluate(
                r"""() => {
                  const a = document.querySelector("a[href*='courseSlug=']");
                  if (!a) return '';
                  const m = a.href.match(/courseSlug=([^&]+)/);
                  return m ? decodeURIComponent(m[1]) : '';
                }"""
            )
        finally:
            await ctx.close()
            await browser.close()

    # Markers are the authoritative member list when the switcher menu is open
    # (it then includes the active course, Course 1, too). A spec entry always
    # lands on Course 1, so only add the landed page when Course 1 has no marker
    # (menu stayed closed) -- otherwise Course 1 would be duplicated.
    by_num = {}
    for m in markers:
        by_num[m["num"]] = {"url": m["href"], "title": m["title"] or None}

    if 1 not in by_num:
        by_num[1] = {"url": landed,
                     "title": prettify_slug(active_slug) if active_slug else None}

    members = [{"course_num": k, "url": by_num[k]["url"], "title": by_num[k]["title"]}
               for k in sorted(by_num)]

    return {"title": spec_title or prettify_slug(course_slug_from_url(url)), "members": members}


async def scrape_single_course(url, on_total=None, on_progress=None, workers=4):
    """Scrape ONE course (the fetch unit).

    on_total(n)              -> initial upper-bound estimate of video count.
    on_progress(saved, est)  -> saved transcripts so far, and the current video
                                estimate (denominator shrinks as non-video
                                lessons -- labs/readings -- are discovered).

    total_lessons in the result counts ONLY videos (lessons that yielded a
    transcript), so the UI progress ring reflects videos, not labs/readings.

    Returns: {title, slug, kind, total_lessons, syllabus,
              lessons: [{week_num, position, title, slug, transcript}]}
    """
    slug = course_slug_from_url(url)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        # 1) enumerate (sequential, fixes canonical order)
        enum_ctx = await browser.new_context(storage_state=STATE_FILE, viewport=VIEWPORT)
        enum_page = await enum_ctx.new_page()
        lessons, has_weeks, display_title, label = await enumerate_course(enum_page, url)
        await enum_ctx.close()

        # quizzes already excluded in enumerate_course; fetch every remaining lesson
        fetch_list = list(lessons)
        total_enum = len(fetch_list)
        if on_total:
            on_total(total_enum)  # upper bound; refined below as labs are found

        # 2) fetch (concurrent) with live, video-only progress
        queue = asyncio.Queue()
        for l in fetch_list:
            queue.put_nowait(l["href"])
        results = {}
        state = {"saved": 0, "skipped": 0}

        def _done(text):
            if text:
                state["saved"] += 1
            else:
                state["skipped"] += 1  # non-video lesson (lab / reading)
            if on_progress:
                # denominator = videos only -> shrinks as non-videos surface
                on_progress(state["saved"], total_enum - state["skipped"])

        n = max(1, min(workers, total_enum or 1))
        await asyncio.gather(*(_worker(browser, queue, results, _done)
                               for _ in range(n)))
        await browser.close()

    # 3) order + number (deterministic), assign week position
    per_week = {}
    out_lessons = []
    for l in lessons:
        text = results.get(l["href"])
        if not text:
            continue
        wk = l["week"] or 0
        per_week[wk] = per_week.get(wk, 0) + 1
        out_lessons.append({
            "week_num": wk,
            "position": per_week[wk],
            "title": l["title"],
            "slug": l["title_slug"],
            "transcript": text,
        })

    return {
        "title": display_title or prettify_slug(slug),
        "slug": slug,
        "kind": "course" if has_weeks else "short_course",
        "total_lessons": len(out_lessons),  # videos only
        "syllabus": build_syllabus(out_lessons, has_weeks, label),
        "lessons": out_lessons,
    }
