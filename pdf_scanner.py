import pdfplumber
import re

#-------------------------
# Scans the pdf for courses and categorized them into either
# completed/in-progress and required courses
# Heavily modify with Gemini Flash 3.8
#
# Requirement:
#   pip install pdfplumber
#-------------------------

#-------------------------
# Scans for completed/in-progress courses and return the list of them 
# concatnated with the course name and number without spaces
#-------------------------

def complete_course_scan(audit):
    completed_courses = []

    # Matches a term at line start OR WAIVE anywhere, followed by dept and number
    course_re = re.compile(
        r"(?:^\d{2}\s*(?:FL|SU|SP|WR)|\bWAIVE)\s+([A-Z]{2,8})\s*(\d{1,3}[A-Z]?)\b"
    )

    # Grades that mean the course did NOT count (failed, withdrew, no credit)
    failed_re = re.compile(r"\d+\.\d{2}\s+(F|W|WF|WU|NC|INC|AU)\b")

    for line in audit:
        match = course_re.search(line.strip())
        if not match:
            continue
        if failed_re.search(line):
            continue

        dept, num = match.groups()
        course = f"{dept}{num}"

        if course not in completed_courses:
            completed_courses.append(course)

    return completed_courses

#-------------------------
# Turns the text of a "Select from:" list into course options, e.g.
#   "BIOL 111,112"            -> ["BIOL111", "BIOL112"]
#   "PHYSIC107 or 113,171"    -> ["PHYSIC107|PHYSIC113", "PHYSIC171"]
#   "CHEM 115 & 117"          -> ["CHEM115&CHEM117"]
#   "BIOL 304 TO 395 BIOL 4"  -> ["BIOL304-395", "BIOL400-499"]
#-------------------------

NUM_RE = re.compile(r"^\d{1,3}[A-Z]?$")


