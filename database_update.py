import os
import sys
import json
import re
from dotenv import load_dotenv
from supabase import create_client, Client

#--------------------------------------------------------------
# Description:
# This script will uploads the scraped courses stored on the
# JSON file into Supabase.
#
# Requirement:
# Need to run the following script and have the JSON file ready.
#   pip install supabase
#
# How to use it:
# Type in the following command and add the name of the JSON file
# to specify the file to upload or else it will default to
# "2026_Fall_courses.json" since this is the latest as this
# script is made.
#   python database_update.py YOUR_FILE.json
#   python database_update.py
#--------------------------------------------------------------

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
DEFAULT_JSON_FILE = "2026_Fall_courses.json"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

#--------------------------------------------------------------
# Extracts the year and term from the filename to allow the
# database to store courses from other semesters 
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
    term = derive_term(file_path)
    print(f"Loading '{file_path}' for term: '{term}'...")

    with open(file_path, "r", encoding="utf-8") as f:
        records = json.load(f)

    if not records:
        print("No records found in JSON.")
        return

    # -------------------------------------------------------------
    # 1. Deduplicate & Upsert Courses into `courses`
    # -------------------------------------------------------------
    course_map = {}
    for r in records:
        key = (r["course_name"], r["course_number"])
        if key not in course_map:
            course_map[key] = {
                "course_name": r["course_name"],
                "course_number": r["course_number"],
                "credits": r.get("credits") or 0,
                "description": r.get("description"),
                "gen_ed": r.get("gen_ed"),
                "prerequisites": r.get("prerequisites") or {}
            }

    unique_courses = list(course_map.values())
    print(f"Upserting {len(unique_courses)} unique courses...")

    batch_size = 500
    for i in range(0, len(unique_courses), batch_size):
        chunk = unique_courses[i : i + batch_size]
        supabase.table("courses").upsert(
            chunk, 
            on_conflict="course_name,course_number"
        ).execute()

    # -------------------------------------------------------------
    # 2. Prepare & Upsert Sections for the Term
    # -------------------------------------------------------------
    sections_payload = []
    for r in records:
        sections_payload.append({
            "class_code": str(r["class_code"]),
            "course_name": r["course_name"],
            "course_number": r["course_number"],
            "section": str(r.get("section", "")),
            "term": term,
            "days": r.get("days") or [],
            "start_time": r.get("start_time"),
            "end_time": r.get("end_time"),
            "location": r.get("location"),
            "maximum_capacity": r.get("maximum_capacity") or 0,
            "enrolled": r.get("enrolled") or 0,
            "instructors": r.get("instructors") if r.get("instructors") else ["TBA"]
        })

    print(f"Upserting {len(sections_payload)} sections...")

    for i in range(0, len(sections_payload), batch_size):
        chunk = sections_payload[i : i + batch_size]
        supabase.table("course_sections").upsert(
            chunk, 
            on_conflict="class_code"
        ).execute()

    print(f"Successfully synced catalog and sections for {term}!")

if __name__ == "__main__":
    # If a file path is passed via CLI, use it; otherwise fallback to default
    target_file = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_JSON_FILE
    upload_catalog_and_sections(target_file)