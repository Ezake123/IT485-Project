import os
import traceback
from flask import Flask, request, jsonify, send_from_directory
from pdf_scanner import read_pdf_lines, analyze_audit, AuditError
from catalog import load_catalog, lookup_courses, section_json, plan_term, CatalogUnavailable
from models import SchedulePreferences, parse_time_value, course_id, spaced, DAY_ORDER
from course_validator import build_requirement_groups, explain_requirements
from scheduler import solve_schedule

# Reads SUPABASE_URL / SUPABASE_KEY / PLAN_TERM from a .env file when running locally
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)

# Folder this file lives in, so index.html is found no matter where Render starts the app
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Reject uploads larger than 10 MB
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024


#-------------------------
# Home page: sends index.html to the browser
#-------------------------

@app.route("/")
def home():
    return send_from_directory(BASE_DIR, "index.html")


#-------------------------
# Builds the JSON the website expects from the audit text lines.
# Raises AuditError (with a message for the student) if it isn't a degree audit.
#-------------------------

def audit_response(lines):
    return jsonify(analyze_audit(lines))


def audit_error(message):
    return jsonify({"error": message}), 400


#-------------------------
# Degree audit upload (PDF)
#
# The page sends the PDF here (form field name: "audit").
# The PDF is read in memory and never saved.
#-------------------------

@app.route("/api/audit", methods=["POST"])
def analyze_audit_pdf():
    pdf = request.files.get("audit")

    if pdf is None or pdf.filename == "":
        return audit_error("No file was uploaded. Choose your degree audit PDF and try again.")

    if not pdf.filename.lower().endswith(".pdf"):
        return audit_error("That file isn't a PDF. Upload your degree audit as a PDF and try again.")

    # A real PDF always starts with "%PDF-", whatever the file is named
    if pdf.stream.read(5) != b"%PDF-":
        return audit_error("That file isn't a valid PDF. Download your degree audit as a PDF and try again.")
    pdf.stream.seek(0)

    try:
        return audit_response(read_pdf_lines(pdf))
    except AuditError as e:
        return audit_error(str(e))
    except Exception:
        traceback.print_exc()
        return audit_error("Something went wrong reading that file. Try uploading your degree audit again.")


#-------------------------
# Degree audit as pasted text (or a .txt file)
#
# The page sends JSON: {"text": "..."}
#-------------------------

@app.route("/api/audit-text", methods=["POST"])
def analyze_audit_text():
    data = request.get_json(silent=True) or {}
    text = data.get("text", "")

    if not isinstance(text, str) or not text.strip():
        return audit_error("No audit text was found. Paste the full text of your degree audit and try again.")

    try:
        return audit_response(text.splitlines())
    except AuditError as e:
        return audit_error(str(e))
    except Exception:
        traceback.print_exc()
        return audit_error("Something went wrong reading that text. Try pasting your degree audit again.")


# Uploads over the 10 MB limit
@app.errorhandler(413)
def too_large(_):
    return audit_error("That file is too large. A degree audit PDF is usually well under 1 MB; check that you picked the right file.")


#-------------------------
# Sample audit for the "Try a sample audit" button
# (a real audit with the student's name and ID removed)
#-------------------------

@app.route("/api/sample", methods=["GET"])
def sample_audit():
    with open(os.path.join(BASE_DIR, "sample_audit.txt"), encoding="utf-8") as f:
        return audit_response(f.read().splitlines())


#-------------------------
# Course details and sections, for the Courses page
#
# The page sends JSON: {"codes": ["CS310", "CHEM 116", "BIOL304-395", ...]}
# Ranges come back expanded into the courses in them that are offered.
#-------------------------

def catalog_or_error():
    try:
        return load_catalog(), None
    except CatalogUnavailable as e:
        return None, (jsonify({"error": str(e)}), 503)
    except Exception as e:
        traceback.print_exc()
        return None, (jsonify({"error": f"Could not load the course catalog ({type(e).__name__}: {str(e)[:200]})"}), 502)


@app.route("/api/courses", methods=["POST"])
def courses():
    data = request.get_json(silent=True) or {}
    codes = data.get("codes")
    if not isinstance(codes, list) or not codes:
        return jsonify({"error": "No course codes were sent."}), 400

    catalog, error = catalog_or_error()
    if error:
        return error
    found, ranges = lookup_courses([str(c) for c in codes][:300], catalog)
    return jsonify({"term": plan_term(), "courses": found, "ranges": ranges})