def parse_course_list(raw_text):
    num_re = NUM_RE
    # Clean parenthetical notes like (THROUGH 19SU) and pipes
    cleaned = re.sub(r"\([^)]*\)", "", raw_text)
    cleaned = re.sub(r"\|", " ", cleaned)

    # Clean out "TERM ?? COURSES:"
    cleaned = re.sub(r"\bTERM\s+[A-Z0-9]{1,2}\s+COURSES\s*:?", " ", cleaned, flags=re.IGNORECASE)

    # Separate numbers glued to dept names (e.g. 301MSIS -> 301 MSIS, NURSNG370 -> NURSNG 370)
    cleaned = re.sub(r"(\d{1,3}[A-Z]?)(?=[A-Z]{2,8}\b)", r"\1 ", cleaned)
    cleaned = re.sub(r"([A-Z]{2,8})(?=\d)", r"\1 ", cleaned)

    # Normalize connectors
    cleaned = re.sub(r"\bAND\b", "&", cleaned)
    cleaned = re.sub(r"[,;:]", " ", cleaned)

    tokens = cleaned.split()
    courses = []
    current_dept = None

    i = 0
    while i < len(tokens):
        tok = tokens[i].strip()

        if tok.isalpha():
            # Handle 'or' conjunction
            if tok.lower() == "or":
                i += 1
                continue

            is_valid_dept = tok.isupper() and (2 <= len(tok) <= 8) and tok not in ("TO", "OR")

            if is_valid_dept:
                has_subsequent_number = False
                for forward_tok in tokens[i + 1 : i + 5]:
                    if re.match(r"^\d{1,3}[A-Z]?$", forward_tok):
                        has_subsequent_number = True
                        break
                    elif forward_tok.isalpha() and forward_tok.isupper() and (2 <= len(forward_tok) <= 8):
                        break

                if has_subsequent_number:
                    current_dept = tok
                    i += 1
                    continue

            if len(courses) > 0 and not is_valid_dept:
                break

        # Course number match
        elif current_dept and re.match(r"^\d{1,3}[A-Z]?$", tok):
            # Range: "BIOL 304 TO 395" -> "BIOL304-395" (any course in that range)
            if i + 2 < len(tokens) and tokens[i + 1] == "TO" and num_re.match(tokens[i + 2]):
                low = re.match(r"\d+", tok).group()
                high = re.match(r"\d+", tokens[i + 2]).group()
                rng = f"{current_dept}{low}-{high}"
                if rng not in courses:
                    courses.append(rng)
                i += 3
                continue

            # Level wildcard: "BIOL 4" -> "BIOL400-499" (any 400-level BIOL course)
            if re.match(r"^\d$", tok):
                rng = f"{current_dept}{tok}00-{tok}99"
                if rng not in courses:
                    courses.append(rng)
                i += 1
                continue

            first_course = f"{current_dept}{tok}"

            # 1. Alternative pair separated by "or" (e.g. "BIOL 210 or 212" -> "BIOL210|BIOL212")
            if i + 2 < len(tokens) and tokens[i + 1].lower() == "or":
                next_tok = tokens[i + 2]

                # Same dept implied: "210 or 212"
                if re.match(r"^\d{1,3}[A-Z]?$", next_tok):
                    pair = f"{first_course}|{current_dept}{next_tok}"
                    if pair not in courses:
                        courses.append(pair)
                    i += 3
                    continue

                # Explicit dept given: "210 or BIOL 212"
                elif i + 3 < len(tokens) and re.match(r"^[A-Z]{2,8}$", next_tok) and re.match(r"^\d{1,3}[A-Z]?$", tokens[i + 3]):
                    pair_dept = next_tok
                    pair_num = tokens[i + 3]
                    pair = f"{first_course}|{pair_dept}{pair_num}"
                    if pair not in courses:
                        courses.append(pair)
                    current_dept = pair_dept
                    i += 4
                    continue

            # 2. Corequisite pair separated by "&" (e.g. "CHEM 252 & 256" -> "CHEM252&CHEM256")
            if i + 2 < len(tokens) and tokens[i + 1] == "&":
                next_tok = tokens[i + 2]

                # Same dept implied: "252 & 256"
                if re.match(r"^\d{1,3}[A-Z]?$", next_tok):
                    pair = f"{first_course}&{current_dept}{next_tok}"
                    if pair not in courses:
                        courses.append(pair)
                    i += 3
                    continue

                # Explicit dept given: "252 & CHEM 256"
                elif i + 3 < len(tokens) and re.match(r"^[A-Z]{2,8}$", next_tok) and re.match(r"^\d{1,3}[A-Z]?$", tokens[i + 3]):
                    pair_dept = next_tok
                    pair_num = tokens[i + 3]
                    pair = f"{first_course}&{pair_dept}{pair_num}"
                    if pair not in courses:
                        courses.append(pair)
                    current_dept = pair_dept
                    i += 4
                    continue

            # Standalone course
            if first_course not in courses:
                courses.append(first_course)

        i += 1

    return courses


#-------------------------
# Figure out the required courses and returns a list of it in the where each requirement
# is another list starting with the number of required courses needed to take following
# with all the courses for that requirement
#
# The first item is either a number of courses (2) or a credit amount ({"credits": 20}).
# Options can be:
#   "CS310"            a single course
#   "CS320|CS321"      either course
#   "CHEM115&CHEM117"  both courses (a set)
#   "BIOL304-395"      any course in that number range
#   "BIOL400-499"      any 400-level course (from "BIOL 4" in the audit)
#-------------------------

