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
from flask_compress import Compress

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
# have a folder named templates with the website inside. Next go
# to your web browser and enter "http://127.0.0.1:5000" 
#
# Developed with assistance from Google Gemini using agentic workflows
#-----------------------------------------------------------------

load_dotenv()

app = Flask(__name__)
Compress(app)

# Enforce a 100 KB maximum upload limit since most degree audit can't even get over 50KB
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 

# Initialize Supabase client
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if (SUPABASE_URL and SUPABASE_KEY) else None

#-----------------------------------------------------------------
# Calculate the default semester, excludes winter and summer 
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
# Extracts unique offered courses from serialized requirements
#-----------------------------------------------------------------

def get_candidate_courses_pool(serialized_reqs, catalog: CatalogManager):
    seen_ids = set()
    candidate_list = []

    def format_sections_payload(raw_secs):
        return [s.to_dict() for s in raw_secs]

    for req in serialized_reqs:
        for unit_token in req.get("units", []):
            if unit_token.startswith("GENED:"):
                raw_tag = unit_token.split("GENED:", 1)[1].strip()
                target_gened = GENED_TOKEN_MAP.get(raw_tag, raw_tag.replace("_", " ")).lower()

                for course_id, course in catalog.courses.items():
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
                                    "sections": format_sections_payload(sections)
                                })
                continue

            # Standard course units
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
                            "sections": format_sections_payload(sections)
                        })

    candidate_list.sort(key=lambda c: (c["subject_code"], c["course_number"]))
    return candidate_list

#-----------------------------------------------------------------
# Handler for file too large
#-----------------------------------------------------------------

