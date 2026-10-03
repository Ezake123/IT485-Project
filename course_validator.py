from __future__ import annotations
import math
import re
from typing import List, Set, Iterable
from models import CatalogManager, CourseUnit, RequirementGroup, course_id

#--------------------------------------------------------------
# Description:
# Turns the open requirements from the degree audit into requirement
# groups the scheduler can work with, keeping only options a student
# can actually take this term:
#   - ranges like "BIOL304-395" become every offered course in that range
#   - courses on the audit's "NOT FROM" list are dropped
#   - completed courses are dropped
#   - courses with no sections this term are dropped
#   - courses whose prerequisites aren't met are dropped
#     (co-requisites don't block: they're scheduled together)
#--------------------------------------------------------------

RANGE_RE = re.compile(r"^([A-Z]{2,8})(\d{1,3})-(\d{1,3})$")
AVERAGE_CREDITS = 3   # used to turn "earn 20 credits" into a number of courses


def expand_token(token: str, catalog: CatalogManager, exclude: Set[str]) -> List[str]:
    """One option from the audit -> tokens for real courses ('BIOL304-395' -> ['BIOL304', 'BIOL316', ...])."""
    clean = course_id(token)
    match = RANGE_RE.match(clean)
    if match:
        subject, low, high = match.group(1), int(match.group(2)), int(match.group(3))
        return [c for c in catalog.offered_in_range(subject, low, high) if c not in exclude]
    return [clean]


def is_unit_offered(unit: CourseUnit, catalog: CatalogManager) -> bool:
    has = lambda c: bool(catalog.get_course(c) and catalog.get_sections(c))
    if unit.unit_type == "AND":
        return all(has(c) for c in unit.courses)
    if unit.unit_type == "OR":
        return any(has(c) for c in unit.courses)
    return has(unit.courses[0])


def unit_prereqs_met(unit: CourseUnit, catalog: CatalogManager, completed: Set[str], earned: float) -> bool:
    met = lambda c: catalog.get_course(c).hard_prerequisites_met(completed, earned)
    offered = [c for c in unit.courses if catalog.get_course(c) and catalog.get_sections(c)]
    if unit.unit_type == "OR":
        return any(met(c) for c in offered)
    return all(met(c) for c in unit.courses)


def trim_unit(unit: CourseUnit, catalog: CatalogManager, completed: Set[str], earned: float) -> CourseUnit:
    """For 'either one' options, keep only the alternatives that are offered and unlocked."""
    if unit.unit_type != "OR":
        return unit
    keep = [c for c in unit.courses
            if catalog.get_course(c) and catalog.get_sections(c)
            and catalog.get_course(c).hard_prerequisites_met(completed, earned)]
    if len(keep) == 1:
        return CourseUnit(raw_token=keep[0], unit_type="SINGLE", courses=keep)
    return CourseUnit(raw_token="|".join(keep), unit_type="OR", courses=keep)


def build_requirement_groups(
    requirements: Iterable[dict],
    catalog: CatalogManager,
    completed: Set[str],
    earned_credits: float = 0,
) -> List[RequirementGroup]:
    """
    requirements: [{"name": "Intro Biology", "need": 2 or {"credits": 20},
                    "options": ["BIOL111", "CHEM115&CHEM117", "BIOL304-395"],
                    "exclude": ["BIOL444"]}, ...]
    """
    completed = {course_id(c) for c in completed}
    groups = []

    for req in requirements:
        exclude = {course_id(c) for c in req.get("exclude") or []}
        need = req.get("need", 1)
        if isinstance(need, dict):
            need = max(1, math.ceil(float(need.get("credits") or 0) / AVERAGE_CREDITS))
        need = int(need or 1)

        units, seen = [], set()
        for token in req.get("options") or []:
            for expanded in expand_token(str(token), catalog, exclude):
                unit = CourseUnit.from_token(expanded)
                key = (unit.unit_type, tuple(unit.courses))
                if key in seen or unit.is_completed(completed):
                    continue
                seen.add(key)
                if is_unit_offered(unit, catalog) and unit_prereqs_met(unit, catalog, completed, earned_credits):
                    units.append(trim_unit(unit, catalog, completed, earned_credits))

        if units:
            groups.append(RequirementGroup(
                group_name=req.get("name") or f"Requirement {len(groups) + 1}",
                courses_needed=min(need, len(units)),
                units=units,
            ))
    return groups


#--------------------------------------------------------------
# Explains why courses from the open requirements didn't make it
# into the schedule, so the page can say "why not more?"
#--------------------------------------------------------------

def explain_unscheduled(requirements, catalog: CatalogManager, completed: Set[str], earned: float,
                        scheduled: Set[str], schedule_sections, prefs, credits_used: int) -> List[dict]:
    from scheduler import section_meets_preferences, conflicts_with
    from models import spaced

    completed = {course_id(c) for c in completed}
    reasons = {}   # course -> (reason key, detail)

    candidates = []
    for req in requirements:
        exclude = {course_id(c) for c in req.get("exclude") or []}
        for token in req.get("options") or []:
            for expanded in expand_token(str(token), catalog, exclude):
                for cid in re.split(r"[&|]", expanded):
                    if cid and cid not in completed and cid not in scheduled and cid not in candidates:
                        candidates.append(cid)

    for cid in candidates:
        course, sections = catalog.get_course(cid), catalog.get_sections(cid)
        if not course or not sections:
            reasons[cid] = ("offered", "")
            continue
        hard = []
        min_credits = (course.prerequisites or {}).get("min_credits")
        if min_credits and earned < min_credits:
            hard.append(f"{min_credits} earned credits")
        hard += [" or ".join(spaced(c) for c in g) for g in course.prerequisites_cnf
                 if not course.is_coreq_group(g) and not any(c in completed for c in g)]
        coreq_missing = [g for g in course.prerequisites_cnf if course.is_coreq_group(g)
                         and not any(c in completed or c in scheduled for c in g)]
        if hard:
            reasons[cid] = ("prereq", hard[0])
        elif not any(section_meets_preferences(s, prefs) for s in sections):
            if prefs.open_seats_only and all(s.is_full for s in sections):
                reasons[cid] = ("full", "")
            else:
                reasons[cid] = ("prefs", "")
        elif not any(section_meets_preferences(s, prefs) and not conflicts_with(s, schedule_sections) for s in sections):
            reasons[cid] = ("conflict", "")
        elif coreq_missing:
            reasons[cid] = ("coreq", " or ".join(spaced(c) for c in coreq_missing[0]))
        else:
            reasons[cid] = ("limit", "")

    labels = {
        "prereq": "Need a prerequisite first",
        "coreq": "Must be taken at the same time as another course",
        "full": "Every section is full",
        "prefs": "No sections on your days and times",
        "conflict": "Every section conflicts with your schedule",
        "limit": "Over your course or credit limit",
        "offered": "Not offered this term",
    }
    groups = []
    for key in ["conflict", "full", "prefs", "coreq", "limit", "prereq", "offered"]:
        items = [(c, d) for c, (k, d) in reasons.items() if k == key]
        if items:
            groups.append({
                "key": key,
                "label": labels[key],
                "courses": [{"course": spaced(c), "detail": d} for c, d in items],
            })
    return groups
