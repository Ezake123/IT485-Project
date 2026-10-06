import os
import io
import json
import re
from datetime import datetime, time
from functools import lru_cache
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
from supabase import create_client, Client
import pdfplumber
from werkzeug.exceptions import RequestEntityTooLarge
from models import CatalogManager, SchedulePreferences, CourseUnit, RequirementGroup, Section, parse_time_value, GENED_TOKEN_MAP
import pdf_scanner
import scheduler

# flask-compress is optional so a missing package can't take the site down.
# Add "flask-compress" to requirements.txt to turn compression on.
try:
    from flask_compress import Compress
except ImportError:
    Compress = None

#-----------------------------------------------------------------
# Description:
# Connects all the scripts and website together
#
# Requirement:
# Verify if all these are installed (all of them belong in requirements.txt):
#   pip install Flask
#   pip install gunicorn
#   pip install supabase
#   pip install pdfplumber
#   pip install flask-compress
#   pip install python-dotenv
#   pip install werkzeug
#
# How to use it:
# Type "python app.py" from the folder location, make sure you also
# have a folder named templates with the website inside. Next go
# to your web browser and enter "http://127.0.0.1:5000"
#
# Developed with assistance from Google Gemini using agentic workflows
#-----------------------------------------------------------------

load_dotenv()

app = Flask(__name__)
if Compress:
    Compress(app)
else:
    print("[!] flask-compress isn't installed, so pages are sent uncompressed. Add it to requirements.txt.")

# Enforce a 100 KB maximum upload limit since most degree audit can't even get over 50KB
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024

# Initialize Supabase client
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if (SUPABASE_URL and SUPABASE_KEY) else None

#-----------------------------------------------------------------
# Calculate the default semester, excludes winter and summer
#-----------------------------------------------------------------

ALLOWED_SEASONS = ("Spring", "Summer", "Fall", "Winter")

def get_default_term_info() -> tuple[str, int]:
    now = datetime.now()
    month = now.month
    year = now.year

    if month in (11, 12):
        return "Spring", year + 1
    elif month in (1, 2, 3):
        return "Spring", year
    else:
        return "Fall", year

#-----------------------------------------------------------------
# Turns the season/year the user picked into a safe term string like
# "Fall 2026", falling back to the default for anything unexpected
#-----------------------------------------------------------------

def resolve_term(season_raw, year_raw) -> tuple[str, int, str]:
    def_season, def_year = get_default_term_info()

    season = str(season_raw or "").strip().capitalize()
    if season not in ALLOWED_SEASONS:
        season = def_season

    try:
        year = int(str(year_raw).strip())
        if not 2000 <= year <= 2100:
            raise ValueError
    except (TypeError, ValueError):
        year = def_year

    return season, year, f"{season} {year}"

def resolve_term_string(term_raw: str) -> str:
    """'Fall 2026' (from the API's ?term=) -> validated 'Fall 2026'."""
    parts = str(term_raw or "").split()
    season, year = (parts + [None, None])[:2]
    return resolve_term(season, year)[2]

def term_year_options() -> list[int]:
    _, def_year = get_default_term_info()
    this_year = datetime.now().year
    return sorted({this_year, this_year + 1, def_year})

#-----------------------------------------------------------------
# Normalizes Gen Ed labels so "Mathematics and Technology" (UI) and
# "Mathematics & Technology" (database) are treated as the same thing
#-----------------------------------------------------------------

def normalize_gened(text: str) -> str:
    text = (text or "").lower().replace("&", " and ")
    return re.sub(r"\s+", " ", text).strip()

#-----------------------------------------------------------------
# Prerequisite helpers. Course IDs are compared without spaces in
# upper case, e.g. "IT110", to match the audit scanner's format.
#-----------------------------------------------------------------

def normalize_course_id(cid: str) -> str:
    return str(cid).replace(" ", "").strip().upper()

def prereq_text_for(course_obj) -> str:
    raw = course_obj.prerequisites if course_obj else None
    if not raw:
        return "None"
    if isinstance(raw, dict):
        return raw.get("raw") or "None"
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed.get("raw", raw) if isinstance(parsed, dict) else raw
        except Exception:
            return raw
    return "None"

# The scraper only keeps course codes from prerequisite text, so wording like
# "or permission of instructor", "placement into MATH 140" or "or equivalent"
# gets lost. When the text has an alternative like that, an unmet course list
# isn't proof the student can't enroll, so we flag it for review instead.
PREREQ_ALTERNATIVE_RE = re.compile(
    r"permission|consent|approval|instructor|placement|aleks|score|equivalent|"
    r"or higher|may not register|class note|see note|department",
    re.IGNORECASE,
)

def prereq_status(course_id: str, catalog: CatalogManager, completed_set, earned_credits) -> str:
    """Returns 'met', 'review' (unclear, schedule but warn) or 'unmet' (skip)."""
    course_obj = catalog.get_course(course_id)
    if not course_obj:
        return "met"  # Nothing to check against
    try:
        if course_obj.prerequisites_met(completed_set, earned_credits):
            return "met"
    except Exception:
        return "met"  # Malformed prerequisite data shouldn't block scheduling
    if PREREQ_ALTERNATIVE_RE.search(prereq_text_for(course_obj)):
        return "review"
    return "unmet"

