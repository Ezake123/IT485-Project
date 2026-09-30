import asyncio
import json
import re
import unicodedata
from datetime import datetime
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

#-----------------------------------------------------------------
# Descriptions:
#
# - Scans the UMB course listings using their website: https://online.umb.edu/courses/
# - Saves the courses and their data as JSON file for now, and will implement to connect with SupaBase later
# - Created with Gemini Flash 3.8 using agentic engineering
#
# Require to run these following commands for this script to work:
#
#   pip install playwright beautifulsoup4
#   playwright install chromium
# 
# How to use it:
# Type "python UMB_course_scraper.py" to run and press "Ctrl + C" to end it early
#-----------------------------------------------------------------

BASE_URL = "https://online.umb.edu/courses/"    # The website where it scans for courses
OUTPUT_FILE = "umb_courses.json"                # Temporary file, will be removed later to solely input into Supabase
MAX_SCRAPE_TIME_SECONDS = None                  # Set to a number of seconds (e.g., 1800) if you want an auto-timeout
CONCURRENCY_LIMIT = 6                           # Number of course detail tabs processing simultaneously

#-----------------------------------------------------------------
# Changes the Unicode characters into normal and common 
# characters strictly only the ones found on keyboard
#-----------------------------------------------------------------

def clean_text(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = re.sub(r"[\u2013\u2014\u2212\u2010\u2011\u2012]", "-", text)
    text = re.sub(r"[\s\xa0]+", " ", text)
    return text.strip()

#-----------------------------------------------------------------
# Splits course identifier into course_name and course_number
#-----------------------------------------------------------------

def split_course_identifier(raw_name: str) -> tuple[str, str]:
    raw_name = clean_text(raw_name)
    match = re.search(r"^([A-Z0-9\- ]+?)\s*(\d{1,4}[A-Z]?)$", raw_name, re.IGNORECASE)
    if match:
        course_name = match.group(1).strip().upper()
        course_number = match.group(2).strip().upper()
        return course_name, course_number
    return raw_name, ""


#-----------------------------------------------------------------
# Converts credits string into a numeric float/int or None
#-----------------------------------------------------------------

def parse_credits(raw_credits: str) -> int | None:
    if not raw_credits:
        return None
    match = re.search(r"\d", raw_credits)
    if match:
        return int(match.group(0))
    return None

#-----------------------------------------------------------------
# Changes the time format since they do weird format like "p.m."
#-----------------------------------------------------------------

def format_clock_time(raw: str, default_period: str = "") -> str | None:
    if not raw or raw.upper() in ["TBA", "ONLINE", "TBD", "NONE"]:
        return None

    raw = clean_text(raw).strip()

    # Determine AM/PM
    period = ""
    if re.search(r"a\.?m\.?", raw, re.IGNORECASE):
        period = "AM"
    elif re.search(r"p\.?m\.?", raw, re.IGNORECASE):
        period = "PM"
    elif default_period:
        period = default_period

    time_match = re.search(r"(\d{1,2})(?::(\d{2}))?", raw)
    if not time_match:
        return None

    hours = int(time_match.group(1))
    minutes = int(time_match.group(2)) if time_match.group(2) else 0

    # Convert to 24-hour format
    if period == "PM" and hours < 12:
        hours += 12
    elif period == "AM" and hours == 12:
        hours = 0

    return f"{hours:02d}:{minutes:02d}:00"

#-----------------------------------------------------------------
# Converts specificly the schedule into 3 values of days (days of 
# the week), starting time, and ending time because UMB put them all 
# onto one string like "We 5p.m. - 9p.m."
#-----------------------------------------------------------------

def parse_schedule(raw_time_str: str) -> tuple[list[str], str | None, str | None]:
    raw_time_str = clean_text(raw_time_str)
    if not raw_time_str or raw_time_str.upper() in ["TBA", "ONLINE", "TBD", "NONE"]:
        return [], None, None

    # Match consecutive standard day abbreviations: Mo, Tu, We, Th, Fr, Sa, Su
    day_match = re.match(r"^((?:Mo|Tu|We|Th|Fr|Sa|Su)+)\s*(.*)$", raw_time_str, re.IGNORECASE)
    days_list = []
    time_part = raw_time_str

    if day_match:
        # Extract every two-character token
        days_str = day_match.group(1).strip()
        days_list = [d.capitalize() for d in re.findall(r"(?:Mo|Tu|We|Th|Fr|Sa|Su)", days_str, re.IGNORECASE)]
        time_part = day_match.group(2).strip()

    if "-" in time_part:
        parts = [p.strip() for p in time_part.split("-", 1)]
        raw_start = parts[0]
        raw_end = parts[1] if len(parts) > 1 else ""

        end_period = ""
        if re.search(r"p\.?m\.?", raw_end, re.IGNORECASE):
            end_period = "PM"
        elif re.search(r"a\.?m\.?", raw_end, re.IGNORECASE):
            end_period = "AM"

        end_time = format_clock_time(raw_end, default_period=end_period)
        start_time = format_clock_time(raw_start, default_period=end_period)
    elif time_part:
        start_time = format_clock_time(time_part)
        end_time = None
    else:
        start_time, end_time = None, None

    return days_list, start_time, end_time

#-----------------------------------------------------------------
# Converts credits text into two separate values of maximum capacity 
# and the enrolled amount
#-----------------------------------------------------------------

def parse_capacity(raw_capacity: str) -> tuple[int | None, int | None]:
    if not raw_capacity:
        return None, None

    # Find all sequences of digits
    digits = [int(n) for n in re.findall(r"\d+", raw_capacity)]

    if len(digits) >= 2:
        enrolled, maximum_capacity = digits[0], digits[1]
        return maximum_capacity, enrolled
    elif len(digits) == 1:
        # If only one number is provided, assume it is maximum capacity with 0 registered
        return digits[0], 0

    return None, None

#-----------------------------------------------------------------
# Cleans up prerequisites into:
# - raw: Full original text
# - courses: Conjunctive Normal Form (AND groups of OR options)
# - min_credits: Required credits threshold (int or None)
# - has_other_restrictions: True if non-course requirements exist
#   like "CS student", it is hard to make a case for everything as
#   the text are just inconsistant
#-----------------------------------------------------------------

def clean_prerequisites(raw_text: str) -> dict:
    cleaned_raw = raw_text.strip() if raw_text else ""
    
    if not cleaned_raw or cleaned_raw.upper() in ["NONE", "N/A", "TBA"]:
        return {
            "raw": cleaned_raw or "None",
            "courses": [],
            "min_credits": None,
            "has_other_restrictions": False
        }

    # 1. Extract credit threshold (e.g., "minimum of 60 credits", "15 credits")
    min_credits = None
    credit_match = re.search(r"(\d{1,3})\s*(?:or more\s*)?(?:degree\s*)?credits", cleaned_raw, re.IGNORECASE)
    if credit_match:
        min_credits = int(credit_match.group(1))

    # 2. Detect other non-course restrictions (majors, standings, permissions, auditions)
    restriction_patterns = [
        r"\b(?:major|minor|student|matriculat\w+|status|standing|level|senior|junior|sophomore|freshman|graduate)\b",
        r"\b(?:permission|consent|audition|prerequisite\s*test|placement\s*test|score|wpe|exam)\b",
        r"\b(?:college\s+of|mgt|cm|degree\s+students?\s+only)\b"
    ]
    has_other_restrictions = any(
        re.search(pat, cleaned_raw, re.IGNORECASE) for pat in restriction_patterns
    )

    # 3. Text cleanup for course extraction
    normalized_text = cleaned_raw

    # Normalize missing spaces in course codes (e.g. "ENGL101" -> "ENGL 101", "104or" -> "104 or")
    normalized_text = re.sub(r"([A-Za-z]{2,8})(\d{2,4}[A-Za-z]?)", r"\1 \2", normalized_text)
    normalized_text = re.sub(r"(\d{1,4}[A-Za-z]?)(or|and)\b", r"\1 \2", normalized_text, flags=re.IGNORECASE)

    # Expand slash pairs (e.g. "BIOL 252/254" -> "BIOL 252 or BIOL 254")
    def expand_slash(match):
        dept = match.group(1)
        num1 = match.group(2)
        num2 = match.group(3)
        return f"{dept} {num1} or {dept} {num2}"
    normalized_text = re.sub(r"\b([A-Z]{2,8}(?:-[A-Z]+)?)\s*(\d{1,4}[A-Z]?)/(\d{1,4}[A-Z]?)\b", expand_slash, normalized_text)

    # Standardize conjunctions
    normalized_text = re.sub(r"&", " and ", normalized_text)
    normalized_text = re.sub(r"(?i)\bco-?requisite\b", "", normalized_text)

    # 4. Parse course codes into CNF groups (AND clauses containing OR alternatives)
    and_clauses = re.split(r"\band\b|;|\.", normalized_text, flags=re.IGNORECASE)
    structured_courses = []
    last_dept = None

    for clause in and_clauses:
        or_options = re.split(r"\bor\b|,", clause, flags=re.IGNORECASE)
        or_group = []

        for segment in or_options:
            matches = list(re.finditer(r"\b([A-Z]{2,8}(?:-[A-Z]+)?)\s*(\d{1,4}[A-Z]?)\b", segment))
            for m in matches:
                dept = m.group(1).upper()
                num = m.group(2).upper()
                last_dept = dept
                course_obj = {"course_name": dept, "course_number": num}
                if course_obj not in or_group:
                    or_group.append(course_obj)

            # Catch inherited department numbers (e.g., "AF 210 or 211")
            if not matches and last_dept:
                inherited_matches = re.finditer(r"\b(\d{3}[A-Z]?)\b", segment)
                for im in inherited_matches:
                    course_obj = {"course_name": last_dept, "course_number": im.group(1).upper()}
                    if course_obj not in or_group:
                        or_group.append(course_obj)

        if or_group:
            structured_courses.append(or_group)

    return {
        "raw": cleaned_raw,
        "courses": structured_courses,
        "min_credits": min_credits,
        "has_other_restrictions": has_other_restrictions
    }

#-----------------------------------------------------------------
# Scans through the rest of the webpage and gather the rest of the
# data needed for the course by searching specific element name from
# the html code
#-----------------------------------------------------------------

def parse_course_detail(html_content: str) -> dict:
    soup = BeautifulSoup(html_content, "html.parser")

    # 1. Gets Course Code, Class Code, and Section Number from the navigation bar
    last_li = soup.select_one(".course-breadcrumbs ul li:last-child")
    breadcrumb_text = clean_text(last_li.get_text()) if last_li else ""
    if not breadcrumb_text:
        bc_elem = soup.select_one(".breadcrumbs, nav, .l-page-sidebar")
        breadcrumb_text = clean_text(bc_elem.get_text()) if bc_elem else ""

    # Slice before 'Class #' to preserve hyphens and spaces
    if "Class #" in breadcrumb_text:
        raw_identifier = breadcrumb_text.split("Class #")[0].strip()
    elif "Class" in breadcrumb_text:
        raw_identifier = breadcrumb_text.split("Class")[0].strip()
    else:
        match = re.search(r"((?:[A-Z0-9\-]+\s+)+\d{1,4}[A-Z]?)", breadcrumb_text)
        raw_identifier = match.group(1).strip() if match else ""

    course_name, course_number = split_course_identifier(raw_identifier)

    class_match = re.search(r"Class\s*#?\s*(\d+)", breadcrumb_text, re.IGNORECASE)
    section_match = re.search(r"Section\s*([0-9A-Za-z]+)", breadcrumb_text, re.IGNORECASE)

    class_code = class_match.group(1).strip() if class_match else ""
    section = section_match.group(1).strip() if section_match else ""

    # 2. Overview table: #courseOverview
    days, start_time, end_time, location, raw_credits = [], None, None, "", ""
    overview_table = soup.select_one("table#courseOverview")
    if overview_table:
        cells = overview_table.select("tbody tr td")
        if len(cells) >= 3:
            dt_cell = cells[0]
            for br in dt_cell.find_all("br"):
                br.replace_with("\n")
            lines = [clean_text(l) for l in dt_cell.get_text().split("\n") if clean_text(l)]

            for line in lines:
                if re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", line):
                    continue
                parsed_days, parsed_start, parsed_end = parse_schedule(line)
                if parsed_days or parsed_start is not None:
                    days = parsed_days
                    start_time = parsed_start
                    end_time = parsed_end
                    break
                elif line.upper() == "TBA":
                    days, start_time, end_time = [], None, None

            location = clean_text(cells[1].get_text())
            raw_credits = clean_text(cells[2].get_text())

    credits_val = parse_credits(raw_credits)

    # 3. Gets Description, Prerequisites, and Gen Ed
    desc_elem = soup.select_one("#courseDescription")
    description = clean_text(desc_elem.get_text()) if desc_elem else ""

    prereq_elem = soup.select_one("#coursePrereq")
    raw_prerequisites = clean_text(prereq_elem.get_text()) if prereq_elem else "None"
    prerequisites = clean_prerequisites(raw_prerequisites)

    gened_elem = soup.select_one("#genedrequirements")
    gen_ed = clean_text(gened_elem.get_text()) if gened_elem else None

    # 4. Gets Enrolled/Capacity & Instructors from the Course Details section
    raw_capacity = ""
    instructors = []
    details_grid = soup.select_one("#courseDetails")
    if details_grid:
        for child_div in details_grid.find_all("div", recursive=False):
            div_text = clean_text(child_div.get_text())
            if "Enrolled / Capacity" in div_text or "Capacity" in div_text:
                cap_match = re.search(r"(\d+\s*/\s*\d+|\d+)", div_text)
                if cap_match:
                    raw_capacity = cap_match.group(1).replace(" ", "")
                break

        inst_p = details_grid.select_one("#instructors")
        if inst_p:
            for a in inst_p.find_all("a"):
                name = clean_text(a.get_text())
                if name and name not in instructors:
                    instructors.append(name)
            if not instructors:
                for br in inst_p.find_all("br"):
                    br.replace_with("\n")
                instructors = [clean_text(l) for l in inst_p.get_text().split("\n") if clean_text(l)]

    # Parse into integers
    maximum_capacity, enrolled = parse_capacity(raw_capacity)

    return {
        "course_name": course_name,
        "course_number": course_number,
        "class_code": class_code,
        "section": section,
        "days": days,
        "start_time": start_time,
        "end_time": end_time,
        "location": location,
        "credits": credits_val,
        "description": description,
        "gen_ed": gen_ed,
        "prerequisites": prerequisites,
        "maximum_capacity": maximum_capacity,
        "enrolled": enrolled,
        "instructors": instructors
    }

#-----------------------------------------------------------------
# Prepares and opens the course detail page from the original webpage
#-----------------------------------------------------------------

async def fetch_course(context, url: str, semaphore: asyncio.Semaphore):
    async with semaphore:
        page = await context.new_page()
        # Abort images, fonts, and stylesheets to maximize throughput
        await page.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in ["image", "media", "font"]
            else route.continue_()
        )
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_selector("#courseContent, table#courseOverview", timeout=4000)
            content = await page.content()
            return parse_course_detail(content)
        except Exception as e:
            print(f"  Skipping {url} (Error: {e})")
            return None
        finally:
            await page.close()

