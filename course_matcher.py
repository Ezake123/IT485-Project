import os
import re
from supabase import create_client

#-------------------------
# Looks up courses and their sections in Supabase
#
# Used by the website's Courses and Schedule pages. Given course codes
# like ["CS310", "CHEM 116"], it returns each course's credits,
# description, prerequisites, and sections for the term being planned.
#
# Needs these environment variables (set them in Render > Environment):
#   SUPABASE_URL  - your project URL
#   SUPABASE_KEY  - the PUBLISHABLE key (this only reads data)
#-------------------------

_client = None


def get_client():
    """Creates the Supabase client the first time it's needed."""
    global _client
    if _client is None:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_KEY are not set")
        _client = create_client(url, key)
    return _client


CODE_RE = re.compile(r"^([A-Z]{2,8})\s*-?\s*(\d{1,3}[A-Z]?)$")


def split_code(code):
    """'CS310' or 'CS 310' -> ('CS', '310'). Returns None if it doesn't look like a course code."""
    match = CODE_RE.match(str(code).strip().upper())
    return (match.group(1), match.group(2)) if match else None


# Database stores days like ["Mo", "We"]; the website uses single letters (Thursday = R)
DAY_MAP = {"Mo": "M", "Tu": "T", "We": "W", "Th": "R", "Fr": "F", "Sa": "S", "Su": "U"}


def prereq_groups(prereqs):
    """
    Converts the prerequisites JSON into a list of groups, e.g.
        {"courses": [[{"course_name": "CS", "course_number": "210"}],
                     [{"course_name": "MATH", "course_number": "140"},
                      {"course_name": "MATH", "course_number": "145"}]]}
    becomes
        [["CS 210"], ["MATH 140", "MATH 145"]]

    Every group is required; within a group, any ONE course is enough.
    Also returns the original prerequisite text so the site can show it.
    """
    if not isinstance(prereqs, dict):
        return [], ""

    groups = []
    for group in prereqs.get("courses") or []:
        options = [
            f"{c['course_name']} {c['course_number']}"
            for c in group
            if isinstance(c, dict) and c.get("course_name") and c.get("course_number")
        ]
        if options:
            groups.append(options)

    raw = (prereqs.get("raw") or "").strip()
    if raw.lower() == "none":
        raw = ""
    return groups, raw


def short_time(value):
    """'16:00:00' -> '16:00'"""
    return value[:5] if value else None


def lookup_courses(codes, term=None):
    pairs = {split_code(c) for c in codes}
    pairs.discard(None)
    if not pairs:
        return {}

    subjects = sorted({subject for subject, _ in pairs})
    numbers = sorted({number for _, number in pairs})
    db = get_client()

    # Filtering by subject and number separately can return extra combinations
    # (e.g. CS 140 when asking for CS 110 and MATH 140); those are skipped below.
    courses = (
        db.table("courses")
        .select("course_name,course_number,credits,description,gen_ed,prerequisites")
        .in_("course_name", subjects)
        .in_("course_number", numbers)
        .execute()
        .data
    )

    query = (
        db.table("course_sections")
        .select("class_code,course_name,course_number,section,term,days,start_time,end_time,"
                "location,maximum_capacity,enrolled,instructors")
        .in_("course_name", subjects)
        .in_("course_number", numbers)
    )
    if term:
        query = query.eq("term", term)
    sections = query.execute().data

    result = {}
    for c in courses:
        key = (c["course_name"], c["course_number"])
        if key not in pairs:
            continue
        groups, raw = prereq_groups(c.get("prerequisites"))
        result[f"{key[0]} {key[1]}"] = {
            "credits": c.get("credits"),
            "description": c.get("description") or "",
            "gen_ed": c.get("gen_ed"),
            "prereqs": groups,
            "prereq_text": raw,
            "sections": [],
        }

    for s in sections:
        code = f"{s['course_name']} {s['course_number']}"
        if code not in result:
            continue
        result[code]["sections"].append({
            "class_code": s.get("class_code"),
            "section": s.get("section") or "",
            "term": s.get("term"),
            "days": "".join(DAY_MAP.get(d, "") for d in (s.get("days") or [])),
            "start": short_time(s.get("start_time")),
            "end": short_time(s.get("end_time")),
            "location": s.get("location") or "",
            "instructors": s.get("instructors") or [],
            "enrolled": s.get("enrolled"),
            "capacity": s.get("maximum_capacity"),
        })

    for course in result.values():
        course["sections"].sort(key=lambda sec: sec["section"])

    return result
