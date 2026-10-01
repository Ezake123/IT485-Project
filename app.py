import os
import traceback
from flask import Flask, request, jsonify, send_from_directory
from pdf_scanner import read_pdf_lines, analyze_audit, AuditError
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
