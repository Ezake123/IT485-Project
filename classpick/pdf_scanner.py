import pdfplumber
import re
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

#-----------------------------------------------------------------
# Scans for total earned credits in the audit.
# Matches patterns like "Earned: 102.00Credits" or "Earned: 102.00 Credits"
#-----------------------------------------------------------------

MAX_ALLOWED_PAGES = 20 # Audits should not be any more pages than this

def earned_credits_scan(audit):
    # Regex looks for 'Earned:', optional spaces, a decimal or integer number, 
    # and optional spaces before 'Credits' (case-insensitive)
    credit_re = re.compile(r"Earned:\s*(\d+(?:\.\d+)?)\s*Credits?", re.IGNORECASE)

    for line in audit:
        match = credit_re.search(line)
        if match:
            return float(match.group(1))

    return 0.0

#-----------------------------------------------------------------
# Scans for completed/in-progress courses and return the list of them 
# concatnated with the course name and number without spaces
#-----------------------------------------------------------------

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

#-----------------------------------------------------------------
# Figure out the required courses and returns a list of it in the 
# where each requirement is another list starting with the number 
# of required courses needed to take following with all the courses 
# for that requirement
#-----------------------------------------------------------------

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
                    
                    # 1. For course range separated by "TO"
                    if i + 2 < len(tokens) and tokens[i + 1].upper() == "TO":
                        next_tok = tokens[i + 2]
                        
                        # Same dept implied: "210L TO 491"
                        if re.match(r"^\d{1,3}[A-Z]?$", next_tok):
                            range_token = f"{first_course}-{current_dept}{next_tok}"
                            if range_token not in courses:
                                courses.append(range_token)
                            i += 3
                            continue
                        
                        elif i + 3 < len(tokens) and re.match(r"^[A-Z]{2,8}$", next_tok) and re.match(r"^\d{1,3}[A-Z]?$", tokens[i + 3]):
                            pair_dept = next_tok
                            pair_num = tokens[i + 3]
                            range_token = f"{first_course}-{pair_dept}{pair_num}"
                            if range_token not in courses:
                                courses.append(range_token)
                            current_dept = pair_dept
                            i += 4
                            continue

                    # 2. Alternative pair separated by "or" 
                    if i + 2 < len(tokens) and tokens[i + 1].lower() == "or":
                        next_tok = tokens[i + 2]
                        
                        # Same dept implied: "210 or 212"
                        if re.match(r"^\d{1,3}[A-Z]?$", next_tok):
                            pair = f"{first_course}|{current_dept}{next_tok}"
                            if pair not in courses:
                                courses.append(pair)
                            i += 3
                            continue
                        
                        elif i + 3 < len(tokens) and re.match(r"^[A-Z]{2,8}$", next_tok) and re.match(r"^\d{1,3}[A-Z]?$", tokens[i + 3]):
                            pair_dept = next_tok
                            pair_num = tokens[i + 3]
                            pair = f"{first_course}|{pair_dept}{pair_num}"
                            if pair not in courses:
                                courses.append(pair)
                            current_dept = pair_dept
                            i += 4
                            continue

                    # 3. Corequisite pair separated by "&"
                    if i + 2 < len(tokens) and tokens[i + 1] == "&":
                        next_tok = tokens[i + 2]
                        
                        # Same dept implied: "252 & 256"
                        if re.match(r"^\d{1,3}[A-Z]?$", next_tok):
                            pair = f"{first_course}&{current_dept}{next_tok}"
                            if pair not in courses:
                                courses.append(pair)
                            i += 3
                            continue
                            
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

#-----------------------------------------------------------------
# For making the special case of unaccounted required courses
# usually caused by multiple select from without saying the required
# amount of courses before it
#-----------------------------------------------------------------

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

# -----------------------------------------------------------------
# Scans specifically for General Education distribution requirements
# -----------------------------------------------------------------

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

    # Definite exit marker: Only exit when reaching the Major block or End of Audit
    exit_re = re.compile(r"(^[A-Z\s]+MAJOR\s*\*{4,}|SUMMARY OF COURSES TAKEN|LEGEND\b)", re.IGNORECASE)
    # Stop marker for 8 or more asterisks
    divider_re = re.compile(r"\*{8,}")

    for raw_line in all_lines:
        line = raw_line.strip()
        if not line:
            continue

        # Check for 8+ asterisks divider exit after World Cultures/Languages
        if in_gen_ed_section and past_world_cultures and divider_re.search(line):
            break

        # Strip audit status prefixes like "+", "NO", "OK", "-", "|"
        clean = re.sub(r"^(?:NO|\+|OK|\-|\*|\|)\s*", "", line).strip()
        if not clean:
            continue

        # 1. Detect section start
        if not in_gen_ed_section:
            if header_re.search(clean):
                in_gen_ed_section = True
            continue

        # 2. Section exit check
        if exit_re.search(clean):
            break

        # 3. Match Gen Ed Category (Flexible search on stripped line)
        matched_cat = False
        for display_name, token_suffix in KNOWN_GENED_CATEGORIES:
            # Check if category name is present in this line
            pattern = r"\b" + re.escape(display_name) + r"\b"
            if re.search(pattern, clean, re.IGNORECASE):
                # Avoid matching the introductory legend list ('AR' ARTS, 'HU' HUMANITIES...)
                if "'" not in clean and "COURSES DESIGNATED" not in clean.upper():
                    current_category_name = display_name
                    current_category_token = f"GENED:{token_suffix}"
                    matched_cat = True

                    # Flag that we have entered/passed the World Cultures/Languages requirement
                    if "WORLD" in token_suffix:
                        past_world_cultures = True
                    break

        if matched_cat:
            continue

        # 4. Detect "Needs: X Course" under the current category
        needs_match = needs_re.search(clean)
        if needs_match and current_category_token:
            needed_count = int(needs_match.group(1))

            # Guard against duplicate additions if already recorded
            if not any(g.units[0].raw_token == current_category_token for g in gen_ed_groups):
                gen_ed_groups.append(
                    RequirementGroup(
                        group_name=current_category_name,
                        courses_needed=needed_count,
                        units=[CourseUnit.from_token(current_category_token)]
                    )
                )

            # Reset state for next category
            current_category_token = None
            current_category_name = None

    return gen_ed_groups

#-----------------------------------------------------------------
# Builds results based on the models
#-----------------------------------------------------------------

def build_audit_result(all_lines: list) -> DegreeAuditResult:
    completed = set(complete_course_scan(all_lines))
    earned = earned_credits_scan(all_lines)
    raw_blocks = normalize_uncounted_requirements(required_course_scan(all_lines))

    requirement_groups: list[RequirementGroup] = []

    # 1. Standard Major / Program Requirements
    for idx, block in enumerate(raw_blocks, start=1):
        if not block:
            continue
        has_count = isinstance(block[0], int)
        needed = block[0] if has_count else 1
        raw_candidates = block[1:] if has_count else block

        active_units = []
        for token in raw_candidates:
            unit = CourseUnit.from_token(str(token))
            if not unit.is_completed(completed):
                active_units.append(unit)

        if active_units:
            requirement_groups.append(
                RequirementGroup(
                    group_name=f"Requirement {idx}",
                    courses_needed=min(needed, len(active_units)),
                    units=active_units
                )
            )

    # 2. Scanned Gen Ed Distribution Requirements
    gened_groups = gen_ed_scan(all_lines)

    return DegreeAuditResult(
        completed_courses=completed,
        earned_credits=earned,
        requirements=requirement_groups,
        gen_ed_requirements=gened_groups
    )