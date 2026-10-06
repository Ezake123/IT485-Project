from __future__ import annotations
from dataclasses import dataclass, field
from datetime import time
from typing import List, Optional, Set, Dict, Any

#-----------------------------------------------------------------
# Description:
# Data models for degree audit and course scheduling pipeline
#
# Developed with assistance from Google Gemini using agentic workflows 
#-----------------------------------------------------------------

# -----------------------------------------------------------------
# Centralized General Education Configuration & Token Mappings
# -----------------------------------------------------------------

# Canonical mapping from Token suffix -> Database catalog Gen Ed field
GENED_TOKEN_MAP = {
    "ARTS": "The Arts",
    "THE_ARTS": "The Arts",
    "HUMANITIES": "Humanities",
    "ARTS_OR_HUMANITIES": "The Arts or Humanities",
    "SOCIAL_AND_BEHAVIORAL_SCIENCES": "Social & Behavioral Sciences",
    "NATURAL_SCIENCES": "Natural Sciences",
    "NATURAL_SCIENCES_OR_MATH_TECH": "Natural Sciences or Mathematics & Technology",
    "MATHEMATICS_TECHNOLOGY": "Mathematics & Technology",
    "WORLD_LANGUAGES_OR_WORLD_CULTURES": "World Cultures or World Languages",
    "WORLD_CULTURES": "World Cultures",
    "WORLD_LANGUAGES": "World Languages"
}

# Ordered list of (Audit Display Name, Token Suffix) used for PDF scanning
KNOWN_GENED_CATEGORIES = [
    ("Social & Behavioral Sciences", "SOCIAL_AND_BEHAVIORAL_SCIENCES"),
    ("World Languages or World Cultures", "WORLD_LANGUAGES_OR_WORLD_CULTURES"),
    ("World Languages", "WORLD_LANGUAGES"),
    ("World Cultures", "WORLD_CULTURES"),
    ("Natural Sciences or Math/Technology", "NATURAL_SCIENCES_OR_MATH_TECH"),
    ("Natural Sciences", "NATURAL_SCIENCES"),
    ("Arts or Humanities", "ARTS_OR_HUMANITIES"),
    ("Mathematics & Technology", "MATHEMATICS_TECHNOLOGY"),
    ("Humanities", "HUMANITIES"),
    ("The Arts", "ARTS"),
]

#--------------------------------------------------------------
# Format for degree audits
#--------------------------------------------------------------

# Each courses scanned from the scanner
@dataclass
class CourseUnit:
    #-----------------------------------------------------------------
    # Represents an atomic course, co-requisite set, or alternative unit.
    # - Single: unit_type='SINGLE', courses=['CS110']
    # - Corequisite: unit_type='AND', courses=['PHYSIC114', 'PHYSIC182']
    # - Alternative: unit_type='OR', courses=['BIOL210', 'BIOL212']
    #-----------------------------------------------------------------
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

# Groups the courses into their corresponding requirement 
@dataclass
class RequirementGroup:
    group_name: str
    courses_needed: int
    units: List[CourseUnit] = field(default_factory=list)

# Universal payload created by the scanner
@dataclass
class DegreeAuditResult:
    completed_courses: Set[str]
    earned_credits: float
    requirements: List[RequirementGroup]
    gen_ed_requirements: List[RequirementGroup] = field(default_factory=list)

#--------------------------------------------------------------
# Format to match the data exactly like the ones in database
#--------------------------------------------------------------

#
@dataclass
class SchedulePreferences:
    earliest_start: Optional[time] = None       # e.g., time(9, 0)
    latest_end: Optional[time] = None           # e.g., time(17, 0)
    allowed_days: Optional[List[str]] = None    # e.g., ['M', 'Tu', 'W', 'Th', 'F']
    delivery_mode: str = "Any"                  # "In-person", "Online", or "Any"
    ignore_capacity: bool = False               # Ignore capacity limit in schedule

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
    credits: int = 0                            

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
    
    def to_dict(self) -> dict:
        return {
            "class_code": self.class_code,
            "section": self.section,
            "days": self.days or [],
            "start_time": self.formatted_start_time,
            "end_time": self.formatted_end_time,
            "is_online": self.is_online
        }

@dataclass
class Course:
    course_name: str                            # e.g., 'AF'
    course_number: str                          # e.g., '470'
    description: Optional[str] = None
    gen_ed: Optional[str] = None                # e.g., 'Arts/Humanities' or None
    prerequisites: Dict[str, Any] = field(default_factory=dict)
    id: Optional[int] = None                    # Supabase ID
    sections: List[Section] = field(default_factory=list)

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

#-----------------------------------------------------------------
# Helper function to parse time vlaue from database
#-----------------------------------------------------------------

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

#-----------------------------------------------------------------
# Manager for 
#-----------------------------------------------------------------

class CatalogManager:
    def __init__(self):
        self.courses: Dict[str, Course] = {}                    
        self.sections: Dict[str, List[Section]] = {}
        self.dept_index: Dict[str, List[Course]] = {}           # Fast O(1) department index

    @staticmethod
    def _normalize_id(course_id: str) -> str:
        return str(course_id).replace(" ", "").strip().upper()

    def add_course(self, course: Course):
        norm_id = self._normalize_id(course.course_id)
        self.courses[norm_id] = course

        # Index course under its uppercase subject code (e.g., 'CS', 'IT', 'MATH')
        dept_key = course.course_name.strip().upper()
        if dept_key not in self.dept_index:
            self.dept_index[dept_key] = []
        self.dept_index[dept_key].append(course)

    def add_section(self, section: Section):
        norm_id = self._normalize_id(section.course_id)
        if norm_id not in self.sections:
            self.sections[norm_id] = []
        self.sections[norm_id].append(section)
        
        # Link directly to course object for instant O(1) retrieval during search
        course = self.courses.get(norm_id)
        if course:
            course.sections.append(section)

    def get_course(self, course_id: str) -> Optional[Course]:
        return self.courses.get(self._normalize_id(course_id))

    def get_sections(self, course_id: str) -> List[Section]:
        return self.sections.get(self._normalize_id(course_id), [])

    @classmethod
    def load_from_supabase_data(cls, courses_rows: List[dict], sections_rows: List[dict]) -> CatalogManager:
        manager = cls()

        for row in courses_rows:
            course = Course(
                id=row.get("id"),
                course_name=str(row["course_name"]).strip(),
                course_number=str(row["course_number"]).strip(),
                description=row.get("description"),
                gen_ed=row.get("gen_ed"),
                prerequisites=row.get("prerequisites") or {}
            )
            manager.add_course(course)

        for row in sections_rows:
            c_name = str(row.get("course_name", "")).strip()
            c_num = str(row.get("course_number", "")).strip()

            if c_name and c_num:
                raw_credits = row.get("credits")
                try:
                    sec_credits = int(float(raw_credits)) if raw_credits is not None else 3
                except (ValueError, TypeError):
                    sec_credits = 3

                sec = Section(
                    class_code=str(row.get("class_code", "")),
                    course_name=c_name,
                    course_number=c_num,
                    section=str(row.get("section", "")),
                    days=row.get("days") or [],
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