#-----------------------------------------------------------------
# Create the filename with the year and semester of courses it is scraping
#-----------------------------------------------------------------

def generate_output_filename(season_str: str) -> str:
    year = str(datetime.now().year)
    season = season_str.capitalize()
    return f"{year}_{season}_courses.json"

#-----------------------------------------------------------------
# Saves records to JSON file (will be removed latered due to storing it on a database)
#-----------------------------------------------------------------

def save_and_display(results: list, output_filename: str):
    if not results:
        print("\nNo course records were scraped.")
        return

    with open(output_filename, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 40)
    print(f"SAVED {len(results)} RECORDS TO {output_filename}")
    print("=" * 40)

#-----------------------------------------------------------------
# Starts the scraper process and prints results while it runs
#-----------------------------------------------------------------

async def run_scraper():
    results = []
    seen = set()
    browser = None
    output_file = "courses.json"

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            catalog_page = await context.new_page()

            print(f"Loading initial landing page: {BASE_URL}...")
            await catalog_page.goto(BASE_URL, wait_until="networkidle")

            # 1. Inspect the hero banner matching <div class="hero_title">
            hero_elem = catalog_page.locator(".hero_title, .hero__title")
            hero_text = ""
            if await hero_elem.count() > 0:
                hero_text = (await hero_elem.first.inner_text()).strip()

            print(f"Hero banner found: '{hero_text}'")

            # 2. Extract the season token ('Fall', 'Spring', 'Summer', 'Winter')
            match = re.search(r"\b(Fall|Spring|Summer|Winter)\b", hero_text, re.IGNORECASE)
            season = match.group(1).capitalize() if match else "Unknown"
            season_lower = season.lower()  # 'fall', 'spring', 'summer', 'winter'

            # 3. Construct the clean term base URL and output filename
            term_base_url = f"https://online.umb.edu/courses/{season_lower}/"
            output_file = generate_output_filename(season)
            print(f"Active Term: '{season}' | Target URL: '{term_base_url}' | Output File: '{output_file}'")

            semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
            page_num = 1

            # 4. Iterate pages: https://online.umb.edu/courses/fall/?page=1&
            while True:
                paged_url = f"{term_base_url}?page={page_num}&"
                print(f"\n--- [Page {page_num}] Fetching: {paged_url} ---")
                await catalog_page.goto(paged_url, wait_until="networkidle")

                # Check if catalog end reached
                no_results = catalog_page.locator(
                    '#search-results:has-text("No results found"), '
                    '.results_main:has-text("No results found"), '
                    ':has-text("No courses found")'
                )
                if await no_results.count() > 0:
                    print(f"Reached end of catalog (No results found on page {page_num}).")
                    break

                # Expand accordions on current page
                multi_buttons = catalog_page.locator(
                    'button:has-text("Multiple Sections"), '
                    'a:has-text("Multiple Sections"), '
                    'tr:has-text("Multiple Sections")'
                )
                count_multi = await multi_buttons.count()
                for i in range(count_multi):
                    try:
                        btn = multi_buttons.nth(i)
                        if await btn.is_visible():
                            await btn.click()
                            await catalog_page.wait_for_timeout(50)
                    except Exception:
                        continue

                # Collect detail URLs
                page_html = await catalog_page.content()
                soup = BeautifulSoup(page_html, "html.parser")
                current_page_links = []

                for a in soup.select("a[href*='/detail/']"):
                    href = a.get("href")
                    if href:
                        full_url = urljoin(paged_url, href)
                        if full_url not in seen:
                            seen.add(full_url)
                            current_page_links.append(full_url)

                if not current_page_links:
                    print(f"No detail links found on page {page_num}. Ending pagination.")
                    break

                print(f"Found {len(current_page_links)} courses on Page {page_num}. Scraping ({CONCURRENCY_LIMIT} workers)...")

                tasks = [fetch_course(context, url, semaphore) for url in current_page_links]
                page_results = await asyncio.gather(*tasks)

                valid_entries = [r for r in page_results if r is not None]
                results.extend(valid_entries)

                # Incremental auto-save into 2026_Fall_courses.json
                with open(output_file, "w", encoding="utf-8") as f:
                    json.dump(results, f, indent=2, ensure_ascii=False)
                print(f"Page {page_num} completed. Total courses saved so far: {len(results)}")

                page_num += 1

            await catalog_page.close()

    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n\n[!] Manual interruption detected (Ctrl + C). Stopping scraper gracefully...")
    except Exception as e:
        print(f"\n[!] Unexpected error encountered: {e}")
    finally:
        save_and_display(results, output_file)
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
                
if __name__ == "__main__":
    try:
        if MAX_SCRAPE_TIME_SECONDS:
            asyncio.run(asyncio.wait_for(run_scraper(), timeout=MAX_SCRAPE_TIME_SECONDS))
        else:
            asyncio.run(run_scraper())
    except (KeyboardInterrupt, asyncio.TimeoutError):
        pass