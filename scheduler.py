from __future__ import annotations
from datetime import time
from typing import List, Optional, Set
from itertools import product
from models import Section, RequirementGroup, CourseUnit, CatalogManager, SchedulePreferences
import re

#-----------------------------------------------------------------
# Description:
# The script to calculate and generate the most optimized schedule
#
# Developed with assistance from Google Gemini using agentic workflows 
#-----------------------------------------------------------------

#-----------------------------------------------------------------
# Checks if the section is a discussion (ends with 'D', e.g., '01D')
#-----------------------------------------------------------------
def is_discussion_section(section) -> bool:
    sec_num = str(getattr(section, "section", "")).strip().upper()
    return sec_num.endswith("D")

#-----------------------------------------------------------------
# Retrieves sections from the course, bundling lecture + discussion
#-----------------------------------------------------------------
def get_course_section_bundles(
    course_id: str,
    catalog: CatalogManager,
    current_schedule: List[Section],
    preferences: Optional[SchedulePreferences]
) -> List[List[Section]]:
    target_comp_filter = None
    clean_id = course_id.strip()
    if ":DISC_ONLY" in clean_id:
        clean_id = clean_id.replace(":DISC_ONLY", "").strip()
        target_comp_filter = "DISC"
    elif ":LEC_ONLY" in clean_id:
        clean_id = clean_id.replace(":LEC_ONLY", "").strip()
        target_comp_filter = "LEC"

    raw_sections = catalog.get_sections(clean_id)
    if not raw_sections:
        match = re.match(r"^([A-Za-z]+)\s*(\d+[A-Za-z]?)$", clean_id)
        if match:
            alt_id = f"{match.group(1).upper()} {match.group(2).upper()}"
            raw_sections = catalog.get_sections(alt_id)
    if not raw_sections:
        return []

    eligible = [
        s for s in raw_sections
        if section_meets_preferences(s, preferences)
        and not section_conflicts_with_schedule(s, current_schedule)
    ]
    if not eligible:
        return []

    all_has_discussions = any(is_discussion_section(s) for s in raw_sections)

    if all_has_discussions:
        lectures = [s for s in eligible if not is_discussion_section(s)]
        discussions = [s for s in eligible if is_discussion_section(s)]

        if target_comp_filter == "DISC":
            return [[disc] for disc in discussions]
        if target_comp_filter == "LEC":
            return [[lec] for lec in lectures]

        c_clean = clean_id.replace(" ", "").upper()
        sched_this_course = [
            s for s in current_schedule 
            if f"{s.course_name}{s.course_number}".replace(" ", "").upper() == c_clean
        ]
        has_sched_lec = any(not is_discussion_section(s) for s in sched_this_course)
        has_sched_disc = any(is_discussion_section(s) for s in sched_this_course)

        if has_sched_lec and not has_sched_disc:
            return [[disc] for disc in discussions]
        if has_sched_disc and not has_sched_lec:
            return [[lec] for lec in lectures]

        if not lectures or not discussions:
            return []

        paired_bundles = []
        for lec in lectures:
            for disc in discussions:
                if not intervals_collide(lec.days, lec.start_time, lec.end_time,
                                         disc.days, disc.start_time, disc.end_time):
                    paired_bundles.append([lec, disc])
        return paired_bundles

    return [[s] for s in eligible]

#-----------------------------------------------------------------
# Converts time value into minutes
#-----------------------------------------------------------------
def time_to_minutes(t) -> int:
    if t is None:
        return 0
    if isinstance(t, time):
        return t.hour * 60 + t.minute
    if isinstance(t, str):
        parts = t.strip().split(":")
        if len(parts) >= 2:
            return int(parts[0]) * 60 + int(parts[1])
    return 0

