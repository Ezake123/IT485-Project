from __future__ import annotations
import re
from dataclasses import dataclass, field
from datetime import time
from typing import List, Optional, Set, Dict, Any

#--------------------------------------------------------------
# Description:
# Shared data classes used by the scheduler, the validator and the
# catalog. Course IDs are always written without spaces, e.g. "CHEM115".
#--------------------------------------------------------------


def parse_time_value(val: Any) -> Optional[time]:
    """'14:30:00', '9:00' or a time object -> time. Anything else -> None."""
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


def course_id(code: str) -> str:
    """'CHEM 115' or 'chem115' -> 'CHEM115'"""
    return str(code).replace(" ", "").upper()


def spaced(cid: str) -> str:
    """'CHEM115' -> 'CHEM 115'"""
    return re.sub(r"^([A-Z]+)(\d)", r"\1 \2", cid)


def number_value(course_number: str) -> int:
    """'304' -> 304, '114L' -> 114"""
    match = re.match(r"\d+", str(course_number))
    return int(match.group()) if match else -1


#--------------------------------------------------------------
# For degree audits
#--------------------------------------------------------------

@dataclass
class CourseUnit:
    """
    One option inside a requirement.
    - Single:      unit_type='SINGLE', courses=['CS110']
    - Set (both):  unit_type='AND',    courses=['CHEM115', 'CHEM117']
    - Either one:  unit_type='OR',     courses=['BIOL210', 'BIOL212']
    """
    raw_token: str
    unit_type: str  # 'SINGLE', 'AND', 'OR'
    courses: List[str]

    @classmethod
    def from_token(cls, token: str) -> CourseUnit:
        clean = course_id(token)
        if "&" in clean:
            return cls(raw_token=token, unit_type="AND", courses=clean.split("&"))
        if "|" in clean:
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
    group_name: str
    courses_needed: int
    units: List[CourseUnit] = field(default_factory=list)


#--------------------------------------------------------------
# Matches the data with Supabase
#--------------------------------------------------------------

DAY_ORDER = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]


@dataclass
class SchedulePreferences:
    earliest_start: Optional[time] = None        # e.g. time(9, 0)
    latest_end: Optional[time] = None            # e.g. time(17, 0)
    allowed_days: Optional[List[str]] = None     # e.g. ['Mo', 'We', 'Fr']
    delivery_mode: str = "Any"                   # "In-person", "Online" or "Any"
    open_seats_only: bool = True                 # skip full sections
    max_courses: int = 5
    max_credits: int = 18
    fit_maximum: bool = False                    # fill up to max_courses / max_credits


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
    credits: Optional[int] = None

    @property
    def course_id(self) -> str:
        return f"{self.course_name.strip()}{self.course_number.strip()}"

    @property
    def has_meeting_time(self) -> bool:
        return bool(self.days and self.start_time and self.end_time)

    @property
    def is_online(self) -> bool:
        loc = (self.location or "").strip().lower()
        if any(word in loc for word in ("online", "distance", "remote", "asynch")):
            return True
        return not self.has_meeting_time and not loc

    @property
    def is_full(self) -> bool:
        if self.maximum_capacity and self.enrolled is not None:
            return self.enrolled >= self.maximum_capacity
        return False

    @property
    def is_discussion(self) -> bool:
        """Discussion/breakout sections end in 'D', e.g. '01D'."""
        return bool(self.section and self.section.strip().upper().endswith("D"))