def required_course_scan(all_lines):
    required_courses = []
    
    # "Needs: 2Courses", "Needs: 1Set", "Needs: 20.00Credits"
    needs_re = re.compile(
        r"Needs:\s*(\d+(?:\.\d+)?)\s*(Courses|Course|Sets|Set|Sub-Reqs|Sub-Req|Credits|Credit)\b"
    )

    # Continuation lines of a "Select from:" list start with a department code,
    # e.g. "BIOL 315(25FL OR AFTER),316,321" or "CHEM 256(1) BIOCHM383 TO 386"
    continuation_re = re.compile(r"^[A-Z]{2,8}\s*\d")
    num_re = re.compile(r"^\d{1,3}[A-Z]?$")

    current_count = None
    collecting = False
    current_block_lines = []

    def finalize_block():
        nonlocal current_count, collecting, current_block_lines
        if collecting and current_block_lines:
            raw_text = " ".join(current_block_lines)
            
            courses = parse_course_list(raw_text)

            if courses:
                if current_count is not None:
                    required_courses.append([current_count] + courses)
                    current_count = None
                else:
                    required_courses.append(courses)

        collecting = False
        current_block_lines = []

    for line in all_lines:
        clean_line = line.strip()
        if not clean_line:
            continue

        needs_match = needs_re.search(clean_line)
        if needs_match:
            finalize_block()
            amount = float(needs_match.group(1))
            if needs_match.group(2).startswith("Credit"):
                current_count = {"credits": int(amount) if amount.is_integer() else amount}
            else:
                current_count = int(amount)
            continue

        if "Select from:" in clean_line:
            finalize_block()
            collecting = True
            after_select = clean_line.split("Select from:", 1)[1].strip()
            if after_select:
                current_block_lines.append(after_select)
            continue

        if collecting:
            if continuation_re.match(clean_line):
                current_block_lines.append(clean_line)
            else:
                finalize_block()

    finalize_block()
    return required_courses

#-------------------------
# For making the special case of unaccounted required courses
# usually caused by multiple select from without saying the required
# amount of courses before it
#-------------------------

def normalize_uncounted_requirements(required_courses):

    normalized = []
    
    for item in required_courses:
        # Create a shallow copy to prevent in-place side effects
        entry = list(item)
        
        # Check if the current entry lacks an integer count at index 0
        if not (entry and isinstance(entry[0], (int, dict))):
            # The list immediately preceding this one gets reduced to 1
            if normalized and isinstance(normalized[-1][0], int):
                normalized[-1][0] = 1
                
            # The current list also gets an explicit count of 1
            entry = [1] + entry
            
        normalized.append(entry)
        
    return normalized

#-------------------------
# Special case to removes completed courses inside required courses
# Usually caused by having a completed course counted into another 
# section of the degree audit
#-------------------------

def is_course_completed(course_token, completed_set):
    """
    Checks completion status:
    - '&' requires BOTH to be completed.
    - '|' requires EITHER ONE to be completed.
    - Single course requires presence in completed_set.
    - A range like 'BIOL304-395' is never removed: one course in the
      range doesn't use up the whole range.
    """
    if re.search(r"\d-\d", course_token):
        return False

    if "&" in course_token:
        parts = course_token.split("&")
        return all(p in completed_set for p in parts)
    
    if "|" in course_token:
        parts = course_token.split("|")
        return any(p in completed_set for p in parts)
        
    return course_token in completed_set


def remove_completed(completed_courses, required_courses):
    completed_set = set(completed_courses)
    remaining_requirements = []

    for req in required_courses:
        has_count = isinstance(req[0], (int, dict))
        count = req[0] if has_count else None
        options = req[1:] if has_count else req

        unfulfilled = []
        for c in options:
            if not is_course_completed(c, completed_set):
                unfulfilled.append(c)

        if unfulfilled:
            if has_count:
                remaining_requirements.append([count] + unfulfilled)
            else:
                remaining_requirements.append(unfulfilled)

    return remaining_requirements

#-------------------------
# Opens the pdf and extract the courses to their corresponding lists
#
# pdf_source can be a file path (for local testing) or an uploaded
# file object (from the website). pdfplumber accepts either.
#
# The third return value (audit) is the raw text of the PDF. It contains
# the student's personal info, so the website should NOT send it back to
# the browser or save it anywhere.
#-------------------------

class AuditError(Exception):
    """Raised when an upload can't be read as a degree audit. The message is shown to the student."""


def read_pdf_lines(pdf_source):
    """
    Reads the text of a PDF, line by line.
    pdf_source can be a file path (local testing) or an uploaded file (the website).
    """
    try:
        with pdfplumber.open(pdf_source) as pdf:
            audit = []
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    audit.extend(page_text.splitlines())
    except Exception:
        raise AuditError(
            "We couldn't open that PDF. It may be damaged or password-protected. "
            "Download your degree audit again and try uploading the new copy."
        )

    if not any(line.strip() for line in audit):
        raise AuditError(
            "That PDF doesn't contain any readable text. It may be a scan or a photo. "
            "Download your degree audit directly from WISER as a PDF and try again."
        )
    return audit


