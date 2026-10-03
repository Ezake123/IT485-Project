from __future__ import annotations
from datetime import time
from itertools import product
from typing import Dict, List, Optional, Set, Tuple
from models import Section, RequirementGroup, CourseUnit, CatalogManager, SchedulePreferences

#--------------------------------------------------------------
# Description:
# Builds a conflict-free schedule from requirement groups.
# Works through the requirements in order, trying each option's sections
# (lecture + discussion pairs are kept together) and backtracking when
# something doesn't fit. Stops at the student's course/credit limits.
#--------------------------------------------------------------

SEARCH_LIMIT = 50000   # max search steps, so big requirement lists can't hang the server


def time_to_minutes(t) -> int:
    if t is None:
        return 0
    if isinstance(t, time):
        return t.hour * 60 + t.minute
    parts = str(t).strip().split(":")
    return int(parts[0]) * 60 + int(parts[1]) if len(parts) >= 2 else 0


def intervals_collide(a: Section, b: Section) -> bool:
    """Two sections overlap if they share a day and their times overlap. TBA/online never conflicts."""
    if not a.has_meeting_time or not b.has_meeting_time:
        return False
    if not set(a.days).intersection(b.days):
        return False
    return max(time_to_minutes(a.start_time), time_to_minutes(b.start_time)) < \
        min(time_to_minutes(a.end_time), time_to_minutes(b.end_time))


def conflicts_with(sec: Section, schedule: List[Section]) -> bool:
    return any(intervals_collide(sec, other) for other in schedule)


def section_meets_preferences(sec: Section, prefs: Optional[SchedulePreferences]) -> bool:
    if not prefs:
        return True
    if prefs.open_seats_only and sec.is_full:
        return False
    if prefs.delivery_mode == "In-person" and sec.is_online:
        return False
    if prefs.delivery_mode == "Online" and not sec.is_online:
        return False

    # Day and time limits only apply to sections that have meeting times
    if sec.has_meeting_time:
        if prefs.allowed_days is not None and not set(sec.days).issubset(set(prefs.allowed_days)):
            return False
        if prefs.earliest_start and time_to_minutes(sec.start_time) < time_to_minutes(prefs.earliest_start):
            return False
        if prefs.latest_end and time_to_minutes(sec.end_time) > time_to_minutes(prefs.latest_end):
            return False
    return True


def course_bundles(cid: str, catalog: CatalogManager, schedule: List[Section],
                   prefs: Optional[SchedulePreferences], pins: Dict[str, str]) -> List[List[Section]]:
    """
    Section choices for one course. If the course has discussion sections ('01D'),
    each choice is a lecture + discussion pair; otherwise a single section.
    """
    raw = catalog.get_sections(cid)
    if not raw:
        return []

    pinned = next((s for s in raw if s.class_code == pins.get(cid)), None)
    if pinned:
        # Keep the chosen section; if the course has discussions, still offer the
        # other half of the pair (discussions for a pinned lecture, or vice versa)
        eligible = [pinned] + [s for s in raw if s.is_discussion != pinned.is_discussion
                               and section_meets_preferences(s, prefs)]
    else:
        eligible = [s for s in raw if section_meets_preferences(s, prefs)]
    eligible = [s for s in eligible if not conflicts_with(s, schedule)]

    # Sections with open seats first, then earlier start times
    eligible.sort(key=lambda s: (s.is_full, not s.has_meeting_time, time_to_minutes(s.start_time)))

    if any(s.is_discussion for s in raw):
        lectures = [s for s in eligible if not s.is_discussion]
        discussions = [s for s in eligible if s.is_discussion]
        bundles = [[lec, disc] for lec in lectures for disc in discussions if not intervals_collide(lec, disc)]
    else:
        bundles = [[s] for s in eligible]
    return unique_by_time(bundles)


def unique_by_time(bundles: List[List[Section]]) -> List[List[Section]]:
    """
    Sections that meet at the same times are interchangeable for avoiding conflicts,
    so keep only the first (best) one of each. This keeps the search fast for
    courses with dozens of sections.
    """
    seen, out = set(), []
    for bundle in bundles:
        key = tuple((tuple(s.days or []), s.start_time, s.end_time, s.is_online) for s in bundle)
        if key not in seen:
            seen.add(key)
            out.append(bundle)
    return out


