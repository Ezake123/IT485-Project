import os
import io
import json
import re
from datetime import datetime, time
from functools import lru_cache
from flask import Flask, render_template, request, redirect, jsonify
from dotenv import load_dotenv
from supabase import create_client, Client
import pdfplumber
from werkzeug.exceptions import RequestEntityTooLarge
from models import CatalogManager, SchedulePreferences, CourseUnit, RequirementGroup, Section, parse_time_value, GENED_TOKEN_MAP
import pdf_scanner
import scheduler
try:
    from flask_compress import Compress
except ImportError:
    Compress = None
#-----------------------------------------------------------------
# Description:
# Connects all the scripts and website together
# 
# Requirement:
# Verify if all these are installed:
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
# have a folder named templates with the website inside and the static. 
# Next go to your web browser and enter "http://127.0.0.1:5000" 
#
# Developed with assistance from Google Gemini using agentic workflows
#-----------------------------------------------------------------

load_dotenv()

app = Flask(__name__)
if Compress:
    Compress(app)

# 100 KB upload limit for degree audits
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if (SUPABASE_URL and SUPABASE_KEY) else None

PAGE_SIZE = 1000

#-----------------------------------------------------------------
# Calculates the active semester default (excludes summer/winter)
#-----------------------------------------------------------------
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
# Prerequisite & String Normalization Helpers
#-----------------------------------------------------------------
PREREQ_ALTERNATIVE_RE = re.compile(
    r"permission|consent|approval|instructor|placement|aleks|score|equivalent|"
    r"or higher|may not register|class note|see note|department",
    re.IGNORECASE,
)

def normalize_course_id(cid: str) -> str:
    return str(cid).replace(" ", "").strip().upper()

def format_course_id(cid: str) -> str:
    m = re.match(r"^([A-Z]+)(\d.*)$", normalize_course_id(cid))
    return f"{m.group(1)} {m.group(2)}" if m else cid

def prereq_text_for(course_obj) -> str:
    if not course_obj or not getattr(course_obj, "prerequisites", None):
        return "None"
    raw = course_obj.prerequisites
    if isinstance(raw, dict):
        return raw.get("raw") or "None"
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed.get("raw", raw) if isinstance(parsed, dict) else raw
        except Exception:
            return raw
    return "None"

def prereq_status(course_id: str, catalog: CatalogManager, completed_set, earned_credits) -> str:
    course_obj = catalog.get_course(course_id)
    if not course_obj:
        return "met"
    try:
        if course_obj.prerequisites_met(completed_set, earned_credits):
            return "met"
    except Exception:
        return "met"
    if PREREQ_ALTERNATIVE_RE.search(prereq_text_for(course_obj)):
        return "review"
    return "unmet"

def short_prereq_reason(course_id: str, catalog: CatalogManager, completed_set, earned_credits) -> str:
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
# Optimized Database Fetching & Caching
#-----------------------------------------------------------------
@lru_cache(maxsize=1)
def fetch_all_courses_catalog() -> list[dict]:
    """Caches base catalog course definitions across terms to prevent redundant fetches."""
    if not supabase:
        raise ValueError("Missing SUPABASE_URL or SUPABASE_KEY.")
    courses = []
    page = 0
    while True:
        res = supabase.table("courses").select("*").range(page * PAGE_SIZE, (page + 1) * PAGE_SIZE - 1).execute()
        courses.extend(res.data)
        if len(res.data) < PAGE_SIZE:
            break
        page += 1
    return courses

