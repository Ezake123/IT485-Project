import asyncio
import json
import re
import unicodedata
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

#-------------------------
# Descriptions:
#
# Scans the UMB course listings using their website: https://online.umb.edu/courses/
# Saves the courses and their data as JSON file for now, and will implement to connect with SupaBase later
# Created with Gemini Flash 3.8 using agentic engineering
#-------------------------

#-------------------------
# Requirements:
#
# Require to run these following commands for this script to work:
#   pip install playwright beautifulsoup4
#   playwright install chromium
#
# Type "python UMB_course_scraper.py" to run and press "Ctrl + C" to end it early
#-------------------------

BASE_URL = "https://online.umb.edu/courses/"
OUTPUT_FILE = "umb_courses.json"
MAX_SCRAPE_TIME_SECONDS = None  # Set to a number of seconds (e.g., 1800) if you want an auto-timeout
CONCURRENCY_LIMIT = 6          # Number of course detail tabs processing simultaneously

def clean_text(text: str) -> str:
    """Normalizes Unicode characters, replaces dashes and whitespace entities."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = re.sub(r"[\u2013\u2014\u2212\u2010\u2011\u2012]", "-", text)
    text = re.sub(r"[\s\xa0]+", " ", text)
    return text.strip()

def format_clock_time(raw: str, default_period: str = "") -> str:
    """Standardizes time inputs like '5p.m.', '11AM', or '11' into 'HH:MM AM/PM'."""
    if not raw or raw.upper() in ["TBA", "ONLINE", "TBD"]:
        return "TBA"

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
        return raw

    hours = int(time_match.group(1))
    minutes = time_match.group(2) if time_match.group(2) else "00"

    if not period:
        period = "PM" if hours < 8 else "AM"

    return f"{hours}:{minutes} {period}"

def parse_schedule(raw_time_str: str):
    """
    Separates days of the week, start_time, and end_time.
    Example: 'We 5p.m. - 9p.m.' -> ('We', '5:00 PM', '9:00 PM')
             'MoWeFr 11AM - 11:50AM' -> ('MoWeFr', '11:00 AM', '11:50 AM')
    """
    raw_time_str = clean_text(raw_time_str)
    if not raw_time_str or raw_time_str.upper() in ["TBA", "ONLINE", "TBD"]:
        return "TBA", "TBA", "TBA"

    # Match consecutive day tokens: Mo, Tu, We, Th, Fr, Sa, Su
    day_match = re.match(r"^((?:Mo|Tu|We|Th|Fr|Sa|Su)+)\s*(.*)$", raw_time_str, re.IGNORECASE)
    days = "TBA"
    time_part = raw_time_str

    if day_match:
        days = day_match.group(1).strip()
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
        end_time = "TBA"
    else:
        start_time, end_time = "TBA", "TBA"

    return days, start_time, end_time

def parse_course_detail(html_content: str, detail_url: str) -> dict:
    soup = BeautifulSoup(html_content, "html.parser")

    # 1. Breadcrumbs: Course Code, Class Code, and Section Number
    last_li = soup.select_one(".course-breadcrumbs ul li:last-child")
    breadcrumb_text = clean_text(last_li.get_text()) if last_li else ""
    if not breadcrumb_text:
        bc_elem = soup.select_one(".breadcrumbs, nav, .l-page-sidebar")
        breadcrumb_text = clean_text(bc_elem.get_text()) if bc_elem else ""

    # Slice before 'Class #' to preserve hyphens (e.g. INTR-D) and spaces
    if "Class #" in breadcrumb_text:
        course_name = breadcrumb_text.split("Class #")[0].strip()
    elif "Class" in breadcrumb_text:
        course_name = breadcrumb_text.split("Class")[0].strip()
    else:
        match = re.search(r"((?:[A-Z0-9\-]+\s+)+\d{1,4}[A-Z]?)", breadcrumb_text)
        course_name = match.group(1).strip() if match else ""

    class_match = re.search(r"Class\s*#?\s*(\d+)", breadcrumb_text, re.IGNORECASE)
    section_match = re.search(r"Section\s*([0-9A-Za-z]+)", breadcrumb_text, re.IGNORECASE)

    class_code = class_match.group(1).strip() if class_match else ""
    section = section_match.group(1).strip() if section_match else ""

    # 2. Overview table: #courseOverview
    date_val, start_time, end_time, location, credits = "TBA", "TBA", "TBA", "", ""
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
                parsed_day, parsed_start, parsed_end = parse_schedule(line)
                if parsed_day != "TBA" or parsed_start != "TBA":
                    date_val = parsed_day
                    start_time = parsed_start
                    end_time = parsed_end
                    break
                elif line.upper() == "TBA":
                    date_val, start_time, end_time = "TBA", "TBA", "TBA"

            location = clean_text(cells[1].get_text())
            credits = clean_text(cells[2].get_text())

    # 3. Description & Prerequisites via explicit IDs
    desc_elem = soup.select_one("#courseDescription")
    description = clean_text(desc_elem.get_text()) if desc_elem else ""

    prereq_elem = soup.select_one("#coursePrereq")
    prerequisites = clean_text(prereq_elem.get_text()) if prereq_elem else "None"

    # 4. Course Details: Enrolled / Capacity & Instructors
    capacity = ""
    instructors = []
    details_grid = soup.select_one("#courseDetails")
    if details_grid:
        for child_div in details_grid.find_all("div", recursive=False):
            div_text = clean_text(child_div.get_text())
            if "Enrolled / Capacity" in div_text or "Capacity" in div_text:
                cap_match = re.search(r"(\d+\s*/\s*\d+|\d+)", div_text)
                if cap_match:
                    capacity = cap_match.group(1).replace(" ", "")
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

    return {
        "course_name": course_name,
        "class_code": class_code,
        "section": section,
        "date": date_val,
        "start_time": start_time,
        "end_time": end_time,
        "location": location,
        "credits": credits,
        "description": description,
        "prerequisites": prerequisites,
        "capacity": capacity,
        "instructors": instructors,
        "url": detail_url
    }

async def fetch_course(context, url: str, semaphore: asyncio.Semaphore):
    """Worker task: navigates to a detail page with blocked media and extracts records."""
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
            return parse_course_detail(content, url)
        except Exception as e:
            print(f"  Skipping {url} (Error: {e})")
            return None
        finally:
            await page.close()

def save_and_display(results: list):
    """Saves accumulated records to disk and prints formatted status."""
    if not results:
        print("\nNo course records were scraped.")
        return

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 40)
    print(f"SAVED {len(results)} RECORDS TO {OUTPUT_FILE}")
    print("=" * 40)

async def run_scraper():
    results = []
    seen = set()
    browser = None

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            catalog_page = await context.new_page()

            print(f"Loading {BASE_URL}...")
            await catalog_page.goto(BASE_URL, wait_until="networkidle")

            # 1. Detect and select latest semester tab
            semester_tabs = catalog_page.locator('a[href*="/courses/"]:has-text("202"), button:has-text("202")')
            tab_count = await semester_tabs.count()
            if tab_count > 0:
                latest_tab = semester_tabs.nth(tab_count - 1)
                semester_label = (await latest_tab.inner_text()).strip()
                print(f"Selected semester: {semester_label}")
                await latest_tab.click()
                await catalog_page.wait_for_timeout(2000)

            base_semester_url = catalog_page.url.split("?")[0].rstrip("/") + "/"
            semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

            # 2. Iterate catalog pages (?page=1&, ?page=2&, ...)
            page_num = 1
            while True:
                paged_url = f"{base_semester_url}?page={page_num}&"
                print(f"\n--- [Page {page_num}] Fetching: {paged_url} ---")
                await catalog_page.goto(paged_url, wait_until="networkidle")

                # Verify end condition: '#search-results' showing 'No results found'
                no_results = catalog_page.locator('#search-results:has-text("No results found"), .results_main:has-text("No results found")')
                if await no_results.count() > 0:
                    print(f"Reached end of catalog (No results found on page {page_num}).")
                    break

                # Expand accordions on current page
                multi_buttons = catalog_page.locator('button:has-text("Multiple Sections"), a:has-text("Multiple Sections"), tr:has-text("Multiple Sections")')
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
                        full_url = urljoin(BASE_URL, href)
                        if full_url not in seen:
                            seen.add(full_url)
                            current_page_links.append(full_url)

                if not current_page_links:
                    print(f"No detail links found on page {page_num}. Ending pagination.")
                    break

                print(f"Found {len(current_page_links)} courses on Page {page_num}. Scraping concurrently ({CONCURRENCY_LIMIT} workers)...")

                # Dispatch parallel scraping tasks for current page links
                tasks = [fetch_course(context, url, semaphore) for url in current_page_links]
                page_results = await asyncio.gather(*tasks)

                valid_entries = [r for r in page_results if r is not None]
                results.extend(valid_entries)

                # Incremental auto-save after every catalog page
                with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                    json.dump(results, f, indent=2, ensure_ascii=False)
                print(f"Page {page_num} completed. Total courses saved so far: {len(results)}")

                page_num += 1

            await catalog_page.close()

    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n\n[!] Manual interruption detected (Ctrl + C). Stopping scraper gracefully...")
    except Exception as e:
        print(f"\n[!] Unexpected error encountered: {e}")
    finally:
        save_and_display(results)
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