def unit_options(unit: CourseUnit, catalog: CatalogManager, schedule: List[Section],
                 prefs: Optional[SchedulePreferences], pins: Dict[str, str]) -> List[Tuple[List[str], List[Section]]]:
    """
    Ways to schedule one requirement option, as (courses taken, sections) pairs.
    OR -> one alternative; AND -> every course together with no internal conflicts.
    """
    if unit.unit_type == "OR":
        return [([cid], b) for cid in unit.courses for b in course_bundles(cid, catalog, schedule, prefs, pins)]

    if unit.unit_type == "AND":
        per_course = []
        for cid in unit.courses:
            bundles = course_bundles(cid, catalog, schedule, prefs, pins)
            if not bundles:
                return []
            per_course.append(bundles)
        combos = []
        for combo in product(*per_course):
            flat = [s for bundle in combo for s in bundle]
            if not any(intervals_collide(flat[i], flat[j])
                       for i in range(len(flat)) for j in range(i + 1, len(flat))):
                combos.append((list(unit.courses), flat))
            if len(combos) >= 50:
                break
        return combos

    cid = unit.courses[0]
    return [([cid], b) for b in course_bundles(cid, catalog, schedule, prefs, pins)]


class _SearchLimit(Exception):
    pass


def solve_schedule(
    requirements: List[RequirementGroup],
    catalog: CatalogManager,
    preferences: Optional[SchedulePreferences] = None,
    pins: Optional[Dict[str, str]] = None,
    completed: Optional[Set[str]] = None,
    strict_coreqs: bool = False,
) -> dict:
    """
    Returns {"sections": [...], "fills": {"CHEM115": "Intro Chemistry", ...}, "complete": bool}
    "complete" is True when every requirement's needed count was scheduled
    (or the course/credit limit was reached).

    strict_coreqs: only accept schedules where every course's co-requisites are
    completed or in the same schedule (e.g. no PHYSIC 181 lab without its lecture).
    """
    prefs = preferences or SchedulePreferences()
    pins = pins or {}
    max_courses = 99 if prefs.fit_maximum else max(1, prefs.max_courses)
    max_credits = max(1, prefs.max_credits)
    total_needed = sum(r.courses_needed for r in requirements)

    best = {"score": (-1, -1), "sections": [], "fills": {}}
    steps = 0

    def credits_of(courses: List[str]) -> int:
        return sum(catalog.credits_for(c) or 3 for c in courses)

    completed = completed or set()

    def coreqs_ok(fills) -> bool:
        if not strict_coreqs:
            return True
        for cid in fills:
            course = catalog.get_course(cid)
            for group in (course.prerequisites_cnf if course else []):
                if course.is_coreq_group(group) and not any(c in completed or c in fills for c in group):
                    return False
        return True

    def record(schedule, fills, credits):
        if not coreqs_ok(fills):
            return
        score = (len(fills), credits)
        if score > best["score"]:
            best.update(score=score, sections=list(schedule), fills=dict(fills))

    def backtrack(req_idx: int, cand_idx: int, remaining: int,
                  schedule: List[Section], fills: Dict[str, str], credits: int) -> bool:
        nonlocal steps
        steps += 1
        if steps > SEARCH_LIMIT:
            raise _SearchLimit()

        record(schedule, fills, credits)
        if (len(fills) >= max_courses or len(fills) >= total_needed) and coreqs_ok(fills):
            return True
        if req_idx >= len(requirements):
            return False

        req = requirements[req_idx]
        if remaining == 0 or cand_idx >= len(req.units):
            if req_idx + 1 >= len(requirements):
                return False
            return backtrack(req_idx + 1, 0, requirements[req_idx + 1].courses_needed, schedule, fills, credits)

        unit = req.units[cand_idx]
        if not any(c in fills for c in unit.courses):
            for taken, sections in unit_options(unit, catalog, schedule, prefs, pins):
                added = credits_of(taken)
                if credits + added > max_credits or len(fills) + len(taken) > max_courses:
                    continue
                new_fills = dict(fills, **{c: req.group_name for c in taken})
                if backtrack(req_idx, cand_idx + 1, remaining - 1, schedule + sections, new_fills, credits + added):
                    return True

        # Skip this option
        return backtrack(req_idx, cand_idx + 1, remaining, schedule, fills, credits)

    if requirements:
        try:
            backtrack(0, 0, requirements[0].courses_needed, [], {}, 0)
        except _SearchLimit:
            pass

    return {
        "sections": best["sections"],
        "fills": best["fills"],
        "credits": max(best["score"][1], 0),
        "complete": len(best["fills"]) >= min(max_courses, total_needed),
    }
