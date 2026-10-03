from __future__ import annotations
from typing import List
from models import Requirements, CatalogManager, CourseUnit, RequirementGroup

def is_unit_offered(unit: CourseUnit, catalog: CatalogManager) -> bool:
    """Verifies courses exist in the catalog and have active sections."""
    if unit.unit_type == "AND":
        return all(catalog.get_course(c) and catalog.get_sections(c) for c in unit.courses)
    if unit.unit_type == "OR":
        return any(catalog.get_course(c) and catalog.get_sections(c) for c in unit.courses)
    c = unit.courses[0]
    return bool(catalog.get_course(c) and catalog.get_sections(c))


def validate_requirements(
    requirements: list[RequirementGroup],
    catalog: CatalogManager
) -> list[RequirementGroup]:
    """Prunes unoffered units and empty requirement groups."""
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