def format_course_id(cid: str) -> str:
    """'IT115L' -> 'IT 115L' for display."""
    m = re.match(r"^([A-Z]+)(\d.*)$", normalize_course_id(cid))
    return f"{m.group(1)} {m.group(2)}" if m else cid

def short_prereq_reason(course_id: str, catalog: CatalogManager, completed_set, earned_credits) -> str:
    """A one-line reason like 'Needs CS 110 or IT 115L, or instructor permission'
    built from the parsed prerequisites instead of the full catalog sentence."""
    course_obj = catalog.get_course(course_id)
    if not course_obj:
        return ""
    parts = []
    try:
        min_credits = course_obj.prerequisites.get("min_credits")
        if min_credits and earned_credits < min_credits:
            parts.append(f"{min_credits} credits")
        for clause in course_obj.prerequisites_cnf:
            if not any(c in completed_set for c in clause):
                parts.append(" or ".join(format_course_id(c) for c in clause))
    except Exception:
        return ""
    # Separate clauses with ", and " so "CS 110 or IT 115L, and MATH 130" reads unambiguously
    reason = ("Needs " + ", and ".join(parts)) if parts else "Has prerequisites"

    text = prereq_text_for(course_obj).lower()
    alternatives = []
    if re.search(r"permission|consent|approval|instructor", text):
        alternatives.append("instructor permission")
    if re.search(r"placement|aleks|score", text):
        alternatives.append("a placement score")
    if "equivalent" in text:
        alternatives.append("an equivalent course")
    if alternatives:
        reason += " (or " + " or ".join(alternatives) + ")"
    return reason

#-----------------------------------------------------------------
# Wildcard requirements from the audit, like "CS 3, 4" (any 300- or
# 400-level CS course) or "BIOL 304 TO 395". These can't be matched to
# specific courses, so students pick them themselves.
#-----------------------------------------------------------------

WILDCARD_LEVEL_RE = re.compile(r"^([A-Z]+)([1-5])(?:XX)?$")
WILDCARD_RANGE_RE = re.compile(r"^([A-Z]+)(\d+[A-Z]?)-([A-Z]*)(\d+[A-Z]?)$")

def is_wildcard_token(tok: str) -> bool:
    clean = normalize_course_id(tok)
    return bool(WILDCARD_LEVEL_RE.match(clean) or WILDCARD_RANGE_RE.match(clean))

def describe_wildcards(tokens: list[str]) -> list[str]:
    """['CS3', 'CS4'] -> ['any 300–400-level CS course'];
    ['BIOL304-BIOL395'] -> ['BIOL 304–395']"""
    levels_by_dept = {}
    ranges = []
    for tok in tokens:
        clean = normalize_course_id(tok)
        level = WILDCARD_LEVEL_RE.match(clean)
        if level:
            levels_by_dept.setdefault(level.group(1), set()).add(int(level.group(2)))
            continue
        rng = WILDCARD_RANGE_RE.match(clean)
        if rng:
            dept1, num1, dept2, num2 = rng.groups()
            if not dept2 or dept2 == dept1:
                ranges.append(f"{dept1} {num1}–{num2}")
            else:
                ranges.append(f"{dept1} {num1} – {dept2} {num2}")

    # Group departments that share the same levels, e.g. language courses
    by_levels = {}
    for dept, levels in levels_by_dept.items():
        by_levels.setdefault(tuple(sorted(levels)), []).append(dept)

    parts = []
    for levels, depts in by_levels.items():
        level_text = f"{levels[0]}00-level" if len(levels) == 1 else f"{levels[0]}00–{levels[-1]}00-level"
        dept_text = ", ".join(depts) if len(depts) <= 3 else f"{', '.join(depts[:3])} (+{len(depts) - 3} more)"
        parts.append(f"any {level_text} {dept_text} course")
    return parts + ranges

#-----------------------------------------------------------------
# Extracts unique offered courses from serialized requirements.
# Each course is tagged with the audit requirement it came from so
# the page can group them ("Choose 6 of these").
#-----------------------------------------------------------------

