"""
Mind map LLM service (OpenRouter via langchain_openai.ChatOpenAI).

Map-reduce so we never dump a whole course into one call:
  - summarize_lesson(...)  MAP: one small call per lesson (cached in DB by the router)
  - generate_seed(...)     REDUCE: root + top concepts from the short summaries only
  - expand_node(...)       grow a node into child concepts/leaves (may use web tools)
  - answer_question(...)   attach a Q&A branch (may use web tools)

The tool loop binds web_search/fetch_url; if the chosen free model can't do
function-calling, it falls back to a plain JSON call (no tools). Either way the
final message is parsed with a tolerant JSON extractor.
"""

import json
import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from core.config import MINDMAP_MODEL, OPENROUTER_API_KEY, OPENROUTER_BASE
from services import web

_MAX_TOOL_HOPS = 4
MAX_CONCEPTS = 8   # top-level sections (bounds tree breadth + build cost)
MAX_LEAVES = 6     # terminal leaves per concept


def _model(temperature: float = 0.4) -> ChatOpenAI:
    return ChatOpenAI(
        model=MINDMAP_MODEL,
        openai_api_key=OPENROUTER_API_KEY,
        openai_api_base=OPENROUTER_BASE,
        temperature=temperature,
        max_retries=2,
        timeout=90,
    )


# ------------------------------------------------------------------ web tools

@tool
def web_search(query: str) -> str:
    """Search the web for information not covered by the course transcripts. Returns top results as text."""
    results = web.web_search(query)
    if not results:
        return "No results."
    return "\n".join(f"- {r['title']} ({r['url']}): {r['snippet']}" for r in results)


@tool
def fetch_url(url: str) -> str:
    """Fetch the readable text of a web page by URL. Use after web_search to read a promising result."""
    return web.fetch_url(url)


_TOOLS = [web_search, fetch_url]
_TOOL_MAP = {"web_search": web_search, "fetch_url": fetch_url}


# ------------------------------------------------------------------ JSON parse

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _extract_json(text: str):
    """Tolerant: strip code fences, else grab the first balanced {...} or [...]."""
    if not text:
        return None
    m = _FENCE_RE.search(text)
    if m:
        text = m.group(1)
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except Exception:  # noqa: BLE001
                continue
    return None


# ------------------------------------------------------------------ core call

def _complete(system: str, user: str, use_tools: bool = False) -> tuple[str, list[str]]:
    """Run one completion. Returns (final_text, urls_fetched_during_tool_use)."""
    messages = [SystemMessage(content=system), HumanMessage(content=user)]
    fetched_urls: list[str] = []

    if use_tools:
        try:
            bound = _model().bind_tools(_TOOLS)
            for _ in range(_MAX_TOOL_HOPS):
                resp: AIMessage = bound.invoke(messages)
                messages.append(resp)
                calls = getattr(resp, "tool_calls", None) or []
                if not calls:
                    return (resp.content or "", fetched_urls)
                for call in calls:
                    fn = _TOOL_MAP.get(call["name"])
                    args = call.get("args", {}) or {}
                    if call["name"] == "fetch_url" and args.get("url"):
                        fetched_urls.append(args["url"])
                    result = fn.invoke(args) if fn else "Unknown tool."
                    messages.append(ToolMessage(content=str(result), tool_call_id=call["id"]))
            # Ran out of hops: ask for the final answer with no more tools.
            final = _model().invoke(messages + [HumanMessage(content="Now give the final JSON answer only.")])
            return (final.content or "", fetched_urls)
        except Exception:  # noqa: BLE001
            # Model/provider can't do tool calls -> fall through to plain call.
            pass

    resp = _model().invoke([SystemMessage(content=system), HumanMessage(content=user)])
    return (resp.content or "", fetched_urls)


# ------------------------------------------------------------------ public ops

def summarize_lesson(title: str, transcript_text: str) -> dict:
    """MAP step: short summary + key concepts for one lesson. Never uses tools."""
    system = (
        "You condense a single course lesson transcript. Respond with ONLY JSON: "
        '{"summary": "<3-5 sentence summary>", "concepts": ["<key term>", ...]}. '
        "List 3-8 concrete concepts/terms actually taught."
    )
    user = f"Lesson: {title}\n\nTranscript:\n{(transcript_text or '')[:16000]}"
    text, _ = _complete(system, user, use_tools=False)
    data = _extract_json(text) or {}
    return {
        "summary": (data.get("summary") or "").strip(),
        "concepts": [c for c in (data.get("concepts") or []) if isinstance(c, str)],
    }