@lru_cache(maxsize=8)
def load_catalog_for_term(term: str = None) -> CatalogManager:
    """Loads sections for the specific term and combines with catalog definitions."""
    if not supabase:
        raise ValueError("Missing SUPABASE_URL or SUPABASE_KEY.")

    def_season, def_year = get_default_term_info()
    clean_term = (term or f"{def_season} {def_year}").strip()
    print(f"[*] Querying Supabase sections for term: '{clean_term}'...")

    all_courses = fetch_all_courses_catalog()

    # Query sections matching term variants
    parts = clean_term.split()
    sections = []
    page = 0
    while True:
        query = supabase.table("course_sections").select("*")
        if len(parts) == 2:
            s_name, s_year = parts[0], parts[1]
            query = query.or_(f"term.ilike.%{s_name}%{s_year}%,term.ilike.%{s_year}%{s_name}%")
        else:
            query = query.ilike("term", f"%{clean_term}%")

        res = query.range(page * PAGE_SIZE, (page + 1) * PAGE_SIZE - 1).execute()
        sections.extend(res.data)
        if len(res.data) < PAGE_SIZE:
            break
        page += 1

    print(f"[+] Loaded {len(all_courses)} courses and {len(sections)} sections into memory for '{clean_term}'.")
    return CatalogManager.load_from_supabase_data(all_courses, sections)

# Pre-warm catalog cache at startup
if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
    with app.app_context():
        init_season, init_year = get_default_term_info()
        default_term_init = f"{init_season} {init_year}"
        for attempt in range(3):
            try:
                load_catalog_for_term(default_term_init)
                print(f"[✓] Supabase catalog pre-warmed successfully for {default_term_init}.")
                break
            except Exception as e:
                print(f"[!] Warning: Pre-warm attempt {attempt + 1} failed: {e}")

#-----------------------------------------------------------------
# Extracts candidate courses pool grouped by audit requirement
#-----------------------------------------------------------------
def get_candidate_courses_pool(serialized_reqs, catalog: CatalogManager):
    seen_ids = set()
    candidate_list = []

    def format_sections_payload(raw_secs):
        return [s.to_dict() for s in raw_secs]

    for req_idx, req in enumerate(serialized_reqs, start=1):
        group_name = req.get("group_name", f"Requirement {req_idx}")
        needed = req.get("courses_needed", 1)
        group_id = f"req-{req_idx:02d}"

        group_meta = {
            "req_id": group_id,
            "req_group_name": group_name,
            "req_needed": needed,
            "group_label": f"Choose {needed} from {group_name}" if needed > 1 else f"Choose 1 from {group_name}"
        }

        for unit_token in req.get("units", []):
            if unit_token.startswith("GENED:"):
                raw_tag = unit_token.split("GENED:", 1)[1].strip()
                target_gened = GENED_TOKEN_MAP.get(raw_tag, raw_tag.replace("_", " ")).lower()

                for _, course in catalog.courses.items():
                    if course.gen_ed and target_gened in course.gen_ed.lower():
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
                                    "sections": format_sections_payload(sections),
                                    **group_meta
                                })
                continue

            unit = CourseUnit.from_token(unit_token)
            target_ids = getattr(unit, "courses", [unit.raw_token])

            for cid in target_ids:
                cid_clean = cid.strip()
                if cid_clean not in seen_ids:
                    seen_ids.add(cid_clean)
                    course_obj = catalog.get_course(cid_clean)
                    sections = catalog.get_sections(cid_clean)

                    if not sections and hasattr(course_obj, "sections"):
                        sections = course_obj.sections

                    if course_obj and sections:
                        credits_val = sections[0].credits if hasattr(sections[0], "credits") else getattr(course_obj, "credits", 3)
                        raw_cname = getattr(course_obj, "course_name", cid_clean).strip()
                        raw_cnum = getattr(course_obj, "course_number", "").strip()
                        dept_clean = re.sub(r'\d+[A-Za-z]?$', '', raw_cname).strip() if raw_cnum and raw_cname.endswith(raw_cnum) else raw_cname

                        candidate_list.append({
                            "subject_code": dept_clean if dept_clean else cid_clean,
                            "course_number": raw_cnum,
                            "name": getattr(course_obj, "description", raw_cname) or raw_cname,
                            "credits": credits_val,
                            "sections_count": len(sections),
                            "sections": format_sections_payload(sections),
                            **group_meta
                        })

    candidate_list.sort(key=lambda c: (c.get("req_id", ""), c["subject_code"], c["course_number"]))
    return candidate_list