def get_candidate_courses_pool(serialized_reqs, catalog: CatalogManager):
    seen_ids = set()
    candidate_list = []

    for req_idx, req in enumerate(serialized_reqs, start=1):
        needed = req.get("courses_needed", 1)
        group_info = {
            "group": f"req-{req_idx:02d}",  # zero-padded so groups sort in audit order
            "group_label": f"Choose {needed} of these",
        }

        for unit_token in req.get("units", []):
            if unit_token.startswith("GENED:"):
                raw_tag = unit_token.split("GENED:", 1)[1].strip()
                target_gened = normalize_gened(GENED_TOKEN_MAP.get(raw_tag, raw_tag.replace("_", " ")))

                for course_id, course in catalog.courses.items():
                    if course.gen_ed and target_gened in normalize_gened(course.gen_ed):
                        cid_clean = course.course_id
                        if cid_clean not in seen_ids:
                            sections = catalog.get_sections(cid_clean)
                            if sections:
                                seen_ids.add(cid_clean)
                                candidate_list.append({
                                    "subject_code": course.course_name.strip(),
                                    "course_number": course.course_number.strip(),
                                    "name": getattr(course, "description", None) or f"{course.course_name} {course.course_number}",
                                    "credits": sections[0].credits if sections else 3,
                                    "sections_count": len(sections),
                                    "sections": [s.to_dict() for s in sections],
                                    **group_info
                                })
                continue

            # Standard course units (wildcards like "CS3" simply won't match a course)
            unit = CourseUnit.from_token(unit_token)
            target_ids = getattr(unit, "courses", [unit.raw_token])

            for cid in target_ids:
                cid_clean = cid.strip()
                if cid_clean not in seen_ids:
                    seen_ids.add(cid_clean)
                    course_obj = catalog.get_course(cid_clean)
                    sections = catalog.get_sections(cid_clean)

                    if course_obj and sections:
                        raw_cname = course_obj.course_name.strip()
                        raw_cnum = course_obj.course_number.strip()

                        candidate_list.append({
                            "subject_code": raw_cname or cid_clean,
                            "course_number": raw_cnum,
                            "name": course_obj.description or f"{raw_cname} {raw_cnum}",
                            "credits": sections[0].credits,
                            "sections_count": len(sections),
                            "sections": [s.to_dict() for s in sections],
                            **group_info
                        })

    candidate_list.sort(key=lambda c: (c["group"], c["subject_code"], c["course_number"]))
    return candidate_list

#-----------------------------------------------------------------
# Verifies if the course is in the database or offered for the term
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
# Checks requirement groups and filters out courses not offered
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

#-----------------------------------------------------------------
# Finds courses by term/semester
#-----------------------------------------------------------------

@lru_cache(maxsize=8)
def load_catalog_for_term(term: str = None) -> CatalogManager:
    if not supabase:
        raise ValueError("Missing SUPABASE_URL or SUPABASE_KEY in environment or .env file.")

    def_season, def_year = get_default_term_info()
    if not term:
        term = f"{def_season} {def_year}"

    clean_term = term.strip()
    print(f"[*] Querying Supabase catalog for term: '{clean_term}'...")

    # 1. Fetch all courses (paginated to avoid 1,000 row cap)
    all_courses = []
    page = 0
    page_size = 1000
    while True:
        res = supabase.table("courses").select("*").range(page * page_size, (page + 1) * page_size - 1).execute()
        all_courses.extend(res.data)
        if len(res.data) < page_size:
            break
        page += 1

    # 2. Fetch sections with flexible matching ("Fall 2026", "2026 Fall", "FA26")
    def fetch_sections_for_tag(target_tag: str):
        parts = target_tag.strip().split()
        sections = []
        p = 0

        # Construct filter query
        while True:
            query = supabase.table("course_sections").select("*")
            if len(parts) == 2:
                s_name, s_year = parts[0], parts[1]
                # Match both "Fall 2026" and "2026 Fall"
                query = query.or_(f"term.ilike.%{s_name}%{s_year}%,term.ilike.%{s_year}%{s_name}%")
            else:
                query = query.ilike("term", f"%{target_tag.strip()}%")

            res = query.range(p * page_size, (p + 1) * page_size - 1).execute()
            sections.extend(res.data)
            if len(res.data) < page_size:
                break
            p += 1
        return sections

    # Target the requested term directly
    all_sections = fetch_sections_for_tag(clean_term)

    print(f"[+] Loaded {len(all_courses)} courses and {len(all_sections)} sections into memory for '{clean_term}'.")
    return CatalogManager.load_from_supabase_data(all_courses, all_sections)

# -------------------------------------------------------------
# PRE-WARM CATALOG CACHE AT SERVER STARTUP
# -------------------------------------------------------------
if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
    with app.app_context():
        def_season_init, def_year_init = get_default_term_info()
        default_term_init = f"{def_season_init} {def_year_init}"
        for attempt in range(3):
            try:
                load_catalog_for_term(default_term_init)
                print(f"[✓] Supabase catalog pre-warmed successfully for {default_term_init}.")
                break
            except Exception as e:
                print(f"[!] Warning: Catalog attempt {attempt + 1} failed: {e}")
                if attempt == 2:
                    print("[x] Running in fallback mode; will retry on first request.")

#-----------------------------------------------------------------
# Values every render of the page needs. Routes override what changes.
#-----------------------------------------------------------------

def base_template_context(selected_season: str, selected_year: int, selected_term: str) -> dict:
    return dict(
        selected_season=selected_season,
        selected_year=selected_year,
        selected_term=selected_term,
        season_options=list(ALLOWED_SEASONS),
        year_options=term_year_options(),
        completed_courses=[],
        completed_courses_json="[]",
        required_courses=[],
        manual_choice_requirements=[],
        serialized_gen_eds_json="[]",
        candidate_courses=[],
        serialized_reqs_json="[]",
        locked_sections=[],
        locked_sections_json="[]",
        earned_credits=0.0,
        schedule=None,
        schedule_json="[]",
        error_msg=None,
        term_notice=None,
        is_scanned=False,
        is_generated=False,
        selected_days=["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"],
        start_time_val="00:00",
        end_time_val="24:00",
        target_course_count=5,
        ignore_capacity=False,
        ignore_prereqs=False,
        warnings=[],
        no_schedule_reason=None,
    )