@dataclass
class Course:
    course_name: str                             # e.g. 'CHEM'
    course_number: str                           # e.g. '115'
    description: Optional[str] = None
    gen_ed: Optional[str] = None
    prerequisites: Dict[str, Any] = field(default_factory=dict)
    credits: Optional[int] = None
    id: Optional[int] = None

    @property
    def course_id(self) -> str:
        return f"{self.course_name.strip()}{self.course_number.strip()}"

    @property
    def prereq_text(self) -> str:
        raw = (self.prerequisites or {}).get("raw") or ""
        return "" if raw.strip().lower() == "none" else raw.strip()

    @property
    def prerequisites_cnf(self) -> List[List[str]]:
        """Every inner list is required; any ONE course in it is enough."""
        cnf: List[List[str]] = []
        for clause in (self.prerequisites or {}).get("courses") or []:
            group = []
            for item in clause:
                if isinstance(item, dict) and item.get("course_name") and item.get("course_number"):
                    group.append(f"{item['course_name'].strip()}{item['course_number'].strip()}")
            if group:
                cnf.append(group)
        return cnf

    @property
    def coreq_numbers(self) -> Set[str]:
        """
        Course numbers listed after "Co-req"/"Co-requisite" in the prerequisite text.
        Those may be taken in the same semester instead of beforehand.
        e.g. "CHEM 117 and Co-requisite: CHEM 116" -> {"116"}
        """
        raw = self.prereq_text
        match = re.search(r"co\s*-?\s*req", raw, re.IGNORECASE)
        if not match:
            return set()
        return set(re.findall(r"\b(\d{3}[A-Z]?)\b", raw[match.start():].upper()))

    def is_coreq_group(self, group: List[str]) -> bool:
        numbers = self.coreq_numbers
        return any(re.sub(r"^[A-Z]+", "", c) in numbers for c in group)

    def missing_prerequisites(self, completed: Set[str], earned_credits: float = 0,
                              planned: Set[str] = frozenset()) -> List[str]:
        """
        Prerequisites not yet met, as readable strings ("CHEM 115 or CHEM 103").
        Co-requisites count as met if they're completed OR planned for the same semester.
        """
        missing = []
        min_credits = (self.prerequisites or {}).get("min_credits")
        if min_credits and earned_credits < min_credits:
            missing.append(f"{min_credits} earned credits")
        for group in self.prerequisites_cnf:
            ok = set(completed) | (set(planned) if self.is_coreq_group(group) else set())
            if not any(c in ok for c in group):
                missing.append(" or ".join(spaced(c) for c in group))
        return missing

    def hard_prerequisites_met(self, completed: Set[str], earned_credits: float = 0) -> bool:
        """True if everything except co-requisites is met (co-reqs are handled when scheduling)."""
        min_credits = (self.prerequisites or {}).get("min_credits")
        if min_credits and earned_credits < min_credits:
            return False
        return all(
            self.is_coreq_group(group) or any(c in completed for c in group)
            for group in self.prerequisites_cnf
        )


class CatalogManager:
    def __init__(self):
        self.courses: Dict[str, Course] = {}           # keyed by 'CHEM115'
        self.sections: Dict[str, List[Section]] = {}   # keyed by 'CHEM115'

    def add_course(self, course: Course):
        self.courses[course.course_id] = course

    def add_section(self, section: Section):
        self.sections.setdefault(section.course_id, []).append(section)

    def get_course(self, cid: str) -> Optional[Course]:
        return self.courses.get(course_id(cid))

    def get_sections(self, cid: str) -> List[Section]:
        return self.sections.get(course_id(cid), [])

    def credits_for(self, cid: str) -> int:
        """Section credits if the scraper stored them there, otherwise the course's credits."""
        for sec in self.get_sections(cid):
            if sec.credits:
                return sec.credits
        course = self.get_course(cid)
        return (course.credits if course and course.credits else 0)

    def offered_in_range(self, subject: str, low: int, high: int) -> List[str]:
        """Course IDs like 'BIOL316' with a number in [low, high] that have sections this term."""
        found = [
            cid for cid, secs in self.sections.items()
            if secs and secs[0].course_name == subject
            and low <= number_value(secs[0].course_number) <= high
        ]
        return sorted(found, key=lambda c: (number_value(re.sub(r"^[A-Z]+", "", c)), c))

    @classmethod
    def load_from_supabase_data(cls, courses_rows: List[dict], sections_rows: List[dict]) -> CatalogManager:
        manager = cls()
        for row in courses_rows:
            manager.add_course(Course(
                id=row.get("id"),
                course_name=row["course_name"],
                course_number=row["course_number"],
                description=row.get("description"),
                gen_ed=row.get("gen_ed"),
                prerequisites=row.get("prerequisites") or {},
                credits=_to_int(row.get("credits")),
            ))

        for row in sections_rows:
            if not row.get("course_name") or not row.get("course_number"):
                continue
            manager.add_section(Section(
                class_code=str(row["class_code"]),
                course_name=row["course_name"],
                course_number=row["course_number"],
                section=str(row.get("section") or ""),
                days=row.get("days") or [],
                start_time=parse_time_value(row.get("start_time")),
                end_time=parse_time_value(row.get("end_time")),
                location=row.get("location"),
                maximum_capacity=row.get("maximum_capacity"),
                enrolled=row.get("enrolled"),
                instructors=row.get("instructors") or ["TBA"],
                term=row.get("term"),
                credits=_to_int(row.get("credits")),
            ))

        for secs in manager.sections.values():
            secs.sort(key=lambda s: s.section)
        return manager


def _to_int(value) -> Optional[int]:
    try:
        return int(float(value)) if value is not None else None
    except (ValueError, TypeError):
        return None
