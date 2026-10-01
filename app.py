import os
import traceback
from flask import Flask, request, jsonify, send_from_directory
from pdf_scanner import read_pdf_lines, analyze_lines
from course_matcher import lookup_courses

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
# Builds the JSON the website expects from the audit text lines
#-------------------------

def audit_response(lines):
    completed, requirements, remaining = analyze_lines(lines)
    return jsonify({
        "completed": completed,        # e.g. ["CS110", "MATH140"]
        "requirements": requirements,  # every requirement, before removing completed courses
        "remaining": remaining         # requirements still open
    })


#-------------------------
# Degree audit upload (PDF)
#
# The page sends the PDF here (form field name: "audit").
# The PDF is read in memory and never saved.
#-------------------------

@app.route("/api/audit", methods=["POST"])
def analyze_audit():
    pdf = request.files.get("audit")

    if pdf is None or pdf.filename == "":
        return jsonify({"error": "No file uploaded."}), 400

    if not pdf.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Please upload a PDF file."}), 400

    try:
        lines = read_pdf_lines(pdf)
        return audit_response(lines)
    except Exception:
        return jsonify({"error": "Could not read that PDF. Make sure it's a degree audit."}), 400


#-------------------------
# Degree audit as pasted text (or a .txt file)
#
# The page sends JSON: {"text": "..."}
#-------------------------

@app.route("/api/audit-text", methods=["POST"])
def analyze_audit_text():
    data = request.get_json(silent=True) or {}
    text = data.get("text", "")

    if not text.strip():
        return jsonify({"error": "No audit text was sent."}), 400

    try:
        return audit_response(text.splitlines())
    except Exception:
        return jsonify({"error": "Could not read that audit text."}), 400


#-------------------------
# Course details and sections from Supabase
#
# The page sends JSON: {"codes": ["CS310", "CHEM 116", "BIOL304-395", ...]}
# Ranges come back expanded into the courses in them that are offered.
# Only sections for PLAN_TERM (a Render environment variable, e.g. "Fall 2026")
# are returned. If PLAN_TERM isn't set, sections from every term come back.
#-------------------------

@app.route("/api/courses", methods=["POST"])
def courses():
    data = request.get_json(silent=True) or {}
    codes = data.get("codes")

    if not isinstance(codes, list) or not codes:
        return jsonify({"error": "No course codes were sent."}), 400

    codes = [str(c) for c in codes][:300]
    term = os.environ.get("PLAN_TERM") or None

    try:
        result, ranges = lookup_courses(codes, term)
    except RuntimeError:
        return jsonify({"error": "The course database isn't connected yet."}), 503
    except Exception as e:
        # Full error goes to Render's Logs tab; a short version is shown on the page
        traceback.print_exc()
        return jsonify({"error": f"Could not load course details ({type(e).__name__}: {str(e)[:200]})"}), 502

    return jsonify({"term": term or "", "courses": result, "ranges": ranges})


# For running locally:  python app.py  then open http://localhost:5000
if __name__ == "__main__":
    app.run(port=5000, debug=True)
