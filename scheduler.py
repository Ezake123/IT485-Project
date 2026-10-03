from __future__ import annotations
from datetime import time
from typing import List, Optional, Set
from itertools import product
from models import Section, RequirementGroup, CourseUnit, CatalogManager, SchedulePreferences
import re

def is_discussion_section(sec: Section) -> bool:
    """Checks if a section code represents a discussion/breakout section (e.g., '01D', '02D')."""
    return bool(sec.section and sec.section.strip().upper().endswith('D'))

def get_course_section_bundles(
    course_id: str,
    catalog: CatalogManager,
    current_schedule: List[Section],
    preferences: Optional[SchedulePreferences]
) -> List[List[Section]]:
    """
    Retrieves valid section combinations for a single course.
    If the course includes discussion sections (e.g. '01D'), bundles each lecture with each valid discussion.
    If no discussion sections exist, returns standard single lecture sections [[lec]].
    """
    raw_sections = catalog.get_sections(course_id)
    if not raw_sections:
        return []

    # 1. Filter sections that respect user preferences and non-NULL meeting times
    eligible = [
        s for s in raw_sections
        if section_meets_preferences(s, preferences)
        and not section_conflicts_with_schedule(s, current_schedule)
    ]
    if not eligible:
        return []

    # Check whether the catalog for this course has discussion sections
    all_has_discussions = any(is_discussion_section(s) for s in raw_sections)

    if all_has_discussions:
        lectures = [s for s in eligible if not is_discussion_section(s)]
        discussions = [s for s in eligible if is_discussion_section(s)]

        # Must have at least one lecture and one discussion available
        if not lectures or not discussions:
            return []

        paired_bundles = []
        for lec in lectures:
            for disc in discussions:
                # Ensure the lecture and discussion do not collide with each other
                if not intervals_collide(lec.days, lec.start_time, lec.end_time,
                                         disc.days, disc.start_time, disc.end_time):
                    paired_bundles.append([lec, disc])
        return paired_bundles

    # Standard course without mandatory discussion breakout sections
    return [[s] for s in eligible]

def time_to_minutes(t) -> int:
    """Converts a datetime.time object or time string ('HH:MM' or 'HH:MM:SS') to minutes from midnight."""
    if t is None:
        return 0

    # If it is already a datetime.time object
    if isinstance(t, time):
        return t.hour * 60 + t.minute

    # If it is a string from Supabase (e.g., '14:30:00' or '09:00')
    if isinstance(t, str):
        clean_time = t.strip()
        parts = clean_time.split(":")
        if len(parts) >= 2:
            return int(parts[0]) * 60 + int(parts[1])

    return 0


def section_meets_preferences(sec: Section, prefs: Optional[SchedulePreferences]) -> bool:
    """Verifies if a section respects user-selected days, time windows, and delivery mode."""
    
    # 1. Reject database NULLs / missing meeting details
    if sec.start_time is None or sec.end_time is None or not sec.days:
        return False

    if not prefs:
        return True

    # 2. Delivery Mode Check
    if prefs.delivery_mode == "In-person" and sec.is_online:
        return False
    if prefs.delivery_mode == "Online" and not sec.is_online:
        return False

    # 3. Allowed Days Filter (e.g. ['Tu', 'Th'] or ['W'])
    if prefs.allowed_days is not None:
        allowed_set = {d.strip() for d in prefs.allowed_days}
        if not set(sec.days).issubset(allowed_set):
            return False

    # 4. Earliest Start Time Limit
    if prefs.earliest_start is not None:
        if time_to_minutes(sec.start_time) < time_to_minutes(prefs.earliest_start):
            return False

    # 5. Latest End Time Limit
    if prefs.latest_end is not None:
        if time_to_minutes(sec.end_time) > time_to_minutes(prefs.latest_end):
            return False

    return True


def intervals_collide(
    days_a: Optional[List[str]], start_a: Optional[time], end_a: Optional[time],
    days_b: Optional[List[str]], start_b: Optional[time], end_b: Optional[time]
) -> bool:
    """Checks whether two day/time slots overlap."""
    if not days_a or not days_b or not start_a or not end_a or not start_b or not end_b:
        return False

    if not set(days_a).intersection(set(days_b)):
        return False

    s_a = time_to_minutes(start_a)
    e_a = time_to_minutes(end_a)
    s_b = time_to_minutes(start_b)
    e_b = time_to_minutes(end_b)

    return max(s_a, s_b) < min(e_a, e_b)


def section_conflicts_with_schedule(sec: Section, schedule: List[Section]) -> bool:
    """Checks if a section collides with any already locked-in section in the schedule."""
    for existing in schedule:
        if intervals_collide(sec.days, sec.start_time, sec.end_time,
                             existing.days, existing.start_time, existing.end_time):
            return True
    return False

