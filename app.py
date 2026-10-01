import os
from flask import Flask, request, jsonify, send_from_directory
from pdf_scanner import read_pdf_lines, analyze_lines

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


# For running locally:  python app.py  then open http://localhost:5000
if __name__ == "__main__":
    app.run(port=5000, debug=True)
