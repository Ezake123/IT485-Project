import os
import re
import threading
import time as clock
from typing import Dict, List, Tuple
from models import CatalogManager, Section, course_id, spaced

#--------------------------------------------------------------
# Description:
# Loads the course catalog and the planning term's sections from Supabase
# into memory, and keeps them for CACHE_SECONDS so each page load doesn't
# hit the database. Replaces course_matcher.py.
#
# Environment variables (Render > Environment, or a local .env file):
#   SUPABASE_URL  - project URL
#   SUPABASE_KEY  - the PUBLISHABLE key (the website only reads)
#   PLAN_TERM     - term to plan for, e.g. "Fall 2026"
#--------------------------------------------------------------

CACHE_SECONDS = 10 * 60
PAGE_SIZE = 1000          # Supabase returns at most 1000 rows per request

_client = None
_cache: Dict[str, Tuple[float, CatalogManager]] = {}
_lock = threading.Lock()


class CatalogUnavailable(Exception):
    """The database isn't configured. The message is shown on the page."""


def plan_term() -> str:
    return os.environ.get("PLAN_TERM") or "Fall 2026"


def get_client():
    global _client
    if _client is None:
        url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")
        if not url or not key:
            raise CatalogUnavailable("The course database isn't connected yet.")
        from supabase import create_client
        _client = create_client(url, key)
    return _client


def fetch_all(make_query) -> List[dict]:
    rows, start = [], 0
    while True:
        batch = make_query().range(start, start + PAGE_SIZE - 1).execute().data
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            return rows
        start += PAGE_SIZE


def load_catalog(term: str = None) -> CatalogManager:
    term = term or plan_term()
    with _lock:
        cached = _cache.get(term)
        if cached and clock.time() - cached[0] < CACHE_SECONDS:
            return cached[1]
        db = get_client()
        courses = fetch_all(lambda: db.table("courses").select("*").order("id"))
        sections = fetch_all(lambda: db.table("course_sections").select("*").eq("term", term).order("class_code"))
        catalog = CatalogManager.load_from_supabase_data(courses, sections)
        _cache[term] = (clock.time(), catalog)
        print(f"[catalog] {term}: {len(catalog.courses)} courses, {sum(map(len, catalog.sections.values()))} sections")
        return catalog


#--------------------------------------------------------------
# JSON for the website
#--------------------------------------------------------------

DAY_LETTER = {"Mo": "M", "Tu": "T", "We": "W", "Th": "R", "Fr": "F", "Sa": "S", "Su": "U"}


def section_json(sec: Section) -> dict:
    return {
        "class_code": sec.class_code,
        "section": sec.section,
        "days": "".join(DAY_LETTER.get(d, "") for d in (sec.days or [])),
        "start": sec.start_time.strftime("%H:%M") if sec.start_time else None,
        "end": sec.end_time.strftime("%H:%M") if sec.end_time else None,
        "location": sec.location or "",
        "instructors": [i for i in (sec.instructors or []) if i and i != "TBA"],
        "enrolled": sec.enrolled,
        "capacity": sec.maximum_capacity,
        "online": sec.is_online,
        "discussion": sec.is_discussion,
    }


def course_json(catalog: CatalogManager, cid: str) -> dict:
    course = catalog.get_course(cid)
    return {
        "credits": catalog.credits_for(cid) or None,
        "description": (course.description or "") if course else "",
        "gen_ed": course.gen_ed if course else None,
        "prereqs": [[spaced(c) for c in g] for g in course.prerequisites_cnf] if course else [],
        "prereq_text": course.prereq_text if course else "",
        "min_credits": (course.prerequisites or {}).get("min_credits") if course else None,
        "sections": [section_json(s) for s in catalog.get_sections(cid)],
    }


RANGE_RE = re.compile(r"^([A-Z]{2,8})(\d{1,3})-(\d{1,3})$")


def lookup_courses(codes: List[str], catalog: CatalogManager) -> Tuple[dict, dict]:
    """
    codes like ["CS310", "CHEM 116", "BIOL304-395"].
    Returns (courses, ranges): details for every course found, and each range
    expanded into the courses in it that have sections this term.
    """
    courses, ranges = {}, {}
    for code in codes:
        cid = course_id(code)
        match = RANGE_RE.match(cid)
        if match:
            members = catalog.offered_in_range(match.group(1), int(match.group(2)), int(match.group(3)))
            ranges[f"{match.group(1)} {match.group(2)}-{match.group(3)}"] = [spaced(m) for m in members]
            for m in members:
                courses[spaced(m)] = course_json(catalog, m)
        elif catalog.get_course(cid) or catalog.get_sections(cid):
            courses[spaced(cid)] = course_json(catalog, cid)
    return courses, ranges