@app.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(e):
    def_season, def_year = get_default_term_info()
    return render_template(
        "index.html",
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
        error_msg="The uploaded PDF exceeds the 100 KB file size limit. Please upload a smaller degree audit file.",
        is_scanned=False,
        is_generated=False,
        selected_days=["Mo", "Tu", "We", "Th", "Fr"],
        start_time_val="03:00",
        end_time_val="23:59",
        target_course_count=5,
        ignore_capacity=False
    ), 413

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
# Finds courses by term/semester (Resilient Fallback)
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

    search_pool = catalog.dept_index.get(potential_dept, catalog.courses.values()) if potential_dept in catalog.dept_index else catalog.courses.values()

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
    term_param = request.args.get("term", "").strip()
    if not course_name or not course_number:
        return jsonify({"error": "Missing course identifier"}), 400

    def_season, def_year = get_default_term_info()
    term = term_param if term_param else f"{def_season} {def_year}"
    catalog = load_catalog_for_term(term)

    course_id = f"{course_name}{course_number}"
    course_obj = catalog.get_course(course_id)
    raw_sections = catalog.get_sections(course_id)

    raw_prereq_source = course_obj.prerequisites if course_obj else None
    prereq_text = "None"
    if raw_prereq_source:
        if isinstance(raw_prereq_source, dict):
            prereq_text = raw_prereq_source.get("raw") or "None"
        elif isinstance(raw_prereq_source, str):
            try:
                parsed = json.loads(raw_prereq_source)
                prereq_text = parsed.get("raw", raw_prereq_source) if isinstance(parsed, dict) else raw_prereq_source
            except Exception:
                prereq_text = raw_prereq_source

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
            "is_full": s.is_full
        })

    sections_payload.sort(key=lambda x: x["section"])

    return jsonify({
        "course_name": course_name,
        "course_number": course_number,
        "description": getattr(course_obj, "description", "No description available.") or "No description available.",
        "gen_ed": getattr(course_obj, "gen_ed", "None") or "None",
        "prerequisites": prereq_text,
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
# Formats and sends the information into the website 
#-----------------------------------------------------------------

@app.route("/", methods=["GET", "POST"])
def index():
    def_season, def_year = get_default_term_info()

    # Prioritize user selections across POST, GET query params, and auto-defaults
    selected_season = request.form.get("selected_season") or request.args.get("season") or def_season
    selected_year = request.form.get("selected_year") or request.args.get("year") or str(def_year)
    selected_term = f"{selected_season} {selected_year}"

    completed_courses = []
    earned_credits = 0.0
    serialized_reqs = []
    display_required = []
    display_gen_eds = []
    serialized_gen_eds = []
    candidate_courses = []
    locked_sections = []
    locked_sections_json = "[]"
    schedule = None
    schedule_json = "[]"
    error_msg = None
    is_scanned = False
    is_generated = False

    selected_days = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
    start_time_val = "00:00"
    end_time_val = "24:00"
    target_course_count = 5
    ignore_capacity = False

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
            if file and file.filename.endswith(".pdf"):
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
                start_time_input = request.form.get("earliest_start", "").strip()
                end_time_input = request.form.get("latest_end", "").strip()
                ignore_capacity = "ignore_capacity" in request.form
                
                raw_target = request.form.get("target_course_count", "5").strip()
                target_course_count = int(raw_target) if raw_target.isdigit() else 5

                start_time_val = start_time_input if start_time_input else "00:00"
                end_time_val = end_time_input if end_time_input else "24:00"

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
                    else:
                        cat_secs = catalog.get_sections(course_id) or catalog.get_sections(f"{dept_val} {num_val}")
                        has_discussions = any(scheduler.is_discussion_section(s) for s in cat_secs)
                        if not has_discussions:
                            locked_course_ids.add(course_id)

                    if is_custom:
                        custom_sec = Section(
                            class_code=class_code_str,
                            course_name=dept_val,
                            course_number=num_val,
                            section=str(s_data.get("section", "01")),
                            days=s_data.get("days") or [],
                            start_time=parse_time_value(s_data.get("start_time")),
                            end_time=parse_time_value(s_data.get("end_time")),
                            location="Self-Enrolled (WISER)",
                            maximum_capacity=999,
                            enrolled=0,
                            instructors=["Custom Instructor"],
                            term=selected_term,
                            credits=int(c_data.get("credits", 3))
                        )
                        locked_sec_objs.append(custom_sec)
                        custom_card_metadata[custom_sec.class_code] = True
                    else:
                        catalog_sections = catalog.get_sections(course_id)
                        if not catalog_sections:
                            catalog_sections = catalog.get_sections(f"{dept_val} {num_val}")

                        matched = next((s for s in catalog_sections if str(s.class_code) == class_code_str), None)
                        if matched:
                            locked_sec_objs.append(matched)

                prefs = SchedulePreferences(
                    earliest_start=parse_time_value(start_time_val),
                    latest_end=parse_time_value(end_time_val),
                    allowed_days=selected_days if selected_days else None,
                    delivery_mode="Any",
                    ignore_capacity=ignore_capacity
                )

                priority_reqs = []
                pool_reqs = []

                locked_course_map = {}
                for s in locked_sec_objs:
                    c_tag = f"{s.course_name.strip()}{s.course_number.strip()}".upper()
                    if c_tag not in locked_course_map:
                        locked_course_map[c_tag] = {"sections": [], "dept": s.course_name.strip(), "num": s.course_number.strip()}
                    locked_course_map[c_tag]["sections"].append(s)

                for c_tag, data in locked_course_map.items():
                    cat_secs = catalog.get_sections(c_tag) or catalog.get_sections(f"{data['dept']} {data['num']}")
                    has_disc_in_catalog = any(scheduler.is_discussion_section(s) for s in cat_secs)

                    if has_disc_in_catalog:
                        held_secs = data["sections"]
                        has_l = any(not scheduler.is_discussion_section(s) for s in held_secs)
                        has_d = any(scheduler.is_discussion_section(s) for s in held_secs)

                        if (has_l and not has_d) or (has_d and not has_l):
                            missing_comp = "Discussion" if has_l else "Lecture"
                            # Explicitly tag the token so scheduler only picks the missing component
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
                    for c in candidate_courses:
                        token = f"{c['subject_code']}{c['course_number']}".replace(" ", "").upper()
                        if token in locked_course_map or token in locked_course_ids:
                            continue

                        unit = CourseUnit.from_token(token)
                        if catalog.get_sections(token) or catalog.get_sections(f"{c['subject_code']} {c['course_number']}"):
                            pool_reqs.append(
                                RequirementGroup(
                                    group_name=f"{c['subject_code']} {c['course_number']}",
                                    courses_needed=1,
                                    units=[unit]
                                )
                            )
                else:
                    reconstructed_reqs = []
                    for item in serialized_reqs:
                        units = [CourseUnit.from_token(token) for token in item["units"]]
                        reconstructed_reqs.append(
                            RequirementGroup(
                                group_name=item["group_name"],
                                courses_needed=item["courses_needed"],
                                units=units
                            )
                        )
                    pool_reqs = validate_requirements(reconstructed_reqs, catalog)

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
                            
                            prereq_text = "None"
                            if course_meta and getattr(course_meta, "prerequisites", None):
                                raw_prereq_source = course_meta.prerequisites
                                if isinstance(raw_prereq_source, dict):
                                    prereq_text = raw_prereq_source.get("raw") or "None"
                                elif isinstance(raw_prereq_source, str):
                                    try:
                                        parsed = json.loads(raw_prereq_source)
                                        prereq_text = parsed.get("raw", raw_prereq_source) if isinstance(parsed, dict) else raw_prereq_source
                                    except Exception:
                                        prereq_text = raw_prereq_source

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
                                "raw_prerequisites": prereq_text,
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

    def is_wildcard_token(tok: str) -> bool:
        clean = tok.strip()
        if re.search(r'^[A-Za-z]+\s*\d+-\d+', clean):
            return True
        if re.match(r'^[A-Za-z]+\s*[1-5](xx)?$', clean, re.IGNORECASE):
            return True
        return False

    display_required = []
    manual_choice_requirements = []

    for req in serialized_reqs:
        units = req.get("units", [])
        needed = req.get("courses_needed", 1)

        wildcards = [u for u in units if is_wildcard_token(u)]

        if wildcards:
            display_title = " or ".join(units)
            manual_choice_requirements.append({
                "group_name": display_title,
                "needed": needed,
                "is_gened": False
            })
        else:
            display_required.append({
                "needed": needed,
                "options": units
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

    return render_template(
        "index.html",
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
        ignore_capacity=ignore_capacity
    )

if __name__ == "__main__":
    app.run(debug=True, port=5000)