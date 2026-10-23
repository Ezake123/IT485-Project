import os
import sys
import json
import re
from dotenv import load_dotenv
from supabase import create_client, Client

#--------------------------------------------------------------
# Description:
# This script uploads the scraped courses stored in the
# JSON file into Supabase.
#
# Requirements:
#   pip install supabase dotenv
#
# Usage:
#   python database_update.py YOUR_FILE.json
#   python database_update.py
#--------------------------------------------------------------

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
DEFAULT_JSON_FILE = "2026_Fall_courses.json"

if not SUPABASE_URL or not SUPABASE_KEY:
    print("Error: Missing SUPABASE_URL or SUPABASE_KEY in environment variables.")
    sys.exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

#--------------------------------------------------------------
# Splits course identifier if number is empty (e.g. 'MATH 114QR')
#--------------------------------------------------------------
def sanitize_course_identifiers(dept: str, num: str) -> tuple[str, str]:
    dept = (dept or "").strip().upper()
    num = (num or "").strip().upper()
    if not num and dept:
        match = re.search(r"^([A-Za-z0-9\- ]+?)\s*(\d{1,4}[A-Za-z]*)$", dept)
        if match:
            return match.group(1).strip().upper(), match.group(2).strip().upper()
    return dept, num

#--------------------------------------------------------------
# Extracts the year and term from the filename
#--------------------------------------------------------------
def derive_term(filename: str) -> str:
    match = re.search(r"(\d{4})_([A-Za-z]+)", filename)
    if match:
        year, season = match.group(1), match.group(2)
        return f"{season} {year}"
    return "Unknown Term"

#--------------------------------------------------------------
# Uploads the data onto Supabase
#--------------------------------------------------------------
def upload_catalog_and_sections(file_path: str):
    if not os.path.exists(file_path):
        print(f"Error: Target file '{file_path}' does not exist.")
        return

    term = derive_term(file_path)
    print(f"Loading '{file_path}' for term: '{term}'...")

    with open(file_path, "r", encoding="utf-8") as f:
        records = json.load(f)

    if not records or not isinstance(records, list):
        print("No valid records found in JSON.")
        return

    # -------------------------------------------------------------
    # 1. Deduplicate & Upsert Courses into `courses`
    # -------------------------------------------------------------
    course_map = {}
    for r in records:
        if not isinstance(r, dict):
            continue

        c_name, c_num = sanitize_course_identifiers(
            r.get("course_name", ""),
            r.get("course_number", "")
        )

        if not c_name:
            continue

        key = (c_name, c_num)
        course_title = (r.get("course_title") or "").strip()

        # Update or set course entry (prefer non-empty titles)
        if key not in course_map or (not course_map[key]["course_title"] and course_title):
            course_map[key] = {
                "course_name": c_name,
                "course_number": c_num,
                "course_title": course_title,
                "description": r.get("description"),
                "gen_ed": r.get("gen_ed"),
                "prerequisites": r.get("prerequisites") or {}
            }

    unique_courses = list(course_map.values())
    print(f"Upserting {len(unique_courses)} unique courses into 'courses'...")

    batch_size = 500
    for i in range(0, len(unique_courses), batch_size):
        chunk = unique_courses[i : i + batch_size]
        supabase.table("courses").upsert(
            chunk, 
            on_conflict="course_name,course_number"
        ).execute()

    # -------------------------------------------------------------
    # 2. Prepare & Upsert Sections for the Term into `course_sections`
    # -------------------------------------------------------------
    sections_payload = []
    for r in records:
        if not isinstance(r, dict) or not r.get("class_code"):
            continue

        c_name, c_num = sanitize_course_identifiers(
            r.get("course_name", ""),
            r.get("course_number", "")
        )

        # Fallback to course title found in catalog map if section title is missing
        c_title = (r.get("course_title") or "").strip()
        if not c_title and (c_name, c_num) in course_map:
            c_title = course_map[(c_name, c_num)]["course_title"]

        sections_payload.append({
            "class_code": str(r["class_code"]).strip(),
            "course_name": c_name,
            "course_number": c_num,
            "section": str(r.get("section", "")).strip(),
            "term": term,
            "credits": r.get("credits") if r.get("credits") is not None else 3,
            "days": r.get("days") or [],
            "start_time": r.get("start_time"),
            "end_time": r.get("end_time"),
            "location": r.get("location"),
            "status": str(r.get("status") or "Open").strip(),
            "instructors": r.get("instructors") if r.get("instructors") else ["TBA"]
        })

    print(f"Upserting {len(sections_payload)} sections into 'course_sections'...")

    for i in range(0, len(sections_payload), batch_size):
        chunk = sections_payload[i : i + batch_size]
        supabase.table("course_sections").upsert(
            chunk, 
            on_conflict="class_code,term"
        ).execute()

    print(f"Successfully synced catalog and sections for {term}!")

if __name__ == "__main__":
    target_file = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_JSON_FILE
    upload_catalog_and_sections(target_file)