#-----------------------------------------------------------------
# Degree Audit Scanning & Processing
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

    serialized_reqs = [
        {
            "group_name": req.group_name,
            "courses_needed": req.courses_needed,
            "units": [u.raw_token for u in req.units]
        }
        for req in audit_result.requirements
    ]

    serialized_gen_eds = [
        {
            "group_name": req.group_name,
            "courses_needed": req.courses_needed,
            "units": [u.raw_token for u in req.units]
        }
        for req in audit_result.gen_ed_requirements
    ]

    return completed, earned_credits, serialized_reqs, serialized_gen_eds

LANGUAGE_DEPTS = {
    "ARABIC", "CHINSE", "FRENCH", "GERMAN", "ITAL", 
    "JAPAN", "PORT", "RUSS", "SPAN", "ARMEN", "VIET", "LATIN", "GREEK"
}

def describe_wildcards(tokens: list[str]) -> list[str]:
    levels_by_dept = {}
    ranges = []
    other_tokens = []

    for tok in tokens:
        clean = str(tok).replace(" ", "").upper()
        level = re.match(r"^([A-Z]+)([1-5])(?:XX)?$", clean)
        if level:
            levels_by_dept.setdefault(level.group(1), set()).add(int(level.group(2)))
            continue
        rng = re.match(r"^([A-Z]+)(\d+[A-Z]?)-([A-Z]*)(\d+[A-Z]?)$", clean)
        if rng:
            dept1, num1, dept2, num2 = rng.groups()
            ranges.append(f"{dept1} {num1}–{num2}")
            continue
        other_tokens.append(tok)

    lang_depts = [d for d in levels_by_dept if d in LANGUAGE_DEPTS]
    non_lang_depts = {d: lvls for d, lvls in levels_by_dept.items() if d not in LANGUAGE_DEPTS}

    parts = []
    if len(lang_depts) >= 3:
        all_levels = set()
        for d in lang_depts:
            all_levels.update(levels_by_dept[d])
        lvl_sorted = sorted(all_levels)
        min_lvl = f"{lvl_sorted[0]}00"
        max_lvl = f"{lvl_sorted[-1]}00"
        level_phrase = f"{min_lvl}–{max_lvl}-level" if len(lvl_sorted) > 1 else f"{min_lvl}-level"
        parts.append(f"Any {level_phrase} World Language course")
    else:
        for d in lang_depts:
            lvl_sorted = sorted(levels_by_dept[d])
            level_text = f"{lvl_sorted[0]}00-level" if len(lvl_sorted) == 1 else f"{lvl_sorted[0]}00–{lvl_sorted[-1]}00-level"
            parts.append(f"Any {level_text} {d} course")

    for dept, levels in non_lang_depts.items():
        lvl_sorted = sorted(levels)
        level_text = f"{lvl_sorted[0]}00-level" if len(lvl_sorted) == 1 else f"{lvl_sorted[0]}00–{lvl_sorted[-1]}00-level"
        parts.append(f"Any {level_text} {dept} course")

    return parts + ranges + other_tokens

#-----------------------------------------------------------------
# API Endpoints
#-----------------------------------------------------------------
@app.route("/healthz", methods=["GET"])
def health_check():
    return "OK", 200