def get_compatible_unit_options(
    unit: CourseUnit,
    catalog: CatalogManager,
    current_schedule: List[Section],
    preferences: Optional[SchedulePreferences] = None
) -> List[List[Section]]:
    """
    Retrieves viable section combinations for a CourseUnit.
    Handles single courses, AND (co-requisites), and OR (alternatives),
    automatically bundling lecture + discussion ('D') pairs.
    """
    # Case A: Alternative courses (OR) - return bundles for each valid alternative
    if unit.unit_type == "OR":
        valid_options = []
        for cid in unit.courses:
            bundles = get_course_section_bundles(cid, catalog, current_schedule, preferences)
            valid_options.extend(bundles)
        return valid_options

    # Case B: Co-requisites (AND) - must satisfy all together without internal conflict
    if unit.unit_type == "AND":
        bundles_per_course = []
        for cid in unit.courses:
            bundles = get_course_section_bundles(cid, catalog, current_schedule, preferences)
            if not bundles:
                return []
            bundles_per_course.append(bundles)

        valid_combos = []
        for combo in product(*bundles_per_course):
            # Flatten list of lists: combo is tuple of lists, e.g. ([lec1, disc1], [chem117_lab])
            flat_secs = [sec for sublist in combo for sec in sublist]
            
            has_internal_conflict = False
            for i in range(len(flat_secs)):
                for j in range(i + 1, len(flat_secs)):
                    if intervals_collide(flat_secs[i].days, flat_secs[i].start_time, flat_secs[i].end_time,
                                         flat_secs[j].days, flat_secs[j].start_time, flat_secs[j].end_time):
                        has_internal_conflict = True
                        break
                if has_internal_conflict:
                    break
            if not has_internal_conflict:
                valid_combos.append(flat_secs)
        return valid_combos

    # Case C: Standard single course
    single_id = unit.courses[0]
    return get_course_section_bundles(single_id, catalog, current_schedule, preferences)

def solve_schedule(
    requirements: List[RequirementGroup],
    catalog: CatalogManager,
    target_count: int = 5,
    preferences: Optional[SchedulePreferences] = None
) -> Optional[List[Section]]:
    if not requirements:
        return None

    # If fit_maximum is selected, set target ceiling to total requirements available
    if preferences and preferences.fit_maximum:
        effective_target = sum(r.courses_needed for r in requirements)
    else:
        effective_target = target_count

    best_schedule: List[Section] = []
    best_course_count = 0

    def backtrack(
        req_idx: int,
        cand_idx: int,
        remaining_needed_in_req: int,
        active_sched: List[Section],
        active_enrolled: Set[str]
    ) -> Optional[List[Section]]:
        nonlocal best_schedule, best_course_count

        current_count = len(active_enrolled)

        if current_count > best_course_count:
            best_course_count = current_count
            best_schedule = list(active_sched)

        if current_count >= effective_target:
            return active_sched

        if req_idx >= len(requirements):
            return None

        current_req = requirements[req_idx]
        candidates = current_req.units

        if remaining_needed_in_req == 0 or cand_idx >= len(candidates):
            next_req_idx = req_idx + 1
            if next_req_idx >= len(requirements):
                return None
            next_needed = requirements[next_req_idx].courses_needed
            return backtrack(next_req_idx, 0, next_needed, active_sched, active_enrolled)

        unit = candidates[cand_idx]
        unit_courses = unit.courses

        if any(c in active_enrolled for c in unit_courses):
            return backtrack(req_idx, cand_idx + 1, remaining_needed_in_req, active_sched, active_enrolled)

        section_options = get_compatible_unit_options(unit, catalog, active_sched, preferences)

        for sec_group in section_options:
            new_sched = list(active_sched) + sec_group
            new_enrolled = set(active_enrolled).union(unit_courses)

            result = backtrack(
                req_idx=req_idx,
                cand_idx=cand_idx + 1,
                remaining_needed_in_req=remaining_needed_in_req - 1,
                active_sched=new_sched,
                active_enrolled=new_enrolled
            )
            if result is not None:
                return result

        return backtrack(
            req_idx=req_idx,
            cand_idx=cand_idx + 1,
            remaining_needed_in_req=remaining_needed_in_req,
            active_sched=active_sched,
            active_enrolled=active_enrolled
        )

    initial_needed = requirements[0].courses_needed
    exact_match = backtrack(0, 0, initial_needed, [], set())

    return exact_match if exact_match is not None else (best_schedule if best_schedule else None)