import pdfplumber
import re
from io import BytesIO
from typing import List, Union
from models import CourseUnit, RequirementGroup, DegreeAuditResult, KNOWN_GENED_CATEGORIES

#-----------------------------------------------------------------
# Description:
# Scans the pdf for courses and categorized them into either
# completed/in-progress and required courses
#
# Requirement:
# Need to run the following script to get pdfplumber for this to work
#   pip install pdfplumber
# 
# Developed with assistance from Google Gemini using agentic workflows
#-----------------------------------------------------------------

MAX_ALLOWED_PAGES = 20

def extract_text_from_pdf_stream(pdf_file: Union[BytesIO, bytes]) -> List[str]:
    """
    Streams PDF pages one-by-one and flushes pdfminer cache immediately after each page.
    Prevents Render 512MB Out-Of-Memory container crashes.
    """
    lines = []
    stream = BytesIO(pdf_file) if isinstance(pdf_file, (bytes, bytearray)) else pdf_file
    with pdfplumber.open(stream) as pdf:
        num_pages = min(len(pdf.pages), MAX_ALLOWED_PAGES)
        for i in range(num_pages):
            page = pdf.pages[i]
            text = page.extract_text()
            if text:
                lines.extend(text.splitlines())
            # Explicit cache flush to release LTTextBox layout trees immediately
            if hasattr(page, "flush_cache"):
                page.flush_cache()
    return lines