#-----------------------------------------------------------------
# Handler for file too large
#-----------------------------------------------------------------

@app.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(e):
    season, year, term = resolve_term(None, None)
    context = base_template_context(season, year, term)
    context["error_msg"] = "The uploaded PDF exceeds the 100 KB file size limit. Please upload a smaller degree audit file."
    return render_template("index.html", **context), 413

#-----------------------------------------------------------------
# Lightweight Health Check Endpoint (Keep-Alive for Render)
#-----------------------------------------------------------------
@app.route("/healthz", methods=["GET"])
def health_check():
    return "OK", 200

#-----------------------------------------------------------------
# Fast Course Search Endpoint (Name and Number Strict Match)
#-----------------------------------------------------------------

@app.route("/api/courses", methods=["GET"])
def search_all_courses():
    query = request.args.get("q", "").strip().lower()
    geneds_param = request.args.get("geneds", "").strip()
    selected_geneds = [normalize_gened(g) for g in geneds_param.split(",") if g.strip()]

    if not query and not selected_geneds:
        return jsonify([])

    term = resolve_term_string(request.args.get("term", ""))
    try:
        catalog = load_catalog_for_term(term)
    except Exception as e:
        print(f"[!] Search failed to load catalog for {term}: {e}")
        return jsonify([])

    results = []
    query_compact = query.replace(" ", "")

    dept_match = re.match(r"^([a-z]+)", query_compact)
    potential_dept = dept_match.group(1).upper() if dept_match else None

    # Search only that department when the query starts with one (e.g. "cs 2")
    search_pool = catalog.dept_index[potential_dept] if potential_dept in catalog.dept_index else catalog.courses.values()

    for course in search_pool:
        dept = course.course_name.strip()
        num = course.course_number.strip()
        full_spaced = f"{dept} {num}".lower()
        full_compact = f"{dept}{num}".lower()
        course_gened = normalize_gened(course.gen_ed)

        text_match = True
        if query:
            text_match = (
                (query in full_spaced) or
                (query_compact in full_compact) or
                (query == dept.lower()) or
                (query == num.lower())
            )

        gened_match = True
        if selected_geneds:
            gened_match = any(target in course_gened for target in selected_geneds)

        if text_match and gened_match:
            secs = getattr(course, "sections", [])
            credits_val = secs[0].credits if (secs and secs[0].credits > 0) else 3

            results.append({
                "code": f"{dept} {num}",
                "subject_code": dept,
                "course_number": num,
                "name": getattr(course, "description", None) or f"{dept} {num}",
                "description": course.description or "No description available.",
                "gen_ed": course.gen_ed or "None",
                "credits": credits_val,
                "sections_count": len(secs),
                "prereqs": "None"
            })
            if len(results) >= 60:
                break

    return jsonify(results)

#-----------------------------------------------------------------
# Construct data for detail card
#-----------------------------------------------------------------

@app.route("/api/course-sections", methods=["GET"])
def get_course_sections_detail():
    course_name = request.args.get("name", "").strip()
    course_number = request.args.get("number", "").strip()
    if not course_name or not course_number:
        return jsonify({"error": "Missing course identifier"}), 400

    term = resolve_term_string(request.args.get("term", ""))
    try:
        catalog = load_catalog_for_term(term)
    except Exception as e:
        print(f"[!] Section lookup failed to load catalog for {term}: {e}")
        return jsonify({"error": "The course catalog couldn't be loaded. Please try again shortly."}), 503

    course_id = f"{course_name}{course_number}"
    course_obj = catalog.get_course(course_id)
    raw_sections = catalog.get_sections(course_id)

    sections_payload = []
    for s in raw_sections:
        sections_payload.append({
            "class_code": s.class_code,
            "section": s.section,
            "term": s.term or term,
            "credits": s.credits,
            "days": s.days or [],
            "start_time": s.formatted_start_time,
            "end_time": s.formatted_end_time,
            "location": s.location or ("Online Asynchronous" if s.is_online else "Room TBA"),
            "delivery_type": s.delivery_type,
            "instructors": s.instructors or ["TBA"],
            "enrolled": s.enrolled if s.enrolled is not None else "N/A",
            "maximum_capacity": s.maximum_capacity if s.maximum_capacity is not None else "N/A",
            "is_full": s.is_full,
            "is_discussion": scheduler.is_discussion_section(s)
        })

    sections_payload.sort(key=lambda x: x["section"])

    return jsonify({
        "course_name": course_name,
        "course_number": course_number,
        "description": getattr(course_obj, "description", "No description available.") or "No description available.",
        "gen_ed": getattr(course_obj, "gen_ed", "None") or "None",
        "prerequisites": prereq_text_for(course_obj),
        "sections": sections_payload
    })

