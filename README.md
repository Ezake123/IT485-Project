# IT485 - Group H.W.H.J Project Classpick
UMASS Boston, Fall 2026
By: Jiahong Huang, Hunter Ward, Hasan Algafri, William Ramos

This project is about creating an optimized automated schedule planning tool for UMB students to save time from creating their schedule by scanning their degree audit. Not only will it help students, it can also assist academic counselor to provide better and faster guidance. 

## Features:
- Audit scanning: Extracts the completed courses and required courses from the audit.
- Course Scraper: Gathers accurate course information from the UMB website and saves as JSON file, also works for other semesters.
- Course Database: A normalized database to also includes courses from other semesters.
- Scheduler: A greedy approach to quickly search and create a time conflict-free schedule and outputs the most optimized schedule with courses the students are required to take at some point, and gives back the most courses it can fit into the schedule. Students can add restrictions to make schedule tailor towards their preference.
- Website: The overall product that provide user friendly interfaces with easy to navigate pages and clear instructions to provide the best experience for the users.
- Schedule Visualization: A visual demonstration of the schedule being built and display on the website.

## Tech Stack
- Back-end: Python, Flask, Playwright, BeautifulSoup, pdfplumber
- Front-end: HTML, CSS, JavaScript
- Database: Supabase - PostgreSQL

## Prerequisites
- Python 3.10+
  - pip install dotenv
  - pip install pdfplumber
  - pip install playwright beautifulsoup4
  - playwright install chromium
  - pip install supabase
- Supabase
  - Setup .env and put the URL and KEY in there
- Render

## Cloning our repo
git clone git@github.com:Ezake123/IT485-Project.git