def generate_seed(course_title: str, syllabus: list, lesson_summaries: list[dict]) -> dict:
    """REDUCE step: root concept + 5-9 top-level sections from the lesson summaries."""
    syl_text = json.dumps(syllabus)[:4000]
    lines = []
    for ls in lesson_summaries:
        concepts = ", ".join(ls.get("concepts") or [])
        lines.append(f"- {ls['title']}: {ls.get('summary', '')} [concepts: {concepts}]")
    summaries_text = "\n".join(lines)[:12000]
    system = (
        "You design the seed of an explorable mind map for a course. Respond with ONLY JSON: "
        '{"root": {"title": "<course core idea>", "summary": "<2-3 sentence overview>"}, '
        '"children": [{"title": "<section/subsection>", "summary": "<1-2 sentence why it matters>"}]}. '
        "Give 5-9 children ordered as a learning path from fundamentals to advanced. "
        "Titles are short concept names, not lesson numbers."
    )
    user = f"Course: {course_title}\n\nSyllabus: {syl_text}\n\nLesson summaries:\n{summaries_text}"
    text, _ = _complete(system, user, use_tools=False)
    data = _extract_json(text) or {}
    root = data.get("root") or {"title": course_title, "summary": ""}
    children = [c for c in (data.get("children") or []) if isinstance(c, dict) and c.get("title")]
    return {"root": root, "children": children[:MAX_CONCEPTS]}


def generate_leaves(concept_title: str, concept_summary: str, context_text: str) -> list[dict]:
    """Build a concept's TERMINAL leaf nodes (the bottom of the tree). May use web tools.

    Leaves end the branch: each summary is a thorough, self-contained explanation; the model
    must NOT propose further sub-branches. Called once per concept at seed/build time."""
    system = (
        "You write the TERMINAL leaves for one concept of a course mind map. Each leaf is where "
        "the concept ends -- a self-contained idea the learner can fully grasp from its explanation. "
        "Ground answers in the provided course context; if it is insufficient, you MAY call "
        "web_search/fetch_url to enrich. Respond with ONLY JSON: "
        '{"children": [{"title": "<leaf concept>", "summary": "<thorough 3-6 sentence explanation, '
        'markdown ok>", "source": "transcript|web|llm"}]}. Give 3-6 leaves. Do NOT propose further '
        "sub-branches or questions; these are final. Do not repeat the parent concept verbatim."
    )
    user = f"Concept: {concept_title}\nConcept summary: {concept_summary}\n\nCourse context:\n{context_text[:12000]}"
    text, urls = _complete(system, user, use_tools=True)
    data = _extract_json(text) or {}
    out = []
    for c in (data.get("children") or []):
        if isinstance(c, dict) and c.get("title"):
            out.append({
                "title": c["title"].strip(),
                "summary": (c.get("summary") or "").strip(),
                "source": c.get("source") if c.get("source") in ("transcript", "web", "llm") else "llm",
                "sources": [{"type": "web", "url": u} for u in urls],
            })
    return out[:MAX_LEAVES]


def answer_question(node_title: str, question: str, context_text: str) -> dict:
    """Answer a user question as a Q&A branch node. May use web tools for gaps."""
    system = (
        "You answer a learner's question about a concept, to attach as a branch in their mind map. "
        "Ground the answer in the provided course context; if insufficient, you MAY call web_search/fetch_url. "
        "Respond with ONLY JSON: "
        '{"title": "<short label for the question>", "summary": "<clear markdown answer>", '
        '"source": "transcript|web|llm"}.'
    )
    user = (
        f"Concept node: {node_title}\n\nQuestion: {question}\n\nCourse context:\n{context_text[:12000]}"
    )
    text, urls = _complete(system, user, use_tools=True)
    data = _extract_json(text) or {}
    return {
        "title": (data.get("title") or question[:80]).strip(),
        "summary": (data.get("summary") or "").strip(),
        "source": data.get("source") if data.get("source") in ("transcript", "web", "llm") else "llm",
        "sources": [{"type": "web", "url": u} for u in urls],
    }