#-----------------------------------------------------------------
# Scans audit
#-----------------------------------------------------------------

def parse_audit_pdf(file_stream):
    audit = []
    with pdfplumber.open(file_stream) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                audit.extend(text.splitlines())

    audit_result = pdf_scanner.build_audit_result(audit)
    completed = sorted(list(audit_result.completed_courses))
    earned_credits = audit_result.earned_credits

    serialized_reqs = []
    for req in audit_result.requirements:
        serialized_reqs.append({
            "group_name": req.group_name,
            "courses_needed": req.courses_needed,
            "units": [u.raw_token for u in req.units]
        })

    serialized_gen_eds = []
    for req in audit_result.gen_ed_requirements:
        serialized_gen_eds.append({
            "group_name": req.group_name,
            "courses_needed": req.courses_needed,
            "units": [u.raw_token for u in req.units]
        })

    return completed, earned_credits, serialized_reqs, serialized_gen_eds

#-----------------------------------------------------------------
# Builds a Section for a course the student added by hand (one that
# isn't in the catalog, e.g. already registered through WISER)
#-----------------------------------------------------------------

def build_custom_section(class_code: str, dept: str, num: str, s_data: dict, c_data: dict, term: str) -> Section:
    try:
        credits = int(float(c_data.get("credits") or 3))
    except (TypeError, ValueError):
        credits = 3

    return Section(
        class_code=class_code,
        course_name=dept,
        course_number=num,
        section=str(s_data.get("section") or "01"),
        days=s_data.get("days") or [],
        start_time=parse_time_value(s_data.get("start_time")),
        end_time=parse_time_value(s_data.get("end_time")),
        location="Self-Enrolled (WISER)",
        maximum_capacity=999,
        enrolled=0,
        instructors=["Custom Instructor"],
        term=term,
        credits=credits
    )

#-----------------------------------------------------------------
# Formats and sends the information into the website
#-----------------------------------------------------------------

