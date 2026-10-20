from __future__ import annotations
from datetime import time
from typing import List, Optional, Set, Dict, Tuple
from models import Section, RequirementGroup, CourseUnit, CatalogManager, SchedulePreferences
import re
#-----------------------------------------------------------------
# Description:
# The script to calculate and generate the most optimized schedule
#
# Developed with assistance from Google Gemini using agentic workflows 
#-----------------------------------------------------------------

def is_discussion_section(section: Section) -> bool:
    sec_num = str(getattr(section, "section", "")).strip().upper()
    return sec_num.endswith("D")

def section_meets_preferences(sec: Section, prefs: Optional[SchedulePreferences]) -> bool:
    if not prefs:
        return True

    # 1. Delivery Mode & Exclude Online Filter
    if getattr(prefs, "exclude_online", False) or prefs.delivery_mode == "In-person":
        if sec.is_online:
            return False
    elif prefs.delivery_mode == "Online" and not sec.is_online:
        return False

    # 2. Asynchronous Online
    if sec.is_online and (sec.start_time is None or not sec.days):
        if not getattr(prefs, "ignore_capacity", False):
            if getattr(sec, "status", "Open").lower() in ["closed", "waitlist", "waitlisted"]:
                return False
        return True

    # 3. Synchronous / In-Person: Must have start, end, and days
    if sec.start_time is None or sec.end_time is None or not sec.days:
        return False

    # 4. Capacity Filter
    if not getattr(prefs, "ignore_capacity", False):
        if getattr(sec, "status", "Open").lower() in ["closed", "waitlist", "waitlisted"]:
            return False

    # 5. Allowed Days Filter
    if prefs.allowed_days is not None:
        allowed_set = {d.strip() for d in prefs.allowed_days}
        if not set(sec.days).issubset(allowed_set):
            return False

    # 6. Time Limits
    s_min = sec.start_time.hour * 60 + sec.start_time.minute
    e_min = sec.end_time.hour * 60 + sec.end_time.minute

    if prefs.earliest_start is not None:
        if s_min < (prefs.earliest_start.hour * 60 + prefs.earliest_start.minute):
            return False

    if prefs.latest_end is not None:
        if e_min > (prefs.latest_end.hour * 60 + prefs.latest_end.minute):
            return False

    return True

def bundle_sections_for_course(
    course_id: str,
    catalog: CatalogManager,
    current_schedule_mask: int,
    preferences: Optional[SchedulePreferences]
) -> List[Tuple[List[Section], int]]:
    """
    Returns valid section bundles alongside their precomputed combined bitmask:
    [( [Section, ...], combined_mask ), ...]
    """
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
            raw_sections = catalog.get_sections(f"{match.group(1).upper()} {match.group(2).upper()}")
    if not raw_sections:
        return []

    # Fast filter: preferences + single CPU bitwise operation against current schedule
    eligible = [
        s for s in raw_sections
        if section_meets_preferences(s, preferences)
        and (s.time_mask == 0 or (s.time_mask & current_schedule_mask) == 0)
    ]
    if not eligible:
        return []

    has_discussions = any(is_discussion_section(s) for s in raw_sections)

    if has_discussions:
        lectures = [s for s in eligible if not is_discussion_section(s)]
        discussions = [s for s in eligible if is_discussion_section(s)]

        if target_comp_filter == "DISC":
            return [([d], d.time_mask) for d in discussions]
        if target_comp_filter == "LEC":
            return [([l], l.time_mask) for l in lectures]

        if not lectures or not discussions:
            return []

        # Time-Equivalence Deduplication: Skip evaluating identical meeting time pairs
        seen_time_combos = set()
        paired_bundles = []

        for lec in lectures:
            for disc in discussions:
                if (lec.time_mask & disc.time_mask) == 0:
                    combined_mask = lec.time_mask | disc.time_mask
                    if (combined_mask & current_schedule_mask) == 0:
                        combo_key = (lec.time_mask, disc.time_mask)
                        if combo_key not in seen_time_combos:
                            seen_time_combos.add(combo_key)
                            paired_bundles.append(([lec, disc], combined_mask))
        return paired_bundles

    # Time-Equivalence Grouping for lecture-only sections
    seen_masks = set()
    result = []
    for s in eligible:
        if s.time_mask == 0:
            result.append(([s], 0))
        elif s.time_mask not in seen_masks:
            seen_masks.add(s.time_mask)
            result.append(([s], s.time_mask))
    return result