#-------------------------
# Checks that the text looks like a UMass Boston degree audit
#-------------------------

TERM_COURSE_RE = re.compile(
    r"^(\d{2}(?:FL|SP|SU|WR))\s+([A-Z]{2,8})\s*(\d{1,4}[A-Z]?)\s+(\d+\.\d{2})\s+(\S+)\s*(.*)$"
)


def check_is_audit(audit):
    text = "\n".join(audit)
    signs = [
        "DEGREE AUDIT" in text.upper() or "DEGREE PROGRAM AUDIT" in text.upper(),
        "Term Course Credits Grade Title" in text,
        "Needs:" in text or "Select from:" in text,
        bool(re.search(r"^(OK|NO)\s+[A-Z]", text, re.MULTILINE)),
        any(TERM_COURSE_RE.match(line.strip()) for line in audit),
    ]
    if sum(signs) < 3:
        raise AuditError(
            "This doesn't look like a degree audit. Make sure you upload the degree audit "
            "PDF from WISER (not a transcript, schedule, or other document) and try again."
        )


#-------------------------
# Reads the audit's structure so the website can show it like the audit does:
# sections (e.g. "VERBAL REASONING AND EXPRESSION") with a status, and inside them
# sub-requirements (e.g. "Composition I") with the courses that satisfied them
# and anything still needed.
#
# Only the requirement sections are read. The student's name and ID at the top
# of the audit are skipped and never returned.
#-------------------------

STARS_RE = re.compile(r"^\*{5,}$")
HEADER_RE = re.compile(r"^(OK|NO|IP|\+-)\s+(.+)$")
SUB_RE = re.compile(r"^(ip\s+)?([+-])(?!>)(?:\s+(R)\b)?\s*(.*)$")
NEEDS_ANY_RE = re.compile(r"Needs:\s*(\d+(?:\.\d+)?)\s*([A-Za-z-]+)")
EARNED_RE = re.compile(r"^(Earned|In-Progress):\s*(\d+(?:\.\d+)?)\s*([A-Za-z-]+)")
CONTINUATION_RE = re.compile(r"^[A-Z]{2,8}\s*\d")
SKIP_RE = re.compile(
    r"^(Term Course Credits Grade Title|Page \d+ of \d+|UMB EQUIVALENT TRANSFER COURSE|IN-P\s*-+>.*|\d+:\s.*|.*>>GPA.*)$"
)
HEADER_STATUS = {"OK": "complete", "NO": "incomplete", "IP": "in_progress", "+-": "in_progress"}
UNIT_NAMES = {
    "course": ("course", "courses"), "courses": ("course", "courses"),
    "set": ("set", "sets"), "sets": ("set", "sets"),
    "credit": ("credit", "credits"), "credits": ("credit", "credits"),
    "sub-req": ("sub-requirement", "sub-requirements"), "sub-reqs": ("sub-requirement", "sub-requirements"),
    "gpa": ("GPA", "GPA"),
}


def _number(value):
    amount = float(value)
    return int(amount) if amount.is_integer() else amount


def _amount_text(value, unit):
    amount = _number(value)
    names = UNIT_NAMES.get(unit.lower())
    if not names:
        return f"{amount} {unit}"
    if names[0] == "GPA":
        return f"{float(value):.1f} GPA"
    return f"{amount} {names[0] if amount == 1 else names[1]}"


def _clean_note(line):
    # "( 94.00Credits taken)" -> "94 credits taken", "3.687GPA" -> "3.687 GPA"
    line = re.sub(r"(\d+(?:\.\d+)?)\s*(Credits|Courses|Course|GPA)\b",
                  lambda m: f"{_number(m.group(1))} {m.group(2).lower() if m.group(2) != 'GPA' else 'GPA'}", line)
    line = re.sub(r"^\(\s*(.*?)\s*\)$", r"\1", line)
    return line.strip()