#-----------------------------------------------------------------
# Verifies if the section satisfies user restrictions
#-----------------------------------------------------------------
def section_meets_preferences(sec: Section, prefs: Optional[SchedulePreferences]) -> bool:
    if not prefs:
        return True

    # 1. Delivery Mode & Exclude Online Filter
    if getattr(prefs, "exclude_online", False) or prefs.delivery_mode == "In-person":
        if sec.is_online:
            return False
    elif prefs.delivery_mode == "Online" and not sec.is_online:
        return False

    # 2. Asynchronous Online: Meeting days/times do not apply
    if sec.is_online and (sec.start_time is None or not sec.days):
        # Still respect capacity filter if not ignored
        if not getattr(prefs, "ignore_capacity", False):
            if getattr(sec, "status", "Open").lower() in ["closed", "waitlist", "waitlisted"]:
                return False
        return True

    # 3. Synchronous Online / In-Person: Must have start, end, and days
    if sec.start_time is None or sec.end_time is None or not sec.days:
        return False

    # 4. Capacity/Status Check
    if not getattr(prefs, "ignore_capacity", False):
        if getattr(sec, "status", "Open").lower() in ["closed", "waitlist", "waitlisted"]:
            return False

    # 5. Allowed Days Filter (Online asynchronous is already bypassed above)
    if prefs.allowed_days is not None:
        allowed_set = {d.strip() for d in prefs.allowed_days}
        if not set(sec.days).issubset(allowed_set):
            return False

    # 6. Time Limits
    if prefs.earliest_start is not None:
        if time_to_minutes(sec.start_time) < time_to_minutes(prefs.earliest_start):
            return False

    if prefs.latest_end is not None:
        if time_to_minutes(sec.end_time) > time_to_minutes(prefs.latest_end):
            return False

    return True

#-----------------------------------------------------------------
# Collision Checks
#-----------------------------------------------------------------
def intervals_collide(
    days_a: Optional[List[str]], start_a: Optional[time], end_a: Optional[time],
    days_b: Optional[List[str]], start_b: Optional[time], end_b: Optional[time]
) -> bool:
    if not days_a or not days_b or not start_a or not end_a or not start_b or not end_b:
        return False
    if not set(days_a).intersection(set(days_b)):
        return False
    s_a, e_a = time_to_minutes(start_a), time_to_minutes(end_a)
    s_b, e_b = time_to_minutes(start_b), time_to_minutes(end_b)
    return max(s_a, s_b) < min(e_a, e_b)

def section_conflicts_with_schedule(sec: Section, schedule: List[Section]) -> bool:
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
    if unit.unit_type == "OR":
        valid_options = []
        for cid in unit.courses:
            bundles = get_course_section_bundles(cid, catalog, current_schedule, preferences)
            valid_options.extend(bundles)
        return valid_options

    if unit.unit_type == "AND":
        bundles_per_course = []
        for cid in unit.courses:
            bundles = get_course_section_bundles(cid, catalog, current_schedule, preferences)
            if not bundles:
                return []
            bundles_per_course.append(bundles)

        valid_combos = []
        for combo in product(*bundles_per_course):
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

    single_id = unit.courses[0]
    return get_course_section_bundles(single_id, catalog, current_schedule, preferences)

