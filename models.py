from __future__ import annotations
from dataclasses import dataclass, field
from datetime import time
from typing import List, Optional, Set, Dict, Any

def parse_time_value(val: Any) -> Optional[time]:
    if not val:
        return None
    if isinstance(val, time):
        return val
    try:
        parts = [int(p) for p in str(val).strip().split(":")]
        if len(parts) >= 2:
            if parts[0] >= 24:
                return time(23, 59, 59)
            return time(parts[0], parts[1], parts[2] if len(parts) > 2 else 0)
    except Exception:
        pass
    return None

#--------------------------------------------------------------
# For degree audits
#--------------------------------------------------------------

@dataclass
class CourseUnit:
    """
    Represents an atomic course, co-requisite set, or alternative unit.
    - Single: unit_type='SINGLE', courses=['CS110']
    - Corequisite: unit_type='AND', courses=['PHYSIC114', 'PHYSIC182']
    - Alternative: unit_type='OR', courses=['BIOL210', 'BIOL212']
    """
    raw_token: str
    unit_type: str  # 'SINGLE', 'AND', 'OR'
    courses: List[str]

    @classmethod
    def from_token(cls, token: str) -> CourseUnit:
        clean = token.replace(" ", "").upper()
        if "&" in clean:
            return cls(raw_token=token, unit_type="AND", courses=clean.split("&"))
        elif "|" in clean:
            return cls(raw_token=token, unit_type="OR", courses=clean.split("|"))
        return cls(raw_token=token, unit_type="SINGLE", courses=[clean])

    def is_completed(self, completed_set: Set[str]) -> bool:
        if self.unit_type == "AND":
            return all(c in completed_set for c in self.courses)
        if self.unit_type == "OR":
            return any(c in completed_set for c in self.courses)
        return self.courses[0] in completed_set

@dataclass
class RequirementGroup:
    """Replaces raw [count, 'CS110', ...] nested lists."""
    group_name: str
    courses_needed: int
    units: List[CourseUnit] = field(default_factory=list)

@dataclass
class DegreeAuditResult:
    """Universal transfer payload emitted directly by pdf_scanner.py."""
    completed_courses: Set[str]
    earned_credits: float
    requirements: List[RequirementGroup]

#--------------------------------------------------------------
# Matches the data with Supabase
#--------------------------------------------------------------

@dataclass
class SchedulePreferences:
    earliest_start: Optional[time] = None       # e.g., time(9, 0)
    latest_end: Optional[time] = None           # e.g., time(17, 0)
    allowed_days: Optional[List[str]] = None    # e.g., ['M', 'Tu', 'W', 'Th', 'F']
    delivery_mode: str = "Any"                  # "In-person", "Online", or "Any"
    fit_maximum: bool = False

@dataclass
class Section:
    class_code: str
    course_name: str
    course_number: str
    section: str
    days: Optional[List[str]] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    location: Optional[str] = None
    maximum_capacity: Optional[int] = None
    enrolled: Optional[int] = None
    instructors: List[str] = field(default_factory=lambda: ["TBA"])
    term: Optional[str] = None
    credits: int = 0                             # <-- Defined on Section

    @property
    def course_id(self) -> str:
        return f"{self.course_name.strip()}{self.course_number.strip()}"

    @property
    def is_online(self) -> bool:
        if not self.location:
            return not self.days or not self.start_time
        loc = self.location.strip().lower()
        return "online" in loc or "distance" in loc or "remote" in loc

    @property
    def is_full(self) -> bool:
        if self.maximum_capacity is not None and self.enrolled is not None:
            return self.enrolled >= self.maximum_capacity
        return False

    @property
    def delivery_type(self) -> str:
        return "Online" if self.is_online else "In-person"
    
    @property
    def formatted_start_time(self) -> str:
        if not self.start_time:
            return "TBA"
        return self.start_time.strftime("%I:%M %p").lstrip("0")

    @property
    def formatted_end_time(self) -> str:
        if not self.end_time:
            return "TBA"
        return self.end_time.strftime("%I:%M %p").lstrip("0")