def _nice_title(title):
    title = title.strip().lstrip("* ").rstrip(":").strip()
    if title.startswith("GENERAL ELECTIVES"):
        return "General Electives"
    if title.isupper():
        words = []
        for word in title.split():
            if word in ("GPA", "BIOL", "II", "III", "UMASS/BOSTON"):
                words.append(word)
            elif word in ("AND", "OR", "OF", "THE", "FOR", "TO", "IN"):
                words.append(word.lower())
            else:
                words.append("/".join(part.capitalize() for part in word.split("/")))
        title = " ".join(words)
        title = title[0].upper() + title[1:]
    return title


def parse_audit_sections(audit):
    info = {"program": "", "catalog_year": "", "what_if": False, "all_complete": None}
    sections = []
    section = None
    node = None          # the sub-requirement (or section) lines currently belong to
    collecting = None    # "select" or "exclude" while reading a course list
    block = []

    def finish_block():
        nonlocal collecting, block
        if collecting and node is not None and block:
            raw = " ".join(block)
            if collecting == "select":
                node["options"].extend(parse_course_list(raw))
            else:
                # Drop exclusions limited to past terms, e.g. "381(THROUGH 09SU)"
                raw = re.sub(r"\d{1,3}[A-Z]?\([^)]*\)", " ", raw)
                node["exclude"].extend(parse_course_list(raw))
        collecting, block = None, []

    def new_node(title, status, required=False):
        return {"title": title, "status": status, "required": required, "notes": [],
                "courses": [], "needs": "", "count": None, "options": [], "exclude": [],
                "earned": "", "in_progress": ""}

    for index, raw_line in enumerate(audit):
        line = raw_line.strip()
        if not line:
            continue

        # Program details from the top of the audit (never the name or ID)
        program = re.search(r"Program code:\s*(\S+)\s+Catalog year:\s*(\S+)", line)
        if program:
            info["catalog_year"] = program.group(2)
            continue
        if "WHAT-IF" in line.upper():
            info["what_if"] = True
        if "AT LEAST ONE REQUIREMENT HAS NOT BEEN SATISFIED" in line:
            info["all_complete"] = False
        if "ALL REQUIREMENTS" in line.upper() and "COMPLETE" in line.upper():
            info["all_complete"] = True
        if section is None and re.match(r"^[A-Z ]+(MAJOR|MINOR)$|^(BACHELOR|MASTER|ASSOCIATE) OF ", line):
            info["program"] = (info["program"] + " · " if info["program"] else "") + _nice_title(line)
            continue

        if line.startswith("LEGEND"):
            break
        if STARS_RE.match(line) or SKIP_RE.match(line):
            continue

        next_line = audit[index + 1].strip() if index + 1 < len(audit) else ""
        header = HEADER_RE.match(line)
        # "NO MORE THAN ONE COURSE..." is a sentence, not a "NO" (incomplete) section
        sentence = header and re.match(r"^(MORE|LESS|FEWER)\b", header.group(2))
        is_header = (
            (header and not sentence and (STARS_RE.match(next_line) or header.group(1) in ("OK", "NO")))
            or (not header and STARS_RE.match(next_line))
            or line.startswith("GENERAL ELECTIVES")
        )
        if is_header:
            finish_block()
            status = HEADER_STATUS[header.group(1)] if header else "info"
            title = header.group(2) if header else line
            section = new_node(_nice_title(title), status)
            section["subs"] = []
            sections.append(section)
            node = section
            continue

        if section is None:
            continue  # top of the audit (name, ID, preparation date): skip

        # Course lists after "Select from:" / "NOT FROM:"
        if "Select from:" in line:
            finish_block()
            collecting = "select"
            rest = line.split("Select from:", 1)[1].strip()
            if rest:
                block.append(rest)
            continue
        if "NOT FROM:" in line:
            finish_block()
            collecting = "exclude"
            rest = line.split("NOT FROM:", 1)[1].strip()
            if rest:
                block.append(rest)
            continue
        if collecting:
            if CONTINUATION_RE.match(line):
                block.append(line)
                continue
            finish_block()

        course = TERM_COURSE_RE.match(line)
        if course:
            term, dept, number, credits, grade, title = course.groups()
            node["courses"].append({
                "term": term, "code": f"{dept} {number}", "credits": _number(credits),
                "grade": grade, "title": title.strip(),
            })
            continue

        needs = NEEDS_ANY_RE.search(line)
        if needs:
            value, unit = needs.groups()
            node["needs"] = _amount_text(value, unit)
            if unit.lower().startswith("credit"):
                node["count"] = {"credits": _number(value)}
            elif unit.lower() in ("course", "courses", "set", "sets"):
                node["count"] = int(float(value))
            continue

        earned = EARNED_RE.match(line)
        if earned:
            key = "earned" if earned.group(1) == "Earned" else "in_progress"
            node[key] = _amount_text(earned.group(2), earned.group(3))
            continue

        # Rules like "Only one 100G course will count toward the degree" start their own item
        if re.match(r"^Only (one|\d+) .* will count", line):
            finish_block()
            node = new_node(line, "info")
            section["subs"].append(node)
            continue

        sub = SUB_RE.match(line)
        if sub:
            finish_block()
            ip, mark, required, title = sub.groups()
            status = "in_progress" if ip else ("complete" if mark == "+" else "incomplete")
            title = re.sub(r"^\d+\)\s*", "", title)
            title = _nice_title(re.sub(r"[\s:-]+$", "", title)) if title.strip() else ""
            node = new_node(title, status, bool(required))
            section["subs"].append(node)
            continue

        node["notes"].append(_clean_note(re.sub(r"^ip\s+", "", line)))

    finish_block()

    # Build the course-option requirements (same format as required_course_scan)
    # and point each sub-requirement at its entry
    requirements = []
    for sec in sections:
        for item in [sec] + sec["subs"]:
            item["req"] = None
            if item["options"]:
                item["req"] = len(requirements)
                requirements.append([item["count"] if item["count"] is not None else 1] + item["options"])
            item["exclude"] = [c for c in item["exclude"] if not re.search(r"\d-\d", c)]
            del item["options"], item["count"]

    return info, sections, requirements