def clean_title_str(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"\([^)]*\)", "", text)
    cleaned = re.sub(r"^[\s:\-]+", "", cleaned)
    cleaned = re.sub(r"[\s:\-]+$", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()

def is_table_header_line(line: str) -> bool:
    clean = line.replace("|", " ").strip()
    norm = re.sub(r"\s+", " ", clean).lower()
    if "term course credits grade title" in norm or "title credits grade course term" in norm:
        return True
    words = set(re.findall(r"\b[a-z]+\b", norm))
    return "term" in words and "credits" in words and bool(words.intersection({"course", "grade", "title"}))

def is_needs_line(line: str) -> bool:
    clean = line.replace("|", " ").strip()
    return bool(re.match(r"^Needs:", clean, re.IGNORECASE))

def parse_requirement_header(line: str) -> str | None:
    clean = line.strip()
    if not clean:
        return None

    if re.search(r"\b(?:LEGEND|END OF ANALYSIS|SUMMARY OF COURSES|TABLE|COLLEGESOURCE|DEGREE AUDIT PROGRAM)\b", clean, re.IGNORECASE):
        return None
    if "=" in clean or clean.startswith("- =") or clean.startswith("NO =") or clean.startswith("OK ="):
        return None
    if re.match(r"^\*{4,}$", clean):
        return None

    if re.search(r"\b\d{2}(?:FL|SP|SU|WR)\b", clean) and re.search(r"\b\d+\.\d{2}\b", clean):
        return None
    if re.search(r"\b\d+\.\d{2}\s+(?:[A-D][+-]?|F|P|IP|T[A-D][+-]?|TIP|WAIVE)\b", clean, re.IGNORECASE):
        return None
    if re.match(r"^(?:[A-D][+-]?|T[A-D][+-]?|IP|TIP|P|F|\+|-|\+-|OK|NO)$", clean, re.IGNORECASE):
        return None

    if is_table_header_line(clean) or is_needs_line(clean):
        return None
    if re.match(r"^(?:Select from:|Earned:|In-Progress:|GPA\b)", clean, re.IGNORECASE):
        return None

    m1 = re.match(r"^(?:(?:ip\s*)?[-+*R]\s+)?(?:(\d+|OR))\)\s*(.*)$", clean, re.IGNORECASE)
    if m1:
        rest = m1.group(2).strip()
        return clean_title_str(rest)

    m2 = re.match(r"^(?:(?:ip\s*)?[-*]\s+)(.*)$", clean, re.IGNORECASE)
    if m2:
        rest = m2.group(1).strip()
        rest = re.sub(r"^(?:(?:\d+|OR)\))\s*", "", rest, flags=re.IGNORECASE)
        return clean_title_str(rest)

    if re.match(r"^(?!Take\b|Select\b|Choose\b)[A-Za-z0-9/ ]+\s+(?:Courses?|Requirements?)$", clean, re.IGNORECASE):
        if not re.search(r"\b(?:must be|completed|transferred|click on|grade of|check with|passed)\b", clean, re.IGNORECASE):
            return clean_title_str(clean)

    return None

def earned_credits_scan(audit):
    credit_re = re.compile(r"Earned:\s*(\d+(?:\.\d+)?)\s*Credits?", re.IGNORECASE)
    for line in audit:
        match = credit_re.search(line)
        if match:
            return float(match.group(1))
    return 0.0

def complete_course_scan(audit):
    completed_courses = []
    course_re = re.compile(
        r"(?:^\d{2}\s*(?:FL|SU|SP|WR)|\bWAIVE)\s+([A-Z]{2,8})\s*(\d{1,3}[A-Z]*)\b"
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

def required_course_scan(all_lines):
    required_courses = []
    needs_re = re.compile(
        r"Needs:.*?\b(\d+)\s*(Courses|Course|Sets|Set|Sub-Reqs|Sub-Req)\b",
        re.IGNORECASE
    )

    current_count = None
    collecting = False
    current_block_lines = []
    current_req_title = ""
    accumulating_title = False
    title_lines = []
    expecting_needs_count = False

    def finalize_block():
        nonlocal current_count, collecting, current_block_lines, current_req_title
        
        if collecting and current_block_lines:
            raw_text = " ".join(current_block_lines)
            cleaned = re.sub(r"\([^)]*\)", "", raw_text)
            cleaned = re.sub(r"\|", " ", cleaned)
            cleaned = re.sub(r"\bTERM\s+[A-Z0-9]{1,2}\s+COURSES\s*:?", " ", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"(\d{1,3}[A-Z]*)(?=[A-Z]{2,8}\b)", r"\1 ", cleaned)
            cleaned = re.sub(r"([A-Z]{2,8})(?=\d)", r"\1 ", cleaned)
            cleaned = re.sub(r"\bAND\b", "&", cleaned)
            cleaned = re.sub(r"[,;:]", " ", cleaned)
            
            tokens = cleaned.split()
            courses = []
            current_dept = None

            i = 0
            while i < len(tokens):
                tok = tokens[i].strip()

                if tok.isalpha():
                    if tok.lower() == "or":
                        i += 1
                        continue

                    is_valid_dept = tok.isupper() and (2 <= len(tok) <= 8)
                    if is_valid_dept:
                        has_subsequent_number = False
                        for forward_tok in tokens[i + 1 : i + 5]:
                            if re.match(r"^\d{1,3}[A-Z]*$", forward_tok):
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

                elif current_dept and re.match(r"^\d{1,3}[A-Z]*$", tok):
                    first_course = f"{current_dept}{tok}"
                    
                    if i + 2 < len(tokens) and tokens[i + 1].upper() == "TO":
                        next_tok = tokens[i + 2]
                        if re.match(r"^\d{1,3}[A-Z]*$", next_tok):
                            range_token = f"{first_course}-{current_dept}{next_tok}"
                            if range_token not in courses:
                                courses.append(range_token)
                            i += 3
                            continue
                        elif i + 3 < len(tokens) and re.match(r"^[A-Z]{2,8}$", next_tok) and re.match(r"^\d{1,3}[A-Z]*$", tokens[i + 3]):
                            pair_dept = next_tok
                            pair_num = tokens[i + 3]
                            range_token = f"{first_course}-{pair_dept}{pair_num}"
                            if range_token not in courses:
                                courses.append(range_token)
                            current_dept = pair_dept
                            i += 4
                            continue

                    if i + 2 < len(tokens) and tokens[i + 1].lower() == "or":
                        next_tok = tokens[i + 2]
                        if re.match(r"^\d{1,3}[A-Z]*$", next_tok):
                            pair = f"{first_course}|{current_dept}{next_tok}"
                            if pair not in courses:
                                courses.append(pair)
                            i += 3
                            continue
                        elif i + 3 < len(tokens) and re.match(r"^[A-Z]{2,8}$", next_tok) and re.match(r"^\d{1,3}[A-Z]*$", tokens[i + 3]):
                            pair_dept = next_tok
                            pair_num = tokens[i + 3]
                            pair = f"{first_course}|{pair_dept}{pair_num}"
                            if pair not in courses:
                                courses.append(pair)
                            current_dept = pair_dept
                            i += 4
                            continue

                    if i + 2 < len(tokens) and tokens[i + 1] == "&":
                        next_tok = tokens[i + 2]
                        if re.match(r"^\d{1,3}[A-Z]*$", next_tok):
                            pair = f"{first_course}&{current_dept}{next_tok}"
                            if pair not in courses:
                                courses.append(pair)
                            i += 3
                            continue
                        elif i + 3 < len(tokens) and re.match(r"^[A-Z]{2,8}$", next_tok) and re.match(r"^\d{1,3}[A-Z]*$", tokens[i + 3]):
                            pair_dept = next_tok
                            pair_num = tokens[i + 3]
                            pair = f"{first_course}&{pair_dept}{pair_num}"
                            if pair not in courses:
                                courses.append(pair)
                            current_dept = pair_dept
                            i += 4
                            continue

                    if first_course not in courses:
                        courses.append(first_course)

                i += 1

            if courses:
                required_courses.append({
                    "needed": current_count,
                    "courses": courses,
                    "title": current_req_title
                })
                current_count = None
                current_req_title = ""

        collecting = False
        current_block_lines = []

    for line in all_lines:
        clean_line = line.strip()
        if not clean_line:
            continue

        if accumulating_title:
            is_stop = False
            if is_table_header_line(clean_line):
                is_stop = True
            elif is_needs_line(clean_line) or needs_re.search(clean_line) or re.match(r"^\d+\s*(?:Courses?|Course|Sub-Reqs?|Sets?)$", clean_line, re.IGNORECASE):
                is_stop = True
            elif re.match(r"^(?:(?:ip\s*)?[-+*R]\s+)?(?:(?:\d+|OR)\))", clean_line, re.IGNORECASE) or clean_line.startswith("- "):
                is_stop = True
            elif "Select from:" in clean_line:
                is_stop = True
            elif (re.search(r"\b\d{2}(?:FL|SP|SU|WR)\b", clean_line) and re.search(r"\b\d+\.\d{2}\b", clean_line)) or re.match(r"^\*{4,}$", clean_line):
                is_stop = True

            if is_stop:
                accumulating_title = False
                current_req_title = clean_title_str(" ".join(title_lines))
                title_lines = []
            else:
                title_lines.append(clean_line)
                continue

        header_title = parse_requirement_header(clean_line)
        if header_title is not None:
            finalize_block()
            accumulating_title = True
            title_lines = [header_title] if header_title else []
            continue

        needs_match = needs_re.search(clean_line)
        if needs_match:
            finalize_block()
            current_count = int(needs_match.group(1))
            expecting_needs_count = False
            continue
        elif is_needs_line(clean_line):
            expecting_needs_count = True
            continue

        if expecting_needs_count:
            count_match = re.search(r"^\b(\d+)\s*(Courses|Course|Sets|Set|Sub-Reqs|Sub-Req)\b", clean_line, re.IGNORECASE)
            if count_match:
                finalize_block()
                current_count = int(count_match.group(1))
                expecting_needs_count = False
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

def normalize_uncounted_requirements(required_courses):
    normalized = []
    for item in required_courses:
        entry = dict(item)
        if entry.get("needed") is None:
            if normalized and normalized[-1].get("needed") is None:
                normalized[-1]["needed"] = 1
            entry["needed"] = 1
        normalized.append(entry)
    return normalized

GENED_SECTION_HEADERS = [
    r"GENERAL\s+EDUCATION\s+DISTRIBUTION",
    r"AREAS\s+OF\s+KNOWLEDGE"
]

def gen_ed_scan(all_lines: list) -> list[RequirementGroup]:
    gen_ed_groups = []
    in_gen_ed_section = False
    past_world_cultures = False
    current_category_name = None
    current_category_token = None

    needs_re = re.compile(r"Needs:\s*(\d+)\s*(?:Courses?|Course|Sub-Reqs?|Sets?)", re.IGNORECASE)
    header_re = re.compile(r"|".join(GENED_SECTION_HEADERS), re.IGNORECASE)
    exit_re = re.compile(r"(^[A-Z\s]+MAJOR\s*\*{4,}|SUMMARY OF COURSES TAKEN|LEGEND\b)", re.IGNORECASE)
    divider_re = re.compile(r"\*{8,}")

    for raw_line in all_lines:
        line = raw_line.strip()
        if not line:
            continue

        if in_gen_ed_section and past_world_cultures and divider_re.search(line):
            break

        clean = re.sub(r"^(?:NO|\+|OK|\-|\*|\|)\s*", "", line).strip()
        if not clean:
            continue

        if not in_gen_ed_section:
            if header_re.search(clean):
                in_gen_ed_section = True
            continue

        if exit_re.search(clean):
            break

        matched_cat = False
        for display_name, token_suffix in KNOWN_GENED_CATEGORIES:
            pattern = r"\b" + re.escape(display_name) + r"\b"
            if re.search(pattern, clean, re.IGNORECASE):
                if "'" not in clean and "COURSES DESIGNATED" not in clean.upper():
                    current_category_name = display_name
                    current_category_token = f"GENED:{token_suffix}"
                    matched_cat = True

                    if "WORLD" in token_suffix:
                        past_world_cultures = True
                    break

        if matched_cat:
            continue

        needs_match = needs_re.search(clean)
        if needs_match and current_category_token:
            needed_count = int(needs_match.group(1))

            if not any(g.units[0].raw_token == current_category_token for g in gen_ed_groups):
                gen_ed_groups.append(
                    RequirementGroup(
                        group_name=current_category_name,
                        courses_needed=needed_count,
                        units=[CourseUnit.from_token(current_category_token)]
                    )
                )

            current_category_token = None
            current_category_name = None

    return gen_ed_groups

def build_audit_result(all_lines: list) -> DegreeAuditResult:
    completed = set(complete_course_scan(all_lines))
    earned = earned_credits_scan(all_lines)
    raw_blocks = normalize_uncounted_requirements(required_course_scan(all_lines))

    requirement_groups: list[RequirementGroup] = []
    req_idx = 1
    for block in raw_blocks:
        needed = block.get("needed", 1)
        raw_candidates = block.get("courses", [])
        title = block.get("title", "").strip()

        active_units = []
        for token in raw_candidates:
            unit = CourseUnit.from_token(str(token))
            if not unit.is_completed(completed):
                active_units.append(unit)

        if active_units:
            group_name = f"Requirement {req_idx} - {title}" if title else f"Requirement {req_idx}"
            requirement_groups.append(
                RequirementGroup(
                    group_name=group_name,
                    courses_needed=min(needed, len(active_units)),
                    units=active_units
                )
            )
            req_idx += 1

    gened_groups = gen_ed_scan(all_lines)

    return DegreeAuditResult(
        completed_courses=completed,
        earned_credits=earned,
        requirements=requirement_groups,
        gen_ed_requirements=gened_groups
    )