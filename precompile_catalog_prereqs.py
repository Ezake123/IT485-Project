import os
import json
import glob
from dotenv import load_dotenv
from supabase import create_client, Client
from prerequisite_parser import prerequisite_compiler

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if (SUPABASE_URL and SUPABASE_KEY) else None


def precompile_catalog_file(filepath: str, update_supabase: bool = False):
    print(f"\n[*] Processing catalog file: {filepath}")
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    courses = data if isinstance(data, list) else data.get("courses", [])
    total_courses = len(courses)
    print(f"[+] Loaded {total_courses} courses. Compiling prerequisite ASTs...")

    ast_cache = {}
    updated_count = 0

    for idx, c in enumerate(courses, 1):
        raw_prereq = c.get("prerequisites") or {}
        raw_text = raw_prereq.get("raw") if isinstance(raw_prereq, dict) else str(raw_prereq or "None")

        if raw_text not in ast_cache:
            ast_cache[raw_text] = prerequisite_compiler.parse(raw_text)

        parsed_ast_data = ast_cache[raw_text]

        # Combine existing schema fields (courses CNF, min_credits) with pre-compiled AST
        min_credits = raw_prereq.get("min_credits") if isinstance(raw_prereq, dict) else None
        courses_cnf = raw_prereq.get("courses") if isinstance(raw_prereq, dict) else []

        enriched_prerequisites = {
            "raw": raw_text,
            "min_credits": min_credits,
            "courses": courses_cnf,
            "permission_allowed": parsed_ast_data.get("permission_allowed", False),
            "prerequisites_formula": parsed_ast_data.get("prerequisites_formula"),
            "corequisites_formula": parsed_ast_data.get("corequisites_formula"),
            "prereq_or_coreq_formula": parsed_ast_data.get("prereq_or_coreq_formula"),
            "anti_requisites_formula": parsed_ast_data.get("anti_requisites_formula"),
            "prerequisites_ast": parsed_ast_data.get("prerequisites_ast"),
            "corequisites_ast": parsed_ast_data.get("corequisites_ast"),
            "prereq_or_coreq_ast": parsed_ast_data.get("prereq_or_coreq_ast"),
            "anti_requisites_ast": parsed_ast_data.get("anti_requisites_ast"),
        }

        c["prerequisites"] = enriched_prerequisites

        if update_supabase and supabase:
            c_dept = str(c.get("course_name") or c.get("subject_code", "")).strip().upper()
            c_num = str(c.get("course_number", "")).strip().upper()
            if c_dept and c_num:
                try:
                    supabase.table("courses").update({
                        "prerequisites": enriched_prerequisites
                    }).eq("course_name", c_dept).eq("course_number", c_num).execute()
                    updated_count += 1
                except Exception as err:
                    print(f"    [!] Failed to update {c_dept} {c_num} in Supabase: {err}")

        if idx % 200 == 0 or idx == total_courses:
            print(f"    Progress: {idx}/{total_courses} ({len(ast_cache)} unique rule templates compiled)")

    # Save precompiled JSON back to disk
    out_path = filepath.replace(".json", "_precompiled.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(courses, f, indent=2)
    print(f"[✓] Saved precompiled file to: {out_path}")
    if update_supabase:
        print(f"[✓] Updated {updated_count} rows in Supabase 'courses' table.")


if __name__ == "__main__":
    # Scan current folder for any course catalog JSON files
    json_files = glob.glob("*courses*.json")
    json_files = [f for f in json_files if not f.endswith("_precompiled.json")]

    if not json_files:
        print("No catalog JSON files found matching '*courses*.json' in the current directory.")
    else:
        for fpath in json_files:
            precompile_catalog_file(fpath, update_supabase=bool(supabase))