#-------------------------
# Builds a schedule with scheduler.py
#
# The page sends JSON:
# {
#   "mode": "auto" (pick courses from the open requirements) or "selected" (use the chosen courses),
#   "completed": ["CS 110", ...], "earned_credits": 104,
#   "requirements": [{"name": ..., "need": 2 or {"credits": 20}, "options": [...], "exclude": [...]}],
#   "courses": [{"code": "CHEM 115", "requirement": "Intro Chemistry"}],   (selected mode)
#   "pins": {"CHEM 115": "3584"},                                          (chosen sections)
#   "prefs": {"days": ["Mo","Tu","We","Th","Fr"], "earliest": "09:00", "latest": "17:00",
#             "max_courses": 5, "max_credits": 18, "delivery": "Any", "open_only": true}
# }
#-------------------------

def read_preferences(raw: dict) -> SchedulePreferences:
    raw = raw or {}
    days = [d for d in (raw.get("days") or []) if d in DAY_ORDER]
    delivery = raw.get("delivery") if raw.get("delivery") in ("Any", "In-person", "Online") else "Any"

    def bounded(value, low, high, default):
        try:
            return min(high, max(low, int(value)))
        except (TypeError, ValueError):
            return default

    return SchedulePreferences(
        earliest_start=parse_time_value(raw.get("earliest")),
        latest_end=parse_time_value(raw.get("latest")),
        allowed_days=days or None,
        delivery_mode=delivery,
        open_seats_only=bool(raw.get("open_only", True)),
        max_courses=bounded(raw.get("max_courses"), 1, 8, 5),
        max_credits=bounded(raw.get("max_credits"), 1, 24, 18),
    )


@app.route("/api/schedule", methods=["POST"])
def schedule():
    data = request.get_json(silent=True) or {}
    catalog, error = catalog_or_error()
    if error:
        return error

    prefs = read_preferences(data.get("prefs"))
    completed = {course_id(c) for c in data.get("completed") or []}
    try:
        earned = float(data.get("earned_credits") or 0)
    except (TypeError, ValueError):
        earned = 0
    pins = {course_id(k): str(v) for k, v in (data.get("pins") or {}).items() if v}

    if data.get("mode") == "selected":
        chosen = [c if isinstance(c, dict) else {"code": c} for c in data.get("courses") or []][:12]
        if not chosen:
            return jsonify({"error": "Add at least one course first."}), 400
        reqs = [{"name": c.get("requirement") or spaced(course_id(c["code"])), "need": 1,
                 "options": [course_id(c["code"])]} for c in chosen if c.get("code")]
        wanted = [course_id(c["code"]) for c in chosen if c.get("code")]
        prefs.max_courses = max(len(wanted), 1)
    else:
        reqs = data.get("requirements") or []
        wanted = []
        if not reqs:
            return jsonify({"error": "No open requirements were sent."}), 400

    try:
        groups = build_requirement_groups(reqs, catalog, completed, earned)
        result = solve_schedule(groups, catalog, prefs, pins, completed,
                                strict_coreqs=data.get("mode") != "selected")
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Could not build a schedule ({type(e).__name__}: {str(e)[:200]})"}), 500

    fills = result["fills"]
    sections = []
    for sec in result["sections"]:
        course = catalog.get_course(sec.course_id)
        sections.append(dict(section_json(sec), course=spaced(sec.course_id),
                             requirement=fills.get(sec.course_id, ""),
                             credits=catalog.credits_for(sec.course_id) or None,
                             description=(course.description or "") if course else ""))

    # Co-requisites that aren't completed or in this schedule
    planned = set(fills)
    warnings = []
    for cid in fills:
        course = catalog.get_course(cid)
        if course:
            for group in course.prerequisites_cnf:
                if course.is_coreq_group(group) and not any(c in completed or c in planned for c in group):
                    warnings.append(f"{spaced(cid)} must be taken with {' or '.join(spaced(c) for c in group)}.")

    try:
        why_not = explain_requirements(reqs, catalog, completed, earned, fills,
                                       result["sections"], prefs) if data.get("mode") != "selected" else []
    except Exception:
        traceback.print_exc()
        why_not = []

    return jsonify({
        "term": plan_term(),
        "why_not": why_not,
        "max_courses": prefs.max_courses,
        "sections": sections,
        "credits": result["credits"],
        "courses": [spaced(c) for c in fills],
        "unscheduled": [spaced(c) for c in wanted if c not in fills],
        "warnings": warnings,
        "requirements_used": len(groups),
    })


# For running locally:  python app.py  then open http://localhost:5000
if __name__ == "__main__":
    app.run(port=5000, debug=True)