@app.route("/api/courses", methods=["GET"])
def search_all_courses():
    query = request.args.get("q", "").strip().lower()
    geneds_param = request.args.get("geneds", "").strip()
    term_param = request.args.get("term", "").strip()
    selected_geneds = [g.strip().lower() for g in geneds_param.split(",") if g.strip()]

    if not query and not selected_geneds:
        return jsonify([])

    def_season, def_year = get_default_term_info()
    term = term_param if term_param else f"{def_season} {def_year}"
    catalog = load_catalog_for_term(term)
    results = []
    query_compact = query.replace(" ", "")

    dept_match = re.match(r"^([a-z]+)", query_compact)
    potential_dept = dept_match.group(1).upper() if dept_match else None

    search_pool = (
        catalog.dept_index.get(potential_dept, catalog.courses.values()) 
        if potential_dept in catalog.dept_index else catalog.courses.values()
    )

    for course in search_pool:
        dept = course.course_name.strip()
        num = course.course_number.strip()
        full_spaced = f"{dept} {num}".lower()
        full_compact = f"{dept}{num}".lower()
        course_gened = (course.gen_ed or "").strip().lower()

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
            credits_val = secs[0].credits if (secs and hasattr(secs[0], "credits") and secs[0].credits > 0) else 3

            results.append({
                "code": f"{dept} {num}",
                "subject_code": dept,
                "course_number": num,
                "name": getattr(course, "description", None) or f"{dept} {num}",
                "description": course.description or "No description available.",
                "gen_ed": course.gen_ed or "None",
                "credits": credits_val,
                "sections_count": len(secs),
                "prereqs": prereq_text_for(course)
            })
            if len(results) >= 120:
                break

    return jsonify(results)

@app.route("/api/course-sections", methods=["GET"])
def get_course_sections_detail():
    course_name = request.args.get("name", "").strip()
    course_number = request.args.get("number", "").strip()
    term_param = request.args.get("term", "").strip()
    if not course_name or not course_number:
        return jsonify({"error": "Missing course identifier"}), 400

    def_season, def_year = get_default_term_info()
    term = term_param if term_param else f"{def_season} {def_year}"
    catalog = load_catalog_for_term(term)

    course_id = f"{course_name}{course_number}"
    course_obj = catalog.get_course(course_id)
    raw_sections = catalog.get_sections(course_id)

    # Filter sections by composite term identity
    target_clean = term.strip().lower()
    sections_payload = []
    for s in raw_sections:
        if s.term and target_clean not in s.term.lower() and s.term.lower() not in target_clean:
            continue
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
            "status": s.status,
            "is_full": s.is_full
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
# Web Views & Schedule Solver Controller
#-----------------------------------------------------------------
@app.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(e):
    def_season, def_year = get_default_term_info()
    return render_template(
        "planner.html",
        selected_season=def_season,
        selected_year=def_year,
        selected_term=f"{def_season} {def_year}",
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
        error_msg="The uploaded PDF exceeds the 100 KB limit. Please upload a smaller audit file.",
        is_scanned=False,
        is_generated=False,
        selected_days=["Mo", "Tu", "We", "Th", "Fr"],
        start_time_val="03:00",
        end_time_val="23:59",
        target_course_count=5,
        ignore_capacity=False,
        ignore_prereqs=True,
        exclude_online=False
    ), 413

@app.route("/", methods=['GET'])
def index():
    return render_template('index.html')