@app.route("/", methods=["GET", "POST"])
def index():
    # Prioritize user selections across POST, GET query params, and auto-defaults
    selected_season, selected_year, selected_term = resolve_term(
        request.form.get("selected_season") or request.args.get("season"),
        request.form.get("selected_year") or request.args.get("year"),
    )
    context = base_template_context(selected_season, selected_year, selected_term)

    completed_courses = []
    earned_credits = 0.0
    serialized_reqs = []
    serialized_gen_eds = []
    candidate_courses = []
    locked_sections = []
    locked_sections_json = "[]"
    schedule = None
    schedule_json = "[]"
    error_msg = None
    is_scanned = False
    is_generated = False

    selected_days = context["selected_days"]
    start_time_val = context["start_time_val"]
    end_time_val = context["end_time_val"]
    target_course_count = 5
    ignore_capacity = False
    ignore_prereqs = False

    warnings = []          # Each item: {"msg": str, "courses": [str, ...]}
    no_schedule_reason = None

    def add_warning(msg, items=None):
        warnings.append({"msg": msg, "courses": items or []})

    catalog = None
    try:
        catalog = load_catalog_for_term(selected_term)
    except Exception as e:
        print(f"Warning: Could not load catalog for {selected_term}: {e}")

    if request.method == "GET" and request.args.get("manual") == "true":
        is_scanned = True

    if request.method == "POST":
        action = request.form.get("action", "")

        if action == "manual":
            is_scanned = True

        # -------------------------------------------------------------
        # STEP 1: SCAN AUDIT ONLY
        # -------------------------------------------------------------
        elif action == "scan":
            file = request.files.get("audit_pdf")
            if file and file.filename.lower().endswith(".pdf"):
                try:
                    pdf_bytes = io.BytesIO(file.read())
                    completed_courses, earned_credits, serialized_reqs, serialized_gen_eds = parse_audit_pdf(pdf_bytes)
                    is_scanned = True

                    if catalog:
                        candidate_courses = get_candidate_courses_pool(serialized_reqs, catalog)

                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    error_msg = f"Failed to parse audit PDF: {str(e)}"
            else:
                error_msg = "Please upload a valid .pdf degree audit file."

        # -------------------------------------------------------------
        # STEP 2: CALCULATE & SOLVE WITH USER-SUBMITTED RESTRICTIONS
        # -------------------------------------------------------------
        elif action == "generate":
            is_scanned = True
            is_generated = True

            try:
                serialized_reqs = json.loads(request.form.get("serialized_reqs_json", "[]") or "[]")
                serialized_gen_eds = json.loads(request.form.get("serialized_gen_eds_json", "[]") or "[]")
                completed_courses = json.loads(request.form.get("completed_courses_json", "[]") or "[]")
                try:
                    earned_credits = float(request.form.get("earned_credits", 0) or 0)
                except ValueError:
                    earned_credits = 0.0

                # My Courses, de-duplicated by class number
                try:
                    parsed_locked = json.loads(request.form.get("locked_sections_json", "[]") or "[]")
                except Exception:
                    parsed_locked = []
                seen_codes = set()
                for item in parsed_locked:
                    s_code = str((item.get("section") or {}).get("class_code", ""))
                    if s_code and s_code not in seen_codes:
                        seen_codes.add(s_code)
                        locked_sections.append(item)
                locked_sections_json = json.dumps(locked_sections)

                if not catalog:
                    raise ValueError("The course catalog could not be loaded from the database. Please try again shortly.")

                selected_days = request.form.getlist("allowed_days")
                start_time_input = request.form.get("earliest_start", "").strip()
                end_time_input = request.form.get("latest_end", "").strip()
                ignore_capacity = "ignore_capacity" in request.form
                ignore_prereqs = "ignore_prereqs" in request.form

                raw_target = request.form.get("target_course_count", "5").strip()
                target_course_count = int(raw_target) if raw_target.isdigit() else 5

                start_time_val = start_time_input if start_time_input else "00:00"
                end_time_val = end_time_input if end_time_input else "24:00"

                # The page always submits the pool (main.js keeps this hidden field in
                # sync), so an empty list means the student cleared it on purpose.
                # Only fall back to the audit requirements if the field is missing.
                custom_pool_raw = request.form.get("selected_candidate_courses_json", "").strip()
                pool_submitted = False
                if custom_pool_raw:
                    try:
                        candidate_courses = json.loads(custom_pool_raw)
                        pool_submitted = True
                    except Exception:
                        candidate_courses = []

                if not pool_submitted:
                    candidate_courses = get_candidate_courses_pool(serialized_reqs, catalog)

                # The page sends the pool without section lists; add them back for the dropdowns
                for c in candidate_courses:
                    if not c.get("sections"):
                        cid = f"{c.get('subject_code', '')}{c.get('course_number', '')}"
                        c["sections"] = [s.to_dict() for s in catalog.get_sections(cid)]

                # Prerequisites can only be checked when we know what the student has
                # taken, i.e. after an audit scan (manual mode has no course history).
                completed_set = {normalize_course_id(c) for c in completed_courses}
                has_audit = bool(completed_set) or earned_credits > 0
                check_prereqs = has_audit and not ignore_prereqs
                if not has_audit and not ignore_prereqs:
                    add_warning("Prerequisites aren't checked without a degree audit, so confirm them before you register.")

                # Courses whose prerequisites are unclear; scheduled, but flagged on their card
                review_ids = set()

                prefs = SchedulePreferences(
                    earliest_start=parse_time_value(start_time_val),
                    latest_end=parse_time_value(end_time_val),
                    allowed_days=selected_days if selected_days else None,
                    delivery_mode="Any",
                    ignore_capacity=ignore_capacity
                )

                # 1. Rebuild Section objects for My Courses
                locked_sec_objs = []
                locked_course_ids = set()   # Courses fully covered by My Courses
                custom_codes = set()
                kept_locked = []

                for item in locked_sections:
                    s_data = item.get("section") or {}
                    c_data = item.get("course") or {}
                    class_code_str = str(s_data.get("class_code", ""))
                    is_custom = bool(item.get("is_custom")) or class_code_str.startswith("CUST-")

                    dept_val = str(c_data.get("subject_code") or "CUSTOM").strip().upper()
                    num_val = str(c_data.get("course_number") or "1").strip().upper()
                    course_id = normalize_course_id(f"{dept_val}{num_val}")

                    if is_custom:
                        locked_sec_objs.append(build_custom_section(class_code_str, dept_val, num_val, s_data, c_data, selected_term))
                        custom_codes.add(class_code_str)
                        locked_course_ids.add(course_id)
                        kept_locked.append(item)
                        continue

                    catalog_sections = catalog.get_sections(course_id)
                    matched = next((s for s in catalog_sections if str(s.class_code) == class_code_str), None)
                    if not matched:
                        add_warning(
                            f"{dept_val} {num_val} section #{class_code_str} isn't offered in {selected_term}, "
                            f"so it was removed from My Courses."
                        )
                        continue

                    kept_locked.append(item)
                    locked_sec_objs.append(matched)

                    # A locked lecture still needs its discussion (and vice versa), so only
                    # mark the course as covered when it has no discussion sections
                    if not any(scheduler.is_discussion_section(s) for s in catalog_sections):
                        locked_course_ids.add(course_id)

                    # My Courses are the student's choice, so keep them, but flag unclear prereqs
                    if check_prereqs and prereq_status(course_id, catalog, completed_set, earned_credits) != "met":
                        review_ids.add(course_id)

                locked_sections = kept_locked
                locked_sections_json = json.dumps(locked_sections)

                # Locked sections are never checked by the solver against each other,
                # so flag any pair that overlaps.
                for i in range(len(locked_sec_objs)):
                    for j in range(i + 1, len(locked_sec_objs)):
                        a, b = locked_sec_objs[i], locked_sec_objs[j]
                        if scheduler.intervals_collide(a.days, a.start_time, a.end_time, b.days, b.start_time, b.end_time):
                            add_warning(
                                f"{a.course_name} {a.course_number} (Sec {a.section}) and {b.course_name} {b.course_number} "
                                f"(Sec {b.section}) in My Courses meet at the same time. Remove one and build again."
                            )

                # 2. Complete lecture/discussion pairs. If the student locked only the
                # lecture (or only the discussion), add the missing part first so it
                # always gets a spot, whatever the target number of courses is.
                fixed_sections = list(locked_sec_objs)
                locked_course_map = {}
                for s in locked_sec_objs:
                    if s.class_code in custom_codes:
                        continue
                    c_tag = normalize_course_id(s.course_id)
                    locked_course_map.setdefault(c_tag, []).append(s)

                for c_tag, held_secs in locked_course_map.items():
                    cat_secs = catalog.get_sections(c_tag)
                    if not any(scheduler.is_discussion_section(s) for s in cat_secs):
                        continue
                    has_l = any(not scheduler.is_discussion_section(s) for s in held_secs)
                    has_d = any(scheduler.is_discussion_section(s) for s in held_secs)
                    if has_l and has_d:
                        continue

                    missing = "discussion" if has_l else "lecture"
                    comp_token = f"{c_tag}:DISC_ONLY" if has_l else f"{c_tag}:LEC_ONLY"
                    options = scheduler.get_compatible_unit_options(
                        CourseUnit.from_token(comp_token), catalog, fixed_sections, prefs
                    )
                    if options:
                        fixed_sections.extend(options[0])
                    else:
                        add_warning(
                            f"{format_course_id(c_tag)} also needs a {missing} section, but none fits your days, "
                            f"times, and other courses. Pick one yourself from the course details."
                        )

                # 3. Requirement groups for Options (skipping anything already in My Courses)
                valid_reqs = []
                prereq_skipped = []
                if pool_submitted:
                    for c in candidate_courses:
                        token = normalize_course_id(f"{c['subject_code']}{c['course_number']}")
                        if token in locked_course_map or token in locked_course_ids:
                            continue

                        status = prereq_status(token, catalog, completed_set, earned_credits) if check_prereqs else "met"
                        if status == "unmet":
                            prereq_skipped.append(
                                f"{c['subject_code']} {c['course_number']}: "
                                f"{short_prereq_reason(token, catalog, completed_set, earned_credits)}"
                            )
                            continue
                        if status == "review":
                            review_ids.add(token)

                        if catalog.get_sections(token):
                            valid_reqs.append(
                                RequirementGroup(
                                    group_name=f"{c['subject_code']} {c['course_number']}",
                                    courses_needed=1,
                                    units=[CourseUnit.from_token(token)]
                                )
                            )
                else:
                    reconstructed_reqs = []
                    for item in serialized_reqs:
                        units = []
                        for token in item["units"]:
                            unit = CourseUnit.from_token(token)
                            if check_prereqs:
                                # Keep only the courses in this unit the student is eligible for
                                statuses = {cid: prereq_status(cid, catalog, completed_set, earned_credits) for cid in unit.courses}
                                eligible = [cid for cid in unit.courses if statuses[cid] != "unmet"]
                                review_ids.update(cid for cid in eligible if statuses[cid] == "review")
                                if (unit.unit_type == "AND" and len(eligible) < len(unit.courses)) or not eligible:
                                    prereq_skipped.append(
                                        f"{' / '.join(format_course_id(c) for c in unit.courses)}: "
                                        f"{short_prereq_reason(unit.courses[0], catalog, completed_set, earned_credits)}"
                                    )
                                    continue
                                unit.courses = eligible
                            units.append(unit)
                        if not units:
                            continue
                        reconstructed_reqs.append(
                            RequirementGroup(
                                group_name=item["group_name"],
                                courses_needed=item["courses_needed"],
                                units=units
                            )
                        )
                    valid_reqs = validate_requirements(reconstructed_reqs, catalog)

                if prereq_skipped:
                    count = len(prereq_skipped)
                    add_warning(
                        f"{count} course{'s' if count != 1 else ''} left out because you're missing prerequisites.",
                        prereq_skipped
                    )

                # Explain an empty result with the actual cause instead of a generic message
                if pool_submitted and not candidate_courses and not locked_sections:
                    no_schedule_reason = "You don't have any courses yet. Use \"Add a course\" to pick some, then build again."
                elif not valid_reqs and not fixed_sections:
                    if prereq_skipped:
                        no_schedule_reason = ("You're missing prerequisites for every course in your options. "
                                              "Add different courses, or turn on \"Ignore prerequisite checks\" under More options.")
                    else:
                        no_schedule_reason = f"None of your courses are offered in {selected_term}."

                # 4. Solve. The target is the total number of courses, including My Courses.
                raw_schedule = scheduler.solve_schedule(
                    requirements=valid_reqs,
                    catalog=catalog,
                    target_count=target_course_count,
                    preferences=prefs,
                    fixed_sections=fixed_sections
                )

                schedule = []
                if raw_schedule:
                    for sec in raw_schedule:
                        if sec.class_code in custom_codes or str(sec.class_code).startswith("CUST-"):
                            schedule.append({
                                "class_code": sec.class_code,
                                "course_name": sec.course_name,
                                "course_number": sec.course_number,
                                "section": sec.section,
                                "location": "Self-Enrolled",
                                "instructors": ["N/A"],
                                "days": sec.days or [],
                                "formatted_start_time": sec.formatted_start_time,
                                "formatted_end_time": sec.formatted_end_time,
                                "is_online": sec.is_online,
                                "credits": sec.credits,
                                "gen_ed": "None",
                                "description": "Course you added yourself.",
                                "raw_prerequisites": "None",
                                "prereq_note": "",
                                "is_custom": True
                            })
                            continue

                        course_meta = catalog.get_course(sec.course_id)
                        cid = normalize_course_id(sec.course_id)
                        schedule.append({
                            "class_code": sec.class_code,
                            "course_name": sec.course_name,
                            "course_number": sec.course_number,
                            "section": sec.section,
                            "location": sec.location or ("Online Asynchronous" if sec.is_online else "Room TBA"),
                            "instructors": sec.instructors or ["TBA"],
                            "days": sec.days or [],
                            "formatted_start_time": sec.formatted_start_time,
                            "formatted_end_time": sec.formatted_end_time,
                            "is_online": sec.is_online,
                            "credits": sec.credits,
                            "gen_ed": course_meta.gen_ed if (course_meta and course_meta.gen_ed) else "None",
                            "description": course_meta.description if (course_meta and course_meta.description) else "No description available.",
                            "raw_prerequisites": prereq_text_for(course_meta),
                            # Short "check this" note when prerequisites are unclear (see prereq_status)
                            "prereq_note": short_prereq_reason(cid, catalog, completed_set, earned_credits) if cid in review_ids else "",
                            "is_custom": False
                        })

                    calendar_entries = []
                    for sec in raw_schedule:
                        calendar_entries.append({
                            "course_id": f"{sec.course_name} {sec.course_number}",
                            "section": sec.section,
                            "days": sec.days or [],
                            "start_time": sec.start_time.strftime("%H:%M") if sec.start_time else None,
                            "end_time": sec.end_time.strftime("%H:%M") if sec.end_time else None,
                            "location": sec.location or "Room TBA",
                            "is_online": sec.is_online
                        })
                    schedule_json = json.dumps(calendar_entries)

            except Exception as e:
                import traceback
                traceback.print_exc()
                error_msg = f"Failed to generate schedule: {str(e)}"

    # Tell the student when the semester they picked has no classes loaded yet
    term_notice = None
    if is_scanned:
        if catalog is None:
            term_notice = "The course catalog couldn't be loaded right now. Please try again in a minute."
        elif not catalog.sections:
            term_notice = (f"{selected_term} classes aren't in ClassPick yet, so there's nothing to schedule. "
                           f"Start over and pick a different semester, or check back later.")

    # Split requirements: specific courses (go to Options) vs. ones the
    # student has to pick themselves (wildcards like "any CS 300-level" and Gen Eds)
    display_required = []
    manual_choice_requirements = []

    for req in serialized_reqs:
        units = req.get("units", [])
        needed = req.get("courses_needed", 1)
        wildcards = [u for u in units if is_wildcard_token(u)]
        regular = [u for u in units if not is_wildcard_token(u)]

        if regular:
            display_required.append({"needed": needed, "options": regular})

        # Only list it here when nothing in Options can cover it. If the group also
        # has specific courses, those are already in Options as the way to fill it.
        if wildcards and not regular:
            label = " or ".join(describe_wildcards(wildcards))
            manual_choice_requirements.append({
                "group_name": label,
                "needed": needed,
                "is_gened": False
            })

    for req in serialized_gen_eds:
        clean_name = req.get("group_name", "General Education").replace("Gen Ed: ", "").strip()
        for token in req.get("units", []):
            if isinstance(token, str) and token.startswith("GENED:"):
                raw_tag = token.split("GENED:", 1)[1].strip()
                clean_name = GENED_TOKEN_MAP.get(raw_tag, raw_tag.replace("_", " ").title())
                break

        manual_choice_requirements.append({
            "group_name": clean_name,
            "needed": req.get("courses_needed", 1),
            "is_gened": True
        })

    context.update(
        completed_courses=completed_courses,
        completed_courses_json=json.dumps(completed_courses),
        required_courses=display_required,
        manual_choice_requirements=manual_choice_requirements,
        serialized_gen_eds_json=json.dumps(serialized_gen_eds),
        candidate_courses=candidate_courses,
        serialized_reqs_json=json.dumps(serialized_reqs),
        locked_sections=locked_sections,
        locked_sections_json=locked_sections_json,
        earned_credits=earned_credits,
        schedule=schedule,
        schedule_json=schedule_json,
        error_msg=error_msg,
        term_notice=term_notice,
        is_scanned=is_scanned,
        is_generated=is_generated,
        selected_days=selected_days,
        start_time_val=start_time_val,
        end_time_val=end_time_val,
        target_course_count=target_course_count,
        ignore_capacity=ignore_capacity,
        ignore_prereqs=ignore_prereqs,
        warnings=warnings,
        no_schedule_reason=no_schedule_reason,
    )
    return render_template("index.html", **context)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
