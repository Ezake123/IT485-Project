import os
import io
import json
from datetime import time
from functools import lru_cache
from flask import Flask, render_template, request
from dotenv import load_dotenv
from supabase import create_client, Client
import pdfplumber
from werkzeug.exceptions import RequestEntityTooLarge
from models import CatalogManager, SchedulePreferences, CourseUnit, RequirementGroup, parse_time_value
import pdf_scanner
import course_validator
import scheduler

load_dotenv()

app = Flask(__name__)

# Enforce a 100 KB maximum upload limit
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024  # 100 KB in bytes

# Initialize Supabase client
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if (SUPABASE_URL and SUPABASE_KEY) else None

@app.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(e):
    return render_template(
        "index.html",
        completed_courses=[],
        completed_courses_json="[]",
        required_courses=[],
        serialized_reqs_json="[]",
        earned_credits=0.0,
        schedule=None,
        schedule_json="[]",
        error_msg="The uploaded PDF exceeds the 10 MB file size limit. Please upload a smaller degree audit file.",
        is_scanned=False,
        is_generated=False,
        selected_days=["Mo", "Tu", "We", "Th", "Fr"],
        start_time_val="03:00",
        end_time_val="23:59",
        fit_maximum=False
    ), 413

@lru_cache(maxsize=4)
def load_catalog_for_term(term: str = "Fall 2026") -> CatalogManager:
    """Fetches course records and active term sections from Supabase once and caches them in memory."""
    if not supabase:
        raise ValueError("Missing SUPABASE_URL or SUPABASE_KEY in environment or .env file.")

    print(f"[*] Querying Supabase catalog for term: {term}...")
    courses_res = supabase.table("courses").select("*").execute()
    sections_res = supabase.table("course_sections").select("*").eq("term", term).execute()

    return CatalogManager.load_from_supabase_data(courses_res.data, sections_res.data)


def parse_audit_pdf(file_stream):
    """Scans and extracts completed courses and required blocks using your existing pdf_scanner."""
    audit = []
    with pdfplumber.open(file_stream) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                audit.extend(text.splitlines())

    audit_result = pdf_scanner.build_audit_result(audit)
    completed = sorted(list(audit_result.completed_courses))
    earned_credits = audit_result.earned_credits

    # Serialize requirements so they can be preserved across HTTP POST steps
    serialized_reqs = []
    for req in audit_result.requirements:
        serialized_reqs.append({
            "group_name": req.group_name,
            "courses_needed": req.courses_needed,
            "units": [u.raw_token for u in req.units]
        })

    return completed, earned_credits, serialized_reqs


@app.route("/", methods=["GET", "POST"])
def index():
    completed_courses = []
    earned_credits = 0.0
    serialized_reqs = []
    display_required = []
    schedule = None
    schedule_json = "[]"
    error_msg = None
    is_scanned = False
    is_generated = False

    # Default restriction settings matching the UI defaults
    selected_days = ["Mo", "Tu", "We", "Th", "Fr"]
    start_time_val = "00:00"
    end_time_val = "24:00"
    fit_maximum = False

    if request.method == "POST":
        action = request.form.get("action", "")

        # -------------------------------------------------------------
        # STEP 1: SCAN AUDIT ONLY (No schedule generated yet)
        # -------------------------------------------------------------
        if action == "scan":
            file = request.files.get("audit_pdf")
            if file and file.filename.endswith(".pdf"):
                try:
                    pdf_bytes = io.BytesIO(file.read())
                    completed_courses, earned_credits, serialized_reqs = parse_audit_pdf(pdf_bytes)
                    is_scanned = True
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

            try:
                serialized_reqs = json.loads(raw_reqs_json)
                completed_courses = json.loads(completed_json)
                is_scanned = True
                is_generated = True

                # Read interactive constraint form parameters
                selected_days = request.form.getlist("allowed_days")
                start_time_input = request.form.get("earliest_start", "").strip()
                end_time_input = request.form.get("latest_end", "").strip()
                fit_maximum = "fit_maximum" in request.form

                start_time_val = start_time_input if start_time_input else "03:00"
                end_time_val = end_time_input if end_time_input else "24:00"

                # Build preferences object
                prefs = SchedulePreferences(
                    earliest_start=parse_time_value(start_time_val),
                    latest_end=parse_time_value(end_time_val),
                    allowed_days=selected_days if selected_days else None,
                    delivery_mode="Any",
                    fit_maximum=fit_maximum
                )

                # Reconstruct RequirementGroup dataclasses
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

                catalog = load_catalog_for_term("Fall 2026")
                valid_reqs = course_validator.validate_requirements(reconstructed_reqs, catalog)

                target = len(valid_reqs) if fit_maximum else 5
                raw_schedule = scheduler.solve_schedule(
                    requirements=valid_reqs,
                    catalog=catalog,
                    target_count=target,
                    preferences=prefs
                )

                schedule = []
                if raw_schedule:
                    for sec in raw_schedule:
                        course_meta = catalog.get_course(sec.course_id)
                        
                        # Extract the human-readable description from the JSON dictionary
                        prereq_text = "None"
                        if course_meta and course_meta.prerequisites:
                            prereqs_data = course_meta.prerequisites
                            
                            # If it's stored as a dict, grab the human-readable 'raw' field
                            if isinstance(prereqs_data, dict):
                                prereq_text = prereqs_data.get("raw") or "None"
                            elif isinstance(prereqs_data, str):
                                # If it was stored as a raw JSON string
                                try:
                                    parsed = json.loads(prereqs_data)
                                    prereq_text = parsed.get("raw", prereqs_data) if isinstance(parsed, dict) else prereqs_data
                                except Exception:
                                    prereq_text = prereqs_data

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
                            "credits": course_meta.credits if course_meta else 3,
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

    return render_template(
        "index.html",
        completed_courses=completed_courses,
        completed_courses_json=json.dumps(completed_courses),
        required_courses=display_required,
        serialized_reqs_json=json.dumps(serialized_reqs),
        earned_credits=earned_credits,
        schedule=schedule,
        schedule_json=schedule_json,
        error_msg=error_msg,
        is_scanned=is_scanned,
        is_generated=is_generated,
        selected_days=selected_days,
        start_time_val=start_time_val,
        end_time_val=end_time_val,
        fit_maximum=fit_maximum
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)