import asyncio
import json
import re
import unicodedata
import random
from datetime import datetime
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

#-----------------------------------------------------------------
# Descriptions:
# - Scans the UMB course listings using their website
# - Saves the courses and their data as JSON file for now, and will implement to connect with SupaBase later
#
# Require to run these following commands for this script to work:
#
#   pip install playwright beautifulsoup4
#   playwright install chromium
# 
# How to use it:
# Type "python umb_course_scraper.py" to run and enter semester and year
# and press "Ctrl + C" to end it early
#
# Developed with assistance from Google Gemini using agentic workflows
#-----------------------------------------------------------------

BASE_URL = "https://courses.umb.edu/search/"
MAX_PAGES = 55
CONCURRENCY_LIMIT = 2 # reduced for the new website
MAX_SCRAPE_TIME_SECONDS = None

# -----------------------------------------------------------------
# Normalizes Unicode characters and strips extra whitespace
# -----------------------------------------------------------------
def clean_text(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = re.sub(r"[\u2013\u2014\u2212\u2010\u2011\u2012]", "-", text)
    text = re.sub(r"[\s\xa0]+", " ", text)
    return text.strip()

# -----------------------------------------------------------------
# Splits course identifier (e.g. "ABA 650") into subject & number
# -----------------------------------------------------------------
def split_course_identifier(raw_name: str) -> tuple[str, str]:
    raw_name = clean_text(raw_name)
    match = re.search(r"^([A-Z0-9\- ]+?)\s*(\d{1,4}[A-Z]?)$", raw_name, re.IGNORECASE)
    if match:
        return match.group(1).strip().upper(), match.group(2).strip().upper()
    return raw_name, ""

# -----------------------------------------------------------------
# Converts credits string to integer; if range (e.g. "1-3"), gets the lower credit
# -----------------------------------------------------------------
def parse_credits(raw_credits: str) -> int | None:
    if not raw_credits:
        return None
    cleaned = clean_text(raw_credits)
    # Match sequences of digits to pick the lowest if a range is present
    digits = re.findall(r"\d+", cleaned)
    if digits:
        return int(digits[0])
    return None

# -----------------------------------------------------------------
# Converts clock strings like "12:30p.m." or "5p.m." to 24-hr format
# -----------------------------------------------------------------
def format_clock_time(raw: str, default_period: str = "") -> str | None:
    if not raw or raw.upper() in ["TBA", "ONLINE", "TBD", "NONE"]:
        return None

    raw = clean_text(raw).strip()

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

    if period == "PM" and hours < 12:
        hours += 12
    elif period == "AM" and hours == 12:
        hours = 0

    return f"{hours:02d}:{minutes:02d}:00"

# -----------------------------------------------------------------
# Parses schedule text (e.g., "TuTh 12:30p.m. - 1:45p.m.")
# -----------------------------------------------------------------
def parse_schedule(raw_time_str: str) -> tuple[list[str], str | None, str | None]:
    raw_time_str = clean_text(raw_time_str)
    if not raw_time_str or raw_time_str.upper() in ["TBA", "ONLINE", "TBD", "NONE"]:
        return [], None, None

    # Matches days abbreviation: Mo, Tu, We, Th, Fr, Sa, Su
    day_match = re.match(r"^((?:Mo|Tu|We|Th|Fr|Sa|Su)+)\s*(.*)$", raw_time_str, re.IGNORECASE)
    days_list = []
    time_part = raw_time_str

    if day_match:
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

# -----------------------------------------------------------------
# Parses course prerequisites into structured CNF format and metadata
# -----------------------------------------------------------------
def clean_prerequisites(raw_text: str) -> dict:
    cleaned_raw = raw_text.strip() if raw_text else ""
    if not cleaned_raw or cleaned_raw.upper() in ["NONE", "N/A", "TBA"]:
        return {
            "raw": cleaned_raw or "None",
            "courses": [],
            "min_credits": None,
            "has_other_restrictions": False
        }

    min_credits = None
    credit_match = re.search(r"(\d{1,3})\s*(?:or more\s*)?(?:degree\s*)?credits", cleaned_raw, re.IGNORECASE)
    if credit_match:
        min_credits = int(credit_match.group(1))

    restriction_patterns = [
        r"\b(?:major|minor|student|matriculat\w+|status|standing|level|senior|junior|sophomore|freshman|graduate)\b",
        r"\b(?:permission|consent|audition|prerequisite\s*test|placement\s*test|score|wpe|exam)\b",
        r"\b(?:college\s+of|mgt|cm|degree\s+students?\s+only)\b"
    ]
    has_other_restrictions = any(
        re.search(pat, cleaned_raw, re.IGNORECASE) for pat in restriction_patterns
    )

    normalized_text = cleaned_raw
    normalized_text = re.sub(r"([A-Za-z]{2,8})(\d{2,4}[A-Za-z]?)", r"\1 \2", normalized_text)
    normalized_text = re.sub(r"(\d{1,4}[A-Za-z]?)(or|and)\b", r"\1 \2", normalized_text, flags=re.IGNORECASE)

    def expand_slash(match):
        dept = match.group(1)
        num1 = match.group(2)
        num2 = match.group(3)
        return f"{dept} {num1} or {dept} {num2}"

    normalized_text = re.sub(r"\b([A-Z]{2,8}(?:-[A-Z]+)?)\s*(\d{1,4}[A-Z]?)/(\d{1,4}[A-Z]?)\b", expand_slash, normalized_text)
    normalized_text = re.sub(r"&", " and ", normalized_text)
    normalized_text = re.sub(r"(?i)\bco-?requisite\b", "", normalized_text)

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

# -----------------------------------------------------------------
# Parses course details directly from HTML
# -----------------------------------------------------------------
def parse_course_detail(html_content: str) -> dict:
    soup = BeautifulSoup(html_content, "html.parser")

    # 1. Course Code, Class Code, and Section Number from p.font-bold inside #courseContent
    course_name, course_number, class_code, section = "", "", "", ""
    font_bold_elem = soup.select_one("#courseContent p.font-bold")
    if font_bold_elem:
        bold_text = clean_text(font_bold_elem.get_text())
        # Matches: "Course #: ABA 650, Class #: 4802, Section #: 01"
        c_match = re.search(r"Course\s*#?:\s*([A-Za-z0-9\- ]+?)(?:,|$)", bold_text, re.IGNORECASE)
        cl_match = re.search(r"Class\s*#?:\s*([0-9]+)", bold_text, re.IGNORECASE)
        sec_match = re.search(r"Section\s*#?:\s*([0-9A-Za-z]+)", bold_text, re.IGNORECASE)

        if c_match:
            course_name, course_number = split_course_identifier(c_match.group(1))
        if cl_match:
            class_code = cl_match.group(1).strip()
        if sec_match:
            section = sec_match.group(1).strip()

    # 2. Description (#courseDescription)
    desc_elem = soup.select_one("#courseDescription")
    description = clean_text(desc_elem.get_text()) if desc_elem else ""

    # 3. Prerequisites (#coursePrereq)
    prereq_elem = soup.select_one("#coursePrereq")
    raw_prerequisites = clean_text(prereq_elem.get_text()) if prereq_elem else "None"
    prerequisites = clean_prerequisites(raw_prerequisites)

    # 4. General Education Requirements (#genedrequirements)
    gened_elem = soup.select_one("#genedrequirements")
    raw_gened = clean_text(gened_elem.get_text()) if gened_elem else ""
    gen_ed = raw_gened if raw_gened and raw_gened.upper() not in ["NONE", "N/A"] else None

    # 5. Course Details Grid (#courseDetails)
    days, start_time, end_time = [], None, None
    location = ""
    credits_val = None
    maximum_capacity, enrolled = None, None
    instructors = []

    details_grid = soup.select_one("#courseDetails")
    if details_grid:
        for card in details_grid.find_all("div", recursive=False):
            header = card.find("h4")
            if not header:
                continue
            header_title = clean_text(header.get_text()).lower()
            p_tag = card.find("p")
            if not p_tag:
                continue

            # Date / Time
            if "date" in header_title and "time" in header_title:
                for br in p_tag.find_all("br"):
                    br.replace_with("\n")
                lines = [clean_text(l) for l in p_tag.get_text().split("\n") if clean_text(l)]
                for line in lines:
                    # Skip date ranges like 1/25/27 - 5/12/27
                    if re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", line):
                        continue
                    p_days, p_start, p_end = parse_schedule(line)
                    if p_days or p_start is not None:
                        days = p_days
                        start_time = p_start
                        end_time = p_end
                        break

            # Location (first occurrence takes precedence)
            elif "location" in header_title and not location:
                location = clean_text(p_tag.get_text())

            # Credits
            elif "credit" in header_title:
                credits_val = parse_credits(p_tag.get_text())

            # Instructor(s)
            elif "instructor" in header_title:
                inst_links = p_tag.find_all("a")
                if inst_links:
                    for a in inst_links:
                        name = clean_text(a.get_text())
                        if name and name not in instructors:
                            instructors.append(name)
                else:
                    for br in p_tag.find_all("br"):
                        br.replace_with("\n")
                    names = [clean_text(l) for l in p_tag.get_text().split("\n") if clean_text(l)]
                    for name in names:
                        if name and name not in instructors:
                            instructors.append(name)

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
        "instructors": instructors
    }

