import pdfplumber
import re

#-------------------------
# Scans the pdf for courses and categorized them into either
# completed/in-progress and required courses
# Heavily modify with Gemini Flash 3.8
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

    for line in audit:
        match = course_re.search(line.strip())
        if not match:
            continue

        dept, num = match.groups()
        course = f"{dept}{num}"

        if course not in completed_courses:
            completed_courses.append(course)

    return completed_courses

#-------------------------
# Figure out the required courses and returns a list of it in the where each requirement
# is another list starting with the number of required courses needed to take following
# with all the courses for that requirement
#-------------------------

def required_course_scan(all_lines):
    required_courses = []
    
    needs_re = re.compile(
        r"Needs:.*?\b(\d+)(Courses|Course|Sets|Set|Sub-Reqs|Sub-Req)\b"
    )

    current_count = None
    collecting = False
    current_block_lines = []

    def finalize_block():
        nonlocal current_count, collecting, current_block_lines
        if collecting and current_block_lines:
            raw_text = " ".join(current_block_lines)
            
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

                    is_valid_dept = tok.isupper() and (2 <= len(tok) <= 8)

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
            current_count = int(needs_match.group(1))
            continue

        if "Select from:" in clean_line:
            finalize_block()
            collecting = True
            after_select = clean_line.split("Select from:", 1)[1].strip()
            if after_select:
                current_block_lines.append(after_select)
            continue

        if collecting:
            current_block_lines.append(clean_line)

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
        if not (entry and isinstance(entry[0], int)):
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
    """
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
        has_count = isinstance(req[0], int)
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
#-------------------------

def scan_pdf(pdf_path):
    audit = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                audit.extend(page_text.splitlines())

    completed_courses = complete_course_scan(audit)
    required_courses = remove_completed(completed_courses, normalize_uncounted_requirements(required_course_scan(audit)))

    return completed_courses, required_courses, audit

#-------------------------
# Testing
#-------------------------

completed_courses, required_courses, audit = scan_pdf("degree_audit.pdf")

print("Completed Courses:")
print(completed_courses)

print()

print("Required Courses:")
print(required_courses)