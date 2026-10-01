from flask import Flask, request, jsonify, send_from_directory
from pdf_scanner import scan_pdf

app = Flask(__name__)

# Reject uploads larger than 10 MB
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024


#-------------------------
# Home page: sends index.html to the browser
#-------------------------

@app.route("/")
def home():
    return send_from_directory(".", "index.html")


#-------------------------
# Degree audit upload
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
        completed, required, _ = scan_pdf(pdf)   # _ = raw text, discarded on purpose
    except Exception:
        return jsonify({"error": "Could not read that PDF. Make sure it's a degree audit."}), 400

    return jsonify({
        "completed": completed,
        "required": required
    })


# For running locally:  python app.py  then open http://localhost:5000
if __name__ == "__main__":
    app.run(port=5000, debug=True)