# -----------------------------------------------------------------
# Fetches course detail using an existing, persistent page object
# -----------------------------------------------------------------
async def fetch_course(page, url: str):
    # Human-like delay between course views
    await asyncio.sleep(random.uniform(1.2, 2.5))
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        if response and response.status == 403:
            print(f"  [403 Blocked] CloudFront rate-limited {url}. Backing off for 30s...")
            await asyncio.sleep(30)
            # Retry once after cooling down
            response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if response and response.status == 403:
                return None

        await page.wait_for_selector(
            "#courseContent, #courseDetails, .courseDetailsWrapper, p.font-bold, h2.course-overview-header",
            state="attached",
            timeout=12000
        )

        content = await page.content()
        return parse_course_detail(content)
    except Exception as e:
        print(f"  Skipping {url} (Error: {e})")
        return None

# -----------------------------------------------------------------
# Saves records to JSON file
# -----------------------------------------------------------------
def save_and_display(results: list, output_filename: str):
    if not results:
        print("\nNo course records were scraped.")
        return

    with open(output_filename, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 40)
    print(f"SAVED {len(results)} RECORDS TO {output_filename}")
    print("=" * 40)

# -----------------------------------------------------------------
# Main execution workflow
# -----------------------------------------------------------------
async def run_scraper():
    semester = input("Enter semester (e.g. spring, fall): ").strip().lower()
    year = input("Enter year (e.g. 2027): ").strip()

    search_base_url = f"{BASE_URL}{semester}-{year}"
    output_file = f"{year}_{semester.capitalize()}_courses.json"

    results = []
    scraped_urls = set()
    
    # Load existing progress if restarting
    try:
        with open(output_file, "r", encoding="utf-8") as f:
            results = json.load(f)
            scraped_urls = {c.get("url") for c in results if isinstance(c, dict) and "url" in c}
            print(f"Resuming: Loaded {len(results)} courses already scraped.")
    except FileNotFoundError:
        pass

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            )
            await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            catalog_page = await context.new_page()

            # PHASE 1: Fast scan to collect unique URLs and their Status from the table
            detail_links = []
            status_map = {}  # Maps URL -> Status (Open, Closed, Waitlist)
            
            print("\n--- PHASE 1: Discovering Course Links & Statuses ---")
            for page_num in range(1, MAX_PAGES + 1):
                paged_url = f"{search_base_url}/?page={page_num}&"
                print(f"Checking Catalog Page {page_num}...")
                resp = await catalog_page.goto(paged_url, wait_until="networkidle", timeout=30000)
                if resp and resp.status == 403:
                    print("CloudFront block encountered during index sweep. Pausing...")
                    break

                body_text = await catalog_page.inner_text("body")
                if "No results found. Please try searching again with a different keyword." in body_text:
                    print(f"End of catalog reached at page {page_num}.")
                    break

                page_html = await catalog_page.content()
                soup = BeautifulSoup(page_html, "html.parser")
                
                # Grab the link and its corresponding status column from the table row
                for tr in soup.select("tbody tr"):
                    link_elem = tr.select_one("td.umass-course-title a[href*='.php'], a.image-link[href*='.php']")
                    if link_elem:
                        href = link_elem.get("href")
                        full_url = urljoin(paged_url, href)
                        
                        if full_url not in detail_links:
                            detail_links.append(full_url)
                        
                        # Grab status (4th column: Title | Section | Class# | Status)
                        tds = tr.find_all("td")
                        if len(tds) >= 4:
                            status_map[full_url] = clean_text(tds[3].get_text())
                        else:
                            status_map[full_url] = "Open"

                await asyncio.sleep(random.uniform(0.5, 1.0))

            await catalog_page.close()
            print(f"Discovered {len(detail_links)} total course URLs to scrape.\n")

            # PHASE 2: Scrape details using a single worker with cooldown pauses
            print("--- PHASE 2: Hydrating Course Details ---")
            worker_page = await context.new_page()

            # Abort heavy assets on worker page
            await worker_page.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type in ["image", "media", "font"]
                else route.continue_()
            )

            count = 0
            for url in detail_links:
                if url in scraped_urls:
                    continue

                data = await fetch_course(worker_page, url)
                if data:
                    data["url"] = url
                    # Hydrate the status mapped from Phase 1
                    data["status"] = status_map.get(url, "Open") 
                    
                    results.append(data)
                    scraped_urls.add(url)
                    count += 1

                    # Save progress every 5 courses
                    if count % 5 == 0:
                        with open(output_file, "w", encoding="utf-8") as f:
                            json.dump(results, f, indent=2, ensure_ascii=False)
                        print(f"[{len(results)}/{len(detail_links)}] Saved {data['course_name']} {data['course_number']}")

                    # Cooldown pause every 40 courses to respect CloudFront's 5-minute rolling window
                    if count % 40 == 0:
                        print("\n[Cooldown] Pausing 45 seconds to reset CloudFront request window...\n")
                        await asyncio.sleep(45)

            await worker_page.close()
            await browser.close()

    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\nManual stop detected (Ctrl + C). Saving current progress...")
    finally:
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"\nSaved {len(results)} total records to {output_file}.")

if __name__ == "__main__":
    asyncio.run(run_scraper())