@dataclass
class Course:
    course_name: str                            # e.g., 'AF'
    course_number: str                          # e.g., '470'
    description: Optional[str] = None
    gen_ed: Optional[str] = None                # e.g., 'Arts/Humanities' or None
    prerequisites: Dict[str, Any] = field(default_factory=dict)
    id: Optional[int] = None                    # Supabase BIGINT ID

    @property
    def course_id(self) -> str:
        return f"{self.course_name.strip()}{self.course_number.strip()}"

    @property
    def prerequisites_cnf(self) -> List[List[str]]:
        raw_cnf = self.prerequisites.get("courses", [])
        cnf: List[List[str]] = []
        for clause in raw_cnf:
            or_group = []
            for item in clause:
                c_name = item.get("course_name", "").strip()
                c_num = item.get("course_number", "").strip()
                if c_name and c_num:
                    or_group.append(f"{c_name}{c_num}")
            if or_group:
                cnf.append(or_group)
        return cnf

    def prerequisites_met(self, completed_course_ids: Set[str], current_earned_credits: int = 0) -> bool:
        min_credits = self.prerequisites.get("min_credits")
        if min_credits and current_earned_credits < min_credits:
            return False

        for or_clause in self.prerequisites_cnf:
            if not any(req in completed_course_ids for req in or_clause):
                return False
        return True

@dataclass
class Requirements:
    requirement_name: str
    courses_needed: int
    candidate_courses: List[str]                # Can contain units like 'CHEM115&CHEM117' or 'BIOL210|BIOL212'

class CatalogManager:
    def __init__(self):
        self.courses: Dict[str, Course] = {}                    # Keyed by 'AF470'
        self.sections: Dict[str, List[Section]] = {}            # Keyed by 'AF470'

    def add_course(self, course: Course):
        self.courses[course.course_id] = course

    def add_section(self, section: Section):
        cid = section.course_id
        if cid not in self.sections:
            self.sections[cid] = []
        self.sections[cid].append(section)

    def get_course(self, course_id: str) -> Optional[Course]:
        return self.courses.get(course_id)

    def get_sections(self, course_id: str) -> List[Section]:
        return self.sections.get(course_id, [])

    @classmethod
    def load_from_supabase_data(cls, courses_rows: List[dict], sections_rows: List[dict]) -> CatalogManager:
        manager = cls()
        id_to_course: Dict[int, Course] = {}

        for row in courses_rows:
            course = Course(
                id=row.get("id"),
                course_name=row["course_name"],
                course_number=row["course_number"],
                description=row.get("description"),
                gen_ed=row.get("gen_ed"),
                prerequisites=row.get("prerequisites") or {}
            )
            manager.add_course(course)
            if course.id is not None:
                id_to_course[course.id] = course

        for row in sections_rows:
            c_name = row.get("course_name")
            c_num = row.get("course_number")

            if not c_name and "course_id" in row and row["course_id"] in id_to_course:
                matched_course = id_to_course[row["course_id"]]
                c_name = matched_course.course_name
                c_num = matched_course.course_number

            if c_name and c_num:
                raw_credits = row.get("credits")
                try:
                    sec_credits = int(float(raw_credits)) if raw_credits is not None else 0
                except (ValueError, TypeError):
                    sec_credits = 0

                sec = Section(
                    class_code=str(row["class_code"]),
                    course_name=c_name,
                    course_number=c_num,
                    section=str(row.get("section", "")),
                    days=row.get("days"),
                    start_time=parse_time_value(row.get("start_time")),
                    end_time=parse_time_value(row.get("end_time")),
                    location=row.get("location"),
                    maximum_capacity=row.get("maximum_capacity"),
                    enrolled=row.get("enrolled"),
                    instructors=row.get("instructors") or ["TBA"],
                    term=row.get("term"),
                    credits=sec_credits
                )
                manager.add_section(sec)

        return manager