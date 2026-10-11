import asyncio
import json
import os
import random
import re
import unicodedata
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from supabase import create_client, Client

#-----------------------------------------------------------------
# Descriptions:
# - Scans the UMB course listings using their website: https://online.umb.edu/courses/
# - Saves the class code with their current status
# - Uploads the status onto Supabase
#
# Require to run these following commands for this script to work:
#
#   pip install playwright beautifulsoup4
#   playwright install chromium
# 
# How to use it:
# Type "python course_status_update.py" to run and enter semester and year
# and press "Ctrl + C" to end it early
#
# Developed with assistance from Google Gemini using agentic workflows
#-----------------------------------------------------------------

load_dotenv()

BASE_URL = "https://courses.umb.edu/search/"
MAX_PAGES = 55

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if (SUPABASE_URL and SUPABASE_KEY) else None


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = re.sub(r"[\s\xa0]+", " ", text)
    return text.strip()


async def scrape_status_map(semester: str, year: str) -> dict[str, str]:
    """Scrapes all 55 search pages and returns a dictionary: {class_code: status}."""
    search_base_url = f"{BASE_URL}{semester}-{year}"
    status_records = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"}
        )
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        page = await context.new_page()

        print(f"\n--- Scanning {semester.capitalize()} {year} Catalog Pages (1 to {MAX_PAGES}) ---")
        for page_num in range(1, MAX_PAGES + 1):
            paged_url = f"{search_base_url}/?page={page_num}&"
            print(f"Reading page {page_num}/{MAX_PAGES}...", end="\r")

            resp = await page.goto(paged_url, wait_until="networkidle", timeout=30000)
            if resp and resp.status == 403:
                print(f"\n[!] CloudFront block encountered on page {page_num}.")
                break

            body_text = await page.inner_text("body")
            if "No results found. Please try searching again with a different keyword." in body_text:
                print(f"\nCatalog end reached at page {page_num}.")
                break

            soup = BeautifulSoup(await page.content(), "html.parser")

            # Table Row Format:
            # tds[0]: Course Title
            # tds[1]: Section
            # tds[2]: Class Number (class_code)
            # tds[3]: Status (Open / Closed / Waitlist)
            for tr in soup.select("tbody tr"):
                tds = tr.find_all("td")
                if len(tds) >= 4:
                    class_code = clean_text(tds[2].get_text())
                    status = clean_text(tds[3].get_text())
                    
                    if class_code.isdigit():
                        status_records[class_code] = status

            await asyncio.sleep(random.uniform(0.3, 0.6))

        await browser.close()

    print(f"\nScraped {len(status_records)} total class statuses.")
    return status_records


def sync_statuses_to_supabase(status_records: dict[str, str], term: str):
    """Pulls current DB state, diffs changes, and updates changed rows in batches."""
    if not supabase:
        print("[!] Supabase credentials missing from .env. Skipping DB upload.")
        return

    print(f"\nFetching existing sections from Supabase for term '{term}'...")
    
    # 1. Fetch current statuses from Supabase
    db_sections = []
    page = 0
    page_size = 1000
    while True:
        res = (
            supabase.table("course_sections")
            .select("class_code, status")
            .ilike("term", f"%{term}%")
            .range(page * page_size, (page + 1) * page_size - 1)
            .execute()
        )
        db_sections.extend(res.data)
        if len(res.data) < page_size:
            break
        page += 1

    current_db_map = {row["class_code"]: (row.get("status") or "Open") for row in db_sections}
    print(f"Found {len(current_db_map)} active sections in Supabase.")

    # 2. Identify only rows whose status changed
    updates_to_run = []
    for class_code, new_status in status_records.items():
        if class_code in current_db_map:
            old_status = current_db_map[class_code]
            if old_status.lower() != new_status.lower():
                updates_to_run.append((class_code, new_status, old_status))

    if not updates_to_run:
        print("[✓] All sections are already up to date! Zero writes needed.")
        return

    print(f"Detected {len(updates_to_run)} sections with changed status. Syncing...")

    # 3. Apply updates to Supabase
    for class_code, new_status, old_status in updates_to_run:
        supabase.table("course_sections").update({"status": new_status}).eq("class_code", class_code).execute()
        print(f"  • Class {class_code}: {old_status} -> {new_status}")

    print(f"\n[✓] Successfully updated {len(updates_to_run)} section statuses in Supabase.")


def main():
    semester = input("Enter semester (e.g. spring, fall): ").strip().lower()
    year = input("Enter year (e.g. 2027): ").strip()

    term = f"{semester.capitalize()} {year}"
    json_snapshot_file = f"{year}_{semester.capitalize()}_status.json"

    # 1. Scrape live status map across the 55 search pages
    status_map = asyncio.run(scrape_status_map(semester, year))

    if not status_map:
        print("No section data collected. Exiting.")
        return

    # 2. Persist local JSON snapshot
    with open(json_snapshot_file, "w", encoding="utf-8") as f:
        json.dump(status_map, f, indent=2)
    print(f"Saved snapshot to {json_snapshot_file}.")

    # 3. Update Supabase
    sync_statuses_to_supabase(status_map, term)


if __name__ == "__main__":
    main()