@app.route("/planner", methods=["GET", "POST"])
def planner():
    def_season, def_year = get_default_term_info()

    selected_season = request.form.get("selected_season") or request.args.get("season") or def_season
    selected_year = request.form.get("selected_year") or request.args.get("year") or str(def_year)
    selected_term = f"{selected_season} {selected_year}"

    completed_courses = []
    earned_credits = 0.0
    serialized_reqs = []
    display_required = []
    manual_choice_requirements = []
    serialized_gen_eds = []
    candidate_courses = []
    locked_sections = []
    locked_sections_json = "[]"
    schedule = None
    schedule_json = "[]"
    error_msg = None
    is_scanned = False
    is_generated = False
    term_notice = None

    selected_days = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
    start_time_val = "00:00"
    end_time_val = "24:00"
    target_course_count = 5
    ignore_capacity = False
    ignore_prereqs = True
    exclude_online = False
    warnings = []

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

        elif action == "scan":
            file = request.files.get("audit_pdf")
            if file and file.filename.endswith(".pdf"):
                try:
                    pdf_bytes = io.BytesIO(file.read())
                    completed_courses, earned_credits, serialized_reqs, serialized_gen_eds = parse_audit_pdf(pdf_bytes)
                    is_scanned = True
                    if catalog:
                        candidate_courses = get_candidate_courses_pool(serialized_reqs, catalog)
                except Exception as e:
                    error_msg = f"Failed to parse audit PDF: {str(e)}"
            else:
                error_msg = "Please upload a valid .pdf degree audit file."

        elif action == "generate":
            raw_reqs_json = request.form.get("serialized_reqs_json", "[]")
            completed_json = request.form.get("completed_courses_json", "[]")
            earned_credits = float(request.form.get("earned_credits", 0.0))
            raw_gen_eds_json = request.form.get("serialized_gen_eds_json", "[]")
            serialized_gen_eds = json.loads(raw_gen_eds_json)

            raw_locked_json = request.form.get("locked_sections_json", "[]")
            try:
                parsed_locked = json.loads(raw_locked_json)
                seen_codes = set()
                locked_sections = []
                for item in parsed_locked:
                    s_code = str(item.get("section", {}).get("class_code", ""))
                    if s_code and s_code not in seen_codes:
                        seen_codes.add(s_code)
                        locked_sections.append(item)
                locked_sections_json = json.dumps(locked_sections)
            except Exception:
                locked_sections = []
                locked_sections_json = "[]"

            try:
                serialized_reqs = json.loads(raw_reqs_json)
                completed_courses = json.loads(completed_json)
                is_scanned = True
                is_generated = True

                selected_days = request.form.getlist("allowed_days")
                start_time_val = request.form.get("earliest_start", "").strip() or "00:00"
                end_time_val = request.form.get("latest_end", "").strip() or "24:00"

                raw_target = request.form.get("target_course_count", "5").strip()
                target_course_count = int(raw_target) if raw_target.isdigit() else 5

                ignore_capacity = "ignore_capacity" in request.form
                ignore_prereqs = "ignore_prereqs" in request.form
                exclude_online = "exclude_online" in request.form

                completed_set = {normalize_course_id(c) for c in completed_courses}
                check_prereqs = (bool(completed_set) or earned_credits > 0) and not ignore_prereqs

                custom_pool_raw = request.form.get("selected_candidate_courses_json", "")
                if custom_pool_raw:
                    try:
                        candidate_courses = json.loads(custom_pool_raw)
                    except Exception:
                        pass

                if not candidate_courses and catalog:
                    candidate_courses = get_candidate_courses_pool(serialized_reqs, catalog)

                if catalog and candidate_courses:
                    for c in candidate_courses:
                        if "sections" not in c or not c["sections"]:
                            cid = f"{c.get('subject_code', '')}{c.get('course_number', '')}"
                            c["sections"] = [s.to_dict() for s in catalog.get_sections(cid)]

                locked_sec_objs = []
                locked_course_ids = set()
                custom_card_metadata = {}

                for item in locked_sections:
                    s_data = item.get("section", {})
                    c_data = item.get("course", {})
                    class_code_str = str(s_data.get("class_code", ""))
                    is_custom = item.get("is_custom", False) or class_code_str.startswith("CUST-")

                    dept_val = str(c_data.get("subject_code", "CUSTOM")).strip()
                    num_val = str(c_data.get("course_number", "1")).strip()
                    course_id = f"{dept_val}{num_val}".replace(" ", "").upper()

                    if is_custom:
                        locked_course_ids.add(course_id)
                        custom_sec = Section(
                            class_code=class_code_str,
                            course_name=dept_val,
                            course_number=num_val,
                            section=str(s_data.get("section", "01")),
                            days=s_data.get("days") or [],
                            start_time=parse_time_value(s_data.get("start_time")),
                            end_time=parse_time_value(s_data.get("end_time")),
                            location="Self-Enrolled (WISER)",
                            status="Open",
                            instructors=["Custom Instructor"],
                            term=selected_term,
                            credits=int(c_data.get("credits", 3))
                        )
                        locked_sec_objs.append(custom_sec)
                        custom_card_metadata[custom_sec.class_code] = True
                    else:
                        cat_secs = catalog.get_sections(course_id)
                        has_discussions = any(scheduler.is_discussion_section(s) for s in cat_secs)
                        if not has_discussions:
                            locked_course_ids.add(course_id)

                        matched = next((s for s in cat_secs if str(s.class_code) == class_code_str), None)
                        if matched:
                            locked_sec_objs.append(matched)
                        else:
                            fallback_sec = Section(
                                class_code=class_code_str,
                                course_name=dept_val,
                                course_number=num_val,
                                section=str(s_data.get("section", "01")),
                                days=s_data.get("days") or [],
                                start_time=parse_time_value(s_data.get("start_time")),
                                end_time=parse_time_value(s_data.get("end_time")),
                                location=s_data.get("location") or "Room TBA",
                                status="Open",
                                instructors=s_data.get("instructors") or ["TBA"],
                                term=selected_term,
                                credits=int(c_data.get("credits", 3))
                            )
                            locked_sec_objs.append(fallback_sec)

                prefs = SchedulePreferences(
                    earliest_start=parse_time_value(start_time_val),
                    latest_end=parse_time_value(end_time_val),
                    allowed_days=selected_days if selected_days else None,
                    delivery_mode="In-person" if exclude_online else "Any",
                    ignore_capacity=ignore_capacity,
                    exclude_online=exclude_online
                )

                priority_reqs = []
                pool_reqs = []

                # Handle lecture/discussion pairing when one component is locked
                locked_course_map = {}
                for s in locked_sec_objs:
                    c_tag = f"{s.course_name.strip()}{s.course_number.strip()}".upper()
                    locked_course_map.setdefault(c_tag, {"sections": [], "dept": s.course_name.strip(), "num": s.course_number.strip()})
                    locked_course_map[c_tag]["sections"].append(s)

                for c_tag, data in locked_course_map.items():
                    cat_secs = catalog.get_sections(c_tag)
                    has_disc_in_catalog = any(scheduler.is_discussion_section(s) for s in cat_secs)

                    if has_disc_in_catalog:
                        held_secs = data["sections"]
                        has_l = any(not scheduler.is_discussion_section(s) for s in held_secs)
                        has_d = any(scheduler.is_discussion_section(s) for s in held_secs)

                        if (has_l and not has_d) or (has_d and not has_l):
                            missing_comp = "Discussion" if has_l else "Lecture"
                            comp_token = f"{c_tag}:DISC_ONLY" if has_l else f"{c_tag}:LEC_ONLY"
                            unit = CourseUnit.from_token(comp_token)
                            priority_reqs.append(
                                RequirementGroup(
                                    group_name=f"{data['dept']} {data['num']} (Required {missing_comp})",
                                    courses_needed=1,
                                    units=[unit]
                                )
                            )

                if candidate_courses:
                    locked_courses_per_group = {}
                    for item in locked_sections:
                        c_meta = item.get("course") or {}
                        gid = c_meta.get("req_id")
                        if gid:
                            cid = normalize_course_id(f"{c_meta.get('subject_code')}{c_meta.get('course_number')}")
                            locked_courses_per_group.setdefault(gid, set()).add(cid)

                    grouped_units = {}
                    group_meta = {}

                    for c in candidate_courses:
                        token = normalize_course_id(f"{c['subject_code']}{c['course_number']}")
                        if token in locked_course_map or token in locked_course_ids:
                            continue

                        status = prereq_status(token, catalog, completed_set, earned_credits) if check_prereqs else "met"
                        if status == "unmet":
                            warnings.append(f"{c['subject_code']} {c['course_number']}: {short_prereq_reason(token, catalog, completed_set, earned_credits)}")
                            continue

                        unit = CourseUnit.from_token(token)
                        if catalog.get_sections(token):
                            gid = c.get("req_id") or "user-added"
                            grouped_units.setdefault(gid, []).append(unit)
                            if gid not in group_meta:
                                group_meta[gid] = {
                                    "name": c.get("req_group_name") or ("Added Courses" if gid == "user-added" else f"{c['subject_code']} {c['course_number']}"),
                                    "needed": int(c.get("req_needed") or 1)
                                }

                    for gid, units in grouped_units.items():
                        locked_count = len(locked_courses_per_group.get(gid, set()))
                        net_needed = len(units) if gid == "user-added" else max(0, group_meta[gid]["needed"] - locked_count)

                        if net_needed > 0 and units:
                            pool_reqs.append(
                                RequirementGroup(
                                    group_name=group_meta[gid]["name"],
                                    courses_needed=min(net_needed, len(units)),
                                    units=units
                                )
                            )

                valid_reqs = priority_reqs + pool_reqs

                raw_schedule = scheduler.solve_schedule(
                    requirements=valid_reqs,
                    catalog=catalog,
                    target_count=target_course_count,
                    preferences=prefs,
                    fixed_sections=locked_sec_objs
                )

                schedule = []
                if raw_schedule:
                    for sec in raw_schedule:
                        is_custom_course = custom_card_metadata.get(sec.class_code, False) or str(sec.class_code).startswith("CUST-")
                        if is_custom_course:
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
                                "description": "Custom user-added course.",
                                "raw_prerequisites": "None",
                                "is_custom": True
                            })
                        else:
                            course_meta = catalog.get_course(sec.course_id)
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
                                "is_custom": False
                            })

                    calendar_entries = [
                        {
                            "course_id": f"{sec.course_name} {sec.course_number}",
                            "section": sec.section,
                            "days": sec.days or [],
                            "start_time": sec.start_time.strftime("%H:%M") if sec.start_time else None,
                            "end_time": sec.end_time.strftime("%H:%M") if sec.end_time else None,
                            "location": sec.location or "Room TBA",
                            "is_online": sec.is_online
                        }
                        for sec in raw_schedule
                    ]
                    schedule_json = json.dumps(calendar_entries)

            except Exception as e:
                error_msg = f"Failed to generate schedule: {str(e)}"

    def is_wildcard_token(tok: str) -> bool:
        clean = tok.strip()
        if re.search(r'^[A-Za-z]+\s*\d+-\d+', clean):
            return True
        if re.match(r'^[A-Za-z]+\s*[1-5](xx)?$', clean, re.IGNORECASE):
            return True
        return False

    # Process audit display structures (drops wildcard duplication when concrete options exist)
    for req in serialized_reqs:
        units = req.get("units", [])
        needed = req.get("courses_needed", 1)

        wildcards = [u for u in units if is_wildcard_token(u)]
        regular = [u for u in units if not is_wildcard_token(u)]

        if regular:
            display_required.append({
                "needed": needed,
                "options": regular
            })
        elif wildcards:
            display_title = " or ".join(describe_wildcards(wildcards))
            manual_choice_requirements.append({
                "group_name": display_title,
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

    if catalog is None:
        term_notice = "The course catalog couldn't be loaded right now. Please try again in a minute."
    elif not catalog.sections:
        term_notice = f"{selected_term} classes aren't in ClassPick yet, so there's nothing to schedule."

    return render_template(
        "planner.html",
        selected_season=selected_season,
        selected_year=int(selected_year),
        selected_term=selected_term,
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
        is_scanned=is_scanned,
        is_generated=is_generated,
        selected_days=selected_days,
        start_time_val=start_time_val,
        end_time_val=end_time_val,
        target_course_count=target_course_count,
        ignore_capacity=ignore_capacity,
        ignore_prereqs=ignore_prereqs,
        exclude_online=exclude_online,
        warnings=warnings,
        term_notice=term_notice
    )

if __name__ == "__main__":
    app.run(debug=True, port=5000)