#-------------------------
# Runs all the scans on the audit text lines
#
# Returns:
#   completed_courses  - e.g. ["CS110", "MATH140"]
#   all_requirements   - every requirement found, BEFORE removing completed courses
#   required_courses   - requirements still open AFTER removing completed courses
#   info, sections     - the audit's layout for the website (see parse_audit_sections)
#
# The website uses all_requirements so that when a student adds or removes
# a completed course on the page, the open requirements update correctly.
#-------------------------

#-------------------------
# Total earned credits, from the first "Earned: 104.00Credits" line
# (used for prerequisites like "60 credits required")
#-------------------------

def earned_credits_scan(audit):
    credit_re = re.compile(r"Earned:\s*(\d+(?:\.\d+)?)\s*Credits?", re.IGNORECASE)
    for line in audit:
        match = credit_re.search(line)
        if match:
            return _number(match.group(1))
    return 0


def analyze_audit(audit):
    check_is_audit(audit)
    completed_courses = complete_course_scan(audit)
    info, sections, all_requirements = parse_audit_sections(audit)
    info["earned_credits"] = earned_credits_scan(audit)
    required_courses = remove_completed(completed_courses, all_requirements)
    return {
        "completed": completed_courses,
        "requirements": all_requirements,
        "remaining": required_courses,
        "info": info,
        "sections": sections,
    }


def analyze_lines(audit):
    completed_courses = complete_course_scan(audit)
    all_requirements = normalize_uncounted_requirements(required_course_scan(audit))
    required_courses = remove_completed(completed_courses, all_requirements)
    return completed_courses, all_requirements, required_courses


def scan_pdf(pdf_source):
    audit = read_pdf_lines(pdf_source)
    completed_courses, _, required_courses = analyze_lines(audit)
    return completed_courses, required_courses, audit

#-------------------------
# Testing
#
# Only runs when you run this file directly:  python pdf_scanner.py
# It does NOT run when app.py imports this file, so the website
# won't crash looking for degree_audit.pdf on Render.
#-------------------------

if __name__ == "__main__":
    completed_courses, required_courses, audit = scan_pdf("degree_audit.pdf")

    print("Completed Courses:")
    print(completed_courses)

    print()

    print("Required Courses:")
    print(required_courses)