#-----------------------------------------------------------------
# Solver: Maximizes course count across Requirement Groups
#-----------------------------------------------------------------
def solve_schedule(
    requirements: List[RequirementGroup],
    catalog: CatalogManager,
    target_count: int = 5,
    preferences: Optional[SchedulePreferences] = None,
    fixed_sections: Optional[List[Section]] = None
) -> Optional[List[Section]]:
    # Pre-seed with user-locked sections (prioritized first)
    seed_schedule: List[Section] = list(fixed_sections) if fixed_sections else []
    seed_enrolled: Set[str] = set()

    for s in seed_schedule:
        c_tag = f"{s.course_name.strip()}{s.course_number.strip()}".upper()
        cat_secs = catalog.get_sections(c_tag) or catalog.get_sections(f"{s.course_name.strip()} {s.course_number.strip()}")
        if any(is_discussion_section(sec) for sec in cat_secs):
            comp_tag = f"{c_tag}_DISC" if is_discussion_section(s) else f"{c_tag}_LEC"
            seed_enrolled.add(comp_tag)
        else:
            seed_enrolled.add(c_tag)

    distinct_seed = {re.sub(r'_(LEC|DISC)$', '', tag) for tag in seed_enrolled}
    if not requirements or len(distinct_seed) >= target_count:
        return seed_schedule if seed_schedule else None

    # Calculate how many distinct courses are realistically possible
    possible_pool_courses = set()
    for req in requirements:
        for u in req.units:
            for cid in u.courses:
                clean_cid = re.sub(r':(DISC_ONLY|LEC_ONLY)$', '', cid).replace(" ", "").upper()
                possible_pool_courses.add(clean_cid)

    total_possible_distinct = len(distinct_seed.union(possible_pool_courses))
    effective_target = min(target_count, total_possible_distinct)

    best_schedule: List[Section] = list(seed_schedule)
    best_course_count = len(distinct_seed)

    MAX_SEARCH_ITERATIONS = 120000
    iterations = 0

    def backtrack(
        req_idx: int,
        cand_idx: int,
        remaining_needed: int,
        active_sched: List[Section],
        active_enrolled: Set[str]
    ) -> Optional[List[Section]]:
        nonlocal best_schedule, best_course_count, iterations
        iterations += 1

        if iterations > MAX_SEARCH_ITERATIONS:
            return None

        # Distinct course count
        distinct_courses = {re.sub(r'_(LEC|DISC)$', '', tag) for tag in active_enrolled}
        current_distinct_count = len(distinct_courses)

        # Track best-found schedule across all branches
        if current_distinct_count > best_course_count:
            best_course_count = current_distinct_count
            best_schedule = list(active_sched)

        # Early exit only when exact target course count is achieved
        if current_distinct_count >= effective_target:
            return list(active_sched)

        # Reached the end of requirements: return None so parent frames continue searching
        if req_idx >= len(requirements):
            return None

        current_req = requirements[req_idx]
        candidates = current_req.units

        # If this group's quota is satisfied, advance to the next requirement group
        if remaining_needed <= 0:
            next_req_idx = req_idx + 1
            if next_req_idx >= len(requirements):
                return None
            next_needed = requirements[next_req_idx].courses_needed
            return backtrack(next_req_idx, 0, next_needed, active_sched, active_enrolled)

        # If candidates in this group are exhausted, advance to the next requirement group
        if cand_idx >= len(candidates):
            next_req_idx = req_idx + 1
            if next_req_idx >= len(requirements):
                return None
            next_needed = requirements[next_req_idx].courses_needed
            return backtrack(next_req_idx, 0, next_needed, active_sched, active_enrolled)

        unit = candidates[cand_idx]
        unit_courses = [c.upper() for c in unit.courses]

        sample_raw = unit_courses[0] if unit_courses else ""
        is_disc_req = ":DISC_ONLY" in sample_raw
        is_lec_req = ":LEC_ONLY" in sample_raw
        sample_id = re.sub(r':(DISC_ONLY|LEC_ONLY)$', '', sample_raw).replace(" ", "").upper()

        cat_secs = catalog.get_sections(sample_id) or catalog.get_sections(re.sub(r'([A-Z]+)(\d+)', r'\1 \2', sample_id))
        has_disc = any(is_discussion_section(s) for s in cat_secs)

        # Check if course is already satisfied in the schedule
        already_taken = False
        if has_disc:
            lec_tag = f"{sample_id}_LEC"
            disc_tag = f"{sample_id}_DISC"
            if is_disc_req:
                already_taken = disc_tag in active_enrolled
            elif is_lec_req:
                already_taken = lec_tag in active_enrolled
            else:
                already_taken = (lec_tag in active_enrolled and disc_tag in active_enrolled) or (sample_id in distinct_courses)
        else:
            already_taken = sample_id in distinct_courses

        if already_taken:
            return backtrack(req_idx, cand_idx + 1, remaining_needed, active_sched, active_enrolled)

        # Try selecting sections for this candidate course
        section_options = get_compatible_unit_options(unit, catalog, active_sched, preferences)

        for sec_group in section_options:
            added_tags = []
            for added_sec in sec_group:
                c_clean = f"{added_sec.course_name.strip()}{added_sec.course_number.strip()}".upper()
                if has_disc:
                    sub_tag = f"{c_clean}_DISC" if is_discussion_section(added_sec) else f"{c_clean}_LEC"
                    added_tags.append(sub_tag)
                else:
                    added_tags.append(c_clean)

            active_sched.extend(sec_group)
            active_enrolled.update(added_tags)

            # Recurse: quota for this group decrements by 1, try next candidate course
            result = backtrack(
                req_idx=req_idx,
                cand_idx=cand_idx + 1,
                remaining_needed=remaining_needed - 1,
                active_sched=active_sched,
                active_enrolled=active_enrolled
            )
            if result is not None:
                return result

            # Backtrack
            del active_sched[-len(sec_group):]
            active_enrolled.difference_update(added_tags)

        # Skip this course and try the next candidate in this group
        return backtrack(
            req_idx=req_idx,
            cand_idx=cand_idx + 1,
            remaining_needed=remaining_needed,
            active_sched=active_sched,
            active_enrolled=active_enrolled
        )

    initial_needed = requirements[0].courses_needed
    exact_match = backtrack(0, 0, initial_needed, seed_schedule, seed_enrolled)

    if exact_match is not None:
        return exact_match
    # If target could not be hit exactly, return the best non-conflicting combination found
    return best_schedule if len(best_schedule) > len(seed_schedule) or best_schedule else None