def solve_schedule(
    requirements: List[RequirementGroup],
    catalog: CatalogManager,
    target_count: int = 5,
    preferences: Optional[SchedulePreferences] = None,
    fixed_sections: Optional[List[Section]] = None
) -> Optional[List[Section]]:
    seed_schedule: List[Section] = list(fixed_sections) if fixed_sections else []
    seed_enrolled: Set[str] = set()
    seed_mask: int = 0

    for s in seed_schedule:
        seed_mask |= s.time_mask
        c_tag = f"{s.course_name.strip()}{s.course_number.strip()}".upper()
        cat_secs = catalog.get_sections(c_tag)
        if any(is_discussion_section(sec) for sec in cat_secs):
            comp_tag = f"{c_tag}_DISC" if is_discussion_section(s) else f"{c_tag}_LEC"
            seed_enrolled.add(comp_tag)
        else:
            seed_enrolled.add(c_tag)

    distinct_seed = {re.sub(r'_(LEC|DISC)$', '', tag) for tag in seed_enrolled}
    if not requirements or len(distinct_seed) >= target_count:
        return seed_schedule if seed_schedule else None

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
        active_enrolled: Set[str],
        active_mask: int
    ) -> Optional[List[Section]]:
        nonlocal best_schedule, best_course_count, iterations
        iterations += 1

        if iterations > MAX_SEARCH_ITERATIONS:
            return None

        distinct_courses = {re.sub(r'_(LEC|DISC)$', '', tag) for tag in active_enrolled}
        current_distinct_count = len(distinct_courses)

        if current_distinct_count > best_course_count:
            best_course_count = current_distinct_count
            best_schedule = list(active_sched)

        if current_distinct_count >= effective_target:
            return list(active_sched)

        if req_idx >= len(requirements):
            return None

        current_req = requirements[req_idx]
        candidates = current_req.units

        if remaining_needed <= 0 or cand_idx >= len(candidates):
            next_req_idx = req_idx + 1
            if next_req_idx >= len(requirements):
                return None
            next_needed = requirements[next_req_idx].courses_needed
            return backtrack(next_req_idx, 0, next_needed, active_sched, active_enrolled, active_mask)

        unit = candidates[cand_idx]
        unit_courses = [c.upper() for c in unit.courses]
        sample_raw = unit_courses[0] if unit_courses else ""
        is_disc_req = ":DISC_ONLY" in sample_raw
        is_lec_req = ":LEC_ONLY" in sample_raw
        sample_id = re.sub(r':(DISC_ONLY|LEC_ONLY)$', '', sample_raw).replace(" ", "").upper()

        cat_secs = catalog.get_sections(sample_id)
        has_disc = any(is_discussion_section(s) for s in cat_secs)

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
            return backtrack(req_idx, cand_idx + 1, remaining_needed, active_sched, active_enrolled, active_mask)

        # Retrieve section bundles with precomputed bitmasks
        bundles_with_masks = []
        if unit.unit_type == "OR":
            for cid in unit.courses:
                bundles_with_masks.extend(bundle_sections_for_course(cid, catalog, active_mask, preferences))
        elif unit.unit_type == "AND":
            # For linked AND pairs, verify non-overlap between components
            per_course = [bundle_sections_for_course(cid, catalog, active_mask, preferences) for cid in unit.courses]
            if all(per_course):
                from itertools import product
                for combo in product(*per_course):
                    combined_bundle = []
                    combo_mask = 0
                    conflict = False
                    for b_secs, b_mask in combo:
                        if b_mask != 0 and (b_mask & combo_mask) != 0:
                            conflict = True
                            break
                        combo_mask |= b_mask
                        combined_bundle.extend(b_secs)
                    if not conflict and (combo_mask & active_mask) == 0:
                        bundles_with_masks.append((combined_bundle, combo_mask))
        else:
            bundles_with_masks = bundle_sections_for_course(sample_raw, catalog, active_mask, preferences)

        for sec_group, b_mask in bundles_with_masks:
            # CPU single-cycle collision check
            if b_mask != 0 and (b_mask & active_mask) != 0:
                continue

            added_tags = []
            for added_sec in sec_group:
                c_clean = f"{added_sec.course_name.strip()}{added_sec.course_number.strip()}".upper()
                if has_disc:
                    added_tags.append(f"{c_clean}_DISC" if is_discussion_section(added_sec) else f"{c_clean}_LEC")
                else:
                    added_tags.append(c_clean)

            active_sched.extend(sec_group)
            active_enrolled.update(added_tags)

            result = backtrack(
                req_idx=req_idx,
                cand_idx=cand_idx + 1,
                remaining_needed=remaining_needed - 1,
                active_sched=active_sched,
                active_enrolled=active_enrolled,
                active_mask=active_mask | b_mask
            )
            if result is not None:
                return result

            del active_sched[-len(sec_group):]
            active_enrolled.difference_update(added_tags)

        return backtrack(
            req_idx=req_idx,
            cand_idx=cand_idx + 1,
            remaining_needed=remaining_needed,
            active_sched=active_sched,
            active_enrolled=active_enrolled,
            active_mask=active_mask
        )

    initial_needed = requirements[0].courses_needed
    exact_match = backtrack(0, 0, initial_needed, seed_schedule, seed_enrolled, seed_mask)
    if exact_match is not None:
        return exact_match
    return best_schedule if len(best_schedule) > len(seed_schedule) or best_schedule else None