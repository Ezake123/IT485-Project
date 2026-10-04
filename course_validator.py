from __future__ import annotations
from typing import List
from models import Requirements, CatalogManager, CourseUnit, RequirementGroup

#-----------------------------------------------------------------
# Description:
# A helper script to validate courses if they are offered
# (Could be merged since it is only used in app.py)
#  
# Developed with assistance from Google Gemini using agentic workflows
#-----------------------------------------------------------------

#-----------------------------------------------------------------
# Verifies if the course in the database or offered for the term
#-----------------------------------------------------------------

def is_unit_offered(unit: CourseUnit, catalog: CatalogManager) -> bool:
    if unit.unit_type == "AND":
        return all(catalog.get_course(c) and len(catalog.get_sections(c)) > 0 for c in unit.courses)
    if unit.unit_type == "OR":
        return any(catalog.get_course(c) and len(catalog.get_sections(c)) > 0 for c in unit.courses)
    c = unit.courses[0]
    has_course = catalog.get_course(c) is not None
    has_sections = len(catalog.get_sections(c)) > 0
    return has_course and has_sections

#-----------------------------------------------------------------
# Checks requirement groups and remove ones with courses that aren't
# offered and remove the requirement groups if the courses are removed
#-----------------------------------------------------------------

def validate_requirements(
    requirements: list[RequirementGroup],
    catalog: CatalogManager
) -> list[RequirementGroup]:
    valid_groups = []

    for group in requirements:
        offered_units = [u for u in group.units if is_unit_offered(u, catalog)]
        if offered_units:
            valid_groups.append(
                RequirementGroup(
                    group_name=group.group_name,
                    courses_needed=min(group.courses_needed, len(offered_units)),
                    units=offered_units
                )
            )

    return valid_groups