import os
import io
import json
import re
from datetime import time
from functools import lru_cache
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
from supabase import create_client, Client
import pdfplumber
from werkzeug.exceptions import RequestEntityTooLarge
from models import CatalogManager, SchedulePreferences, CourseUnit, RequirementGroup, parse_time_value, GENED_TOKEN_MAP
import pdf_scanner
import course_validator
import scheduler

#-----------------------------------------------------------------
# Description:
# Connects all the scripts and website together
# 
# Requirement:
# Verify all other requirements in other scripts are satisfied
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

# Enforce a 100 KB maximum upload limit since most degree audit can't even get over 50KB
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 

# Initialize Supabase client
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if (SUPABASE_URL and SUPABASE_KEY) else None

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

def prereqs_met_for(course_id: str, catalog: CatalogManager, completed_set, earned_credits) -> bool:
    course_obj = catalog.get_course(course_id)
    if not course_obj:
        return True  # Nothing to check against
    try:
        return course_obj.prerequisites_met(completed_set, earned_credits)
    except Exception:
        return True  # Malformed prerequisite data shouldn't block scheduling

#-----------------------------------------------------------------
# Extracts unique offered courses from serialized requirements
#-----------------------------------------------------------------

def get_candidate_courses_pool(serialized_reqs, catalog: CatalogManager):
    seen_ids = set()
    candidate_list = []

    for req in serialized_reqs:
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
                                    "sections_count": len(sections)
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
                            "sections_count": len(sections)
                        })

    candidate_list.sort(key=lambda c: (c["subject_code"], c["course_number"]))
    return candidate_list

#-----------------------------------------------------------------
# Handler for file too large
#-----------------------------------------------------------------

@app.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(e):
    return render_template(
        "index.html",
        completed_courses=[],
        completed_courses_json="[]",
        required_courses=[],
        gen_ed_requirements=[],
        serialized_gen_eds_json="[]",
        candidate_courses=[],
        locked_sections=[],
        locked_sections_json="[]",
        serialized_reqs_json="[]",
        earned_credits=0.0,
        schedule=None,
        schedule_json="[]",
        error_msg="The uploaded PDF exceeds the 100 KB file size limit. Please upload a smaller degree audit file.",
        is_scanned=False,
        is_generated=False,
        selected_days=["Mo", "Tu", "We", "Th", "Fr"],
        start_time_val="03:00",
        end_time_val="23:59",
        target_course_count = 5,
        ignore_capacity=False,
        ignore_prereqs=False,
        warnings=[]
    ), 413

#-----------------------------------------------------------------
# Finds courses by term/semester
#-----------------------------------------------------------------

@lru_cache(maxsize=4)
def load_catalog_for_term(term: str = "Fall 2026") -> CatalogManager:
    if not supabase:
        raise ValueError("Missing SUPABASE_URL or SUPABASE_KEY in environment or .env file.")

    print(f"[*] Querying Supabase catalog for term: {term}...")
    
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

    # 2. Fetch all sections for the term (paginated across 1,000+ rows)
    all_sections = []
    page = 0
    while True:
        res = (
            supabase.table("course_sections")
            .select("*")
            .ilike("term", f"%{term.strip()}%")  # Case-insensitive partial match to handle whitespace/formatting
            .range(page * page_size, (page + 1) * page_size - 1)
            .execute()
        )
        all_sections.extend(res.data)
        if len(res.data) < page_size:
            break
        page += 1

    print(f"[+] Loaded {len(all_courses)} courses and {len(all_sections)} sections into memory.")
    return CatalogManager.load_from_supabase_data(all_courses, all_sections)

#-----------------------------------------------------------------
# Fast Course Search Endpoint (Name and Number Strict Match)
#-----------------------------------------------------------------

@app.route("/api/courses", methods=["GET"])
def search_all_courses():
    query = request.args.get("q", "").strip().lower()
    geneds_param = request.args.get("geneds", "").strip()
    selected_geneds = [normalize_gened(g) for g in geneds_param.split(",") if g.strip()]

    # If neither query text nor Gen Ed filters are supplied, return empty
    if not query and not selected_geneds:
        return jsonify([])

    catalog = load_catalog_for_term("Fall 2026")
    results = []
    query_compact = query.replace(" ", "")

    for course_id, course in catalog.courses.items():
        dept = course.course_name.strip()
        num = course.course_number.strip()
        full_spaced = f"{dept} {num}".lower()
        full_compact = f"{dept}{num}".lower()
        course_gened = normalize_gened(course.gen_ed)

        # 1. Text filter check (if user typed something)
        text_match = True
        if query:
            text_match = (
                (query in full_spaced) or 
                (query_compact in full_compact) or 
                (query == dept.lower()) or 
                (query == num.lower())
            )

        # 2. Gen Ed filter check (if user checked any Gen Ed boxes)
        gened_match = True
        if selected_geneds:
            gened_match = any(target in course_gened for target in selected_geneds)

        # Must satisfy both criteria
        if text_match and gened_match:
            sections = catalog.get_sections(f"{dept}{num}")
            if not sections:
                sections = catalog.get_sections(f"{dept} {num}")

            credits_val = sections[0].credits if (sections and hasattr(sections[0], "credits") and sections[0].credits > 0) else 3

            results.append({
                "code": f"{dept} {num}",
                "subject_code": dept,
                "course_number": num,
                "name": getattr(course, "description", None) or f"{dept} {num}",
                "description": course.description or "No description available.",
                "gen_ed": course.gen_ed or "None",
                "credits": credits_val,
                "sections_count": len(sections),
                "prereqs": "None"
            })
            if len(results) >= 300:
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

    catalog = load_catalog_for_term("Fall 2026")
    course_id = f"{course_name}{course_number}"
    course_obj = catalog.get_course(course_id)
    raw_sections = catalog.get_sections(course_id)

    if not raw_sections:
        raw_sections = catalog.get_sections(f"{course_name} {course_number}")

    # Extract prerequisite text safely
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
            "term": s.term or "Fall 2026",
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

    # Sort sections in natural sequence (e.g. 01, 02, 01D, 02D)
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

    # Default restriction settings matching the UI defaults
    selected_days = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
    start_time_val = "00:00"
    end_time_val = "24:00"
    target_course_count = 5
    ignore_capacity = False
    ignore_prereqs = False
    warnings = []

    catalog = None
    try:
        catalog = load_catalog_for_term("Fall 2026")
    except Exception as e:
        print(f"Warning: Could not pre-load catalog: {e}")

    # Check for manual mode trigger on GET request
    if request.method == "GET" and request.args.get("manual") == "true":
        is_scanned = True
        # Keep completed_courses and serialized_reqs empty so the breakdown panel is suppressed

    if request.method == "POST":
        action = request.form.get("action", "")

        # -------------------------------------------------------------
        # STEP 1: SCAN AUDIT ONLY (No schedule generated yet)
        # -------------------------------------------------------------
        if action == "scan":
            file = request.files.get("audit_pdf")
            if file and file.filename.lower().endswith(".pdf"):
                try:
                    pdf_bytes = io.BytesIO(file.read())
                    completed_courses, earned_credits, serialized_reqs, serialized_gen_eds = parse_audit_pdf(pdf_bytes)
                    is_scanned = True

                    if catalog:
                        # Only pre-load major / degree requirements into the pool (No Gen Eds)
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

            # 1. Preserve and unpack locked sections
            raw_locked_json = request.form.get("locked_sections_json", "[]")
            locked_sections_json = raw_locked_json
            try:
                locked_sections = json.loads(raw_locked_json)
            except Exception:
                locked_sections = []

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

                ignore_prereqs = "ignore_prereqs" in request.form

                if not catalog:
                    raise ValueError("The course catalog could not be loaded from the database. Please try again shortly.")

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

                # Prerequisites can only be checked when we know what the student has
                # taken, i.e. after an audit scan (manual mode has no course history).
                completed_set = {normalize_course_id(c) for c in completed_courses}
                has_audit = bool(completed_set) or earned_credits > 0
                check_prereqs = has_audit and not ignore_prereqs
                if not has_audit and not ignore_prereqs:
                    warnings.append("Prerequisites weren't checked because no degree audit was scanned. Confirm you meet them before registering.")

                # 2. Rebuild Section instances for locked selections
                locked_sec_objs = []
                locked_course_ids = set()
                for item in locked_sections:
                    s_data = item.get("section", {})
                    c_data = item.get("course", {})
                    course_id = f"{c_data.get('subject_code')}{c_data.get('course_number')}".replace(" ", "").upper()
                    locked_course_ids.add(course_id)
                    
                    catalog_sections = catalog.get_sections(course_id)
                    if not catalog_sections:
                        catalog_sections = catalog.get_sections(f"{c_data.get('subject_code')} {c_data.get('course_number')}")

                    matched = next((s for s in catalog_sections if str(s.class_code) == str(s_data.get("class_code"))), None)
                    if matched:
                        locked_sec_objs.append(matched)
                        # Locked sections are the student's explicit choice, so keep them,
                        # but say so if they don't meet the prerequisites.
                        if check_prereqs and not prereqs_met_for(course_id, catalog, completed_set, earned_credits):
                            warnings.append(
                                f"Locked {matched.course_name} {matched.course_number}: your audit doesn't show its prerequisites "
                                f"({prereq_text_for(catalog.get_course(course_id))})."
                            )
                    else:
                        warnings.append(
                            f"Locked section #{s_data.get('class_code')} for {c_data.get('subject_code')} {c_data.get('course_number')} "
                            f"is no longer offered and was left out."
                        )

                # Locked sections are never checked by the solver against each other,
                # so flag any pair that overlaps.
                for i in range(len(locked_sec_objs)):
                    for j in range(i + 1, len(locked_sec_objs)):
                        a, b = locked_sec_objs[i], locked_sec_objs[j]
                        if scheduler.intervals_collide(a.days, a.start_time, a.end_time, b.days, b.start_time, b.end_time):
                            warnings.append(
                                f"Time conflict: your locked sections {a.course_name} {a.course_number} (Sec {a.section}) and "
                                f"{b.course_name} {b.course_number} (Sec {b.section}) overlap. Unlock one and generate again."
                            )

                prefs = SchedulePreferences(
                    earliest_start=parse_time_value(start_time_val),
                    latest_end=parse_time_value(end_time_val),
                    allowed_days=selected_days if selected_days else None,
                    delivery_mode="Any",
                    ignore_capacity=ignore_capacity
                )

                # 3. Build requirement groups for pool courses (excluding locked courses)
                valid_reqs = []
                prereq_skipped = []
                if pool_submitted:
                    for c in candidate_courses:
                        token = f"{c['subject_code']}{c['course_number']}".replace(" ", "").upper()
                        if token not in locked_course_ids:
                            if check_prereqs and not prereqs_met_for(token, catalog, completed_set, earned_credits):
                                prereq_skipped.append(
                                    f"{c['subject_code']} {c['course_number']} ({prereq_text_for(catalog.get_course(token))})"
                                )
                                continue
                            unit = CourseUnit.from_token(token)
                            if catalog.get_sections(token) or catalog.get_sections(f"{c['subject_code']} {c['course_number']}"):
                                valid_reqs.append(
                                    RequirementGroup(
                                        group_name=f"{c['subject_code']} {c['course_number']}",
                                        courses_needed=1,
                                        units=[unit]
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
                                eligible = [cid for cid in unit.courses if prereqs_met_for(cid, catalog, completed_set, earned_credits)]
                                if unit.unit_type == "AND" and len(eligible) < len(unit.courses):
                                    prereq_skipped.append(token)
                                    continue
                                if not eligible:
                                    prereq_skipped.append(token)
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
                    valid_reqs = course_validator.validate_requirements(reconstructed_reqs, catalog)

                if prereq_skipped:
                    warnings.append(
                        "Skipped because your audit doesn't show the prerequisites: " + "; ".join(prereq_skipped)
                        + ". Check \"Ignore prerequisite checks\" if you have permission or they're in progress."
                    )
                if pool_submitted and not candidate_courses and not locked_sections:
                    warnings.append("Your course pool is empty. Add courses from the search below, then generate again.")

                # 4. Total target courses wanted (e.g. 5). The solver fills up to this total using locked + pool.
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
                        # Fetch catalog metadata for description, gen_ed, and prerequisites
                        course_meta = catalog.get_course(sec.course_id)

                        # Extract prerequisites text safely
                        raw_prereq_source = course_meta.prerequisites if course_meta else None
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
                            "credits": sec.credits,  # <-- USE sec.credits DIRECTLY (Do not use course_meta.credits)
                            "gen_ed": course_meta.gen_ed if (course_meta and course_meta.gen_ed) else "None",
                            "description": course_meta.description if (course_meta and course_meta.description) else "No description available.",
                            "raw_prerequisites": prereq_text
                        })

                    # Form calendar payload for weekly grid
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

    # Format requirement units for Jinja display
    for req in serialized_reqs:
        display_required.append({
            "needed": req["courses_needed"],
            "options": req["units"]
        })

    # Format gen ed units for Jinja display with clean database names
    for req in serialized_gen_eds:
        formatted_options = []
        for token in req.get("units", []):
            if isinstance(token, str) and token.startswith("GENED:"):
                raw_tag = token.split("GENED:", 1)[1].strip()
                clean_name = GENED_TOKEN_MAP.get(raw_tag, raw_tag.replace("_", " ").title())
                formatted_options.append(clean_name)
            else:
                formatted_options.append(str(token))

        display_gen_eds.append({
            "group_name": req.get("group_name", "General Education"),
            "needed": req.get("courses_needed", 1),
            "options": formatted_options
        })

    return render_template(
        "index.html",
        completed_courses=completed_courses,
        completed_courses_json=json.dumps(completed_courses),
        required_courses=display_required,
        gen_ed_requirements=display_gen_eds,
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
        warnings=warnings
    )

if __name__ == "__main__":
    app.run(debug=True, port=5000)