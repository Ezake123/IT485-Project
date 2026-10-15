// =================================================================
// CLASSPICK - MAIN CLIENT CONTROLLER (main.js)
// -----------------------------------------------------------------
// Table of Contents:
//   0. Global State & Environment
//   1. Time Formatting & Conversion Helpers
//   2. Conflict Detection Engine
//   3. State Synchronization & Complexity Safeguards
//   4. Accordion & Group Layout Controls
//   5. Locked Section Management
//   6. Candidate Pool UI & Dynamic Rendering
//   7. Custom / Unlisted Section Modal
//   8. Catalog Live Search & Gen Ed Filters
//   9. Weekly Timetable Grid Visualizer
//  10. Course Details Modal (Prerequisites & Components)
//  11. Clipboard & Helper Utilities
//  12. DOM Ready Initialization & Event Binding
// =================================================================

// =================================================================
// 0. GLOBAL STATE & ENVIRONMENT
// =================================================================
let candidatePool = [];
let lockedSections = [];
let currentModalSections = [];
let activeModalCourse = null;
let activeModalSectionIndex = 0;
let customCourseCounter = 1;
let openReqGroups = new Set(); // Tracks manually expanded requirement groups

// Attach core state to window for template bindings & solver sync
window.candidatePool = candidatePool;
window.lockedSections = lockedSections;
window.customCourseCounter = customCourseCounter;

function getActiveTerm() {
    return window.ACTIVE_TERM || "Fall 2026";
}

function escapeHtml(str) {
    if (!str) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
}

// =================================================================
// 1. TIME FORMATTING & CONVERSION HELPERS
// =================================================================
function timeStrToMinutes(str) {
    if (!str || str === "TBA") return 0;
    const clean = str.trim().toUpperCase();

    const isPM = clean.includes("PM");
    const isAM = clean.includes("AM");
    const numericPart = clean.replace(/[AP]M/, "").trim();

    const parts = numericPart.split(":").map(Number);
    if (parts.length < 2 || isNaN(parts[0]) || isNaN(parts[1])) return 0;

    let hours = parts[0];
    const minutes = parts[1];

    if (isAM && hours === 12) hours = 0;
    if (isPM && hours < 12) hours += 12;

    return (hours * 60) + minutes;
}

function formatTimeTo12Hour(timeStr) {
    if (!timeStr || timeStr === "TBA") return "";
    const clean = timeStr.trim();
    
    if (clean.toUpperCase().includes("AM") || clean.toUpperCase().includes("PM")) {
        return clean;
    }

    const parts = clean.split(":");
    if (parts.length < 2) return clean;

    let hour = parseInt(parts[0], 10);
    const minutePart = parseInt(parts[1], 10);

    if (isNaN(hour) || isNaN(minutePart)) return clean;

    const minutes = String(minutePart).padStart(2, "0");
    const ampm = hour >= 12 ? "PM" : "AM";

    hour = hour % 12;
    if (hour === 0) hour = 12;

    return `${hour}:${minutes} ${ampm}`;
}

// =================================================================
// 2. CONFLICT DETECTION ENGINE
// =================================================================
function checkLockedSectionConflict(candidateSection, candidateTitle = "", ignoreClassCode = null) {
    if (!Array.isArray(lockedSections) || lockedSections.length === 0) return null;
    if (!candidateSection || !Array.isArray(candidateSection.days) || candidateSection.days.length === 0) return null;

    const candStart = timeStrToMinutes(candidateSection.start_time);
    const candEnd = timeStrToMinutes(candidateSection.end_time);

    if (candStart === 0 && candEnd === 0) return null; // Asynchronous / online
    if (candStart >= candEnd) return null;

    const candDays = new Set(candidateSection.days.map(d => String(d).trim().toUpperCase()));

    for (const item of lockedSections) {
        const sec = item.section;
        if (!sec || !Array.isArray(sec.days) || sec.days.length === 0) continue;

        if (ignoreClassCode && String(sec.class_code) === String(ignoreClassCode)) {
            continue;
        }

        const secStart = timeStrToMinutes(sec.start_time);
        const secEnd = timeStrToMinutes(sec.end_time);

        if (secStart === 0 && secEnd === 0) continue;

        const hasDayOverlap = sec.days.some(d => candDays.has(String(d).trim().toUpperCase()));
        if (hasDayOverlap) {
            // Check interval overlap: max(start) < min(end)
            if (Math.max(candStart, secStart) < Math.min(candEnd, secEnd)) {
                const lockedTitle = `${item.course.subject_code} ${item.course.course_number} (Sec ${sec.section})`;
                const daysList = sec.days.join(", ");
                const timeStr = `${formatTimeTo12Hour(sec.start_time)} - ${formatTimeTo12Hour(sec.end_time)}`;
                return `${lockedTitle} on ${daysList} at ${timeStr}`;
            }
        }
    }

    return null;
}

// =================================================================
// 3. STATE SYNCHRONIZATION & COMPLEXITY SAFEGUARDS
// =================================================================
function syncPoolInput() {
    const input = document.getElementById("selected_candidate_courses_json");
    if (input) {
        input.value = JSON.stringify(candidatePool);
    }
    const countBadge = document.getElementById("candidateCount");
    if (countBadge) {
        countBadge.textContent = `${candidatePool.length} Courses`;
    }
    verifyPoolComplexity();
}

function syncLockedInput() {
    const input = document.getElementById("locked_sections_json");
    if (input) {
        input.value = JSON.stringify(lockedSections);
    }
    const badge = document.getElementById("lockedCount");
    if (badge) {
        badge.textContent = `${lockedSections.length} Locked`;
    }
}

function verifyPoolComplexity() {
    const targetInput = document.getElementById("target_course_count");
    const targetK = parseInt(targetInput ? targetInput.value : 5, 10) || 5;
    
    const totalSections = candidatePool.reduce((sum, item) => sum + (parseInt(item.sections_count, 10) || 0), 0);
    const numCourses = candidatePool.length;

    const warningBanner = document.getElementById("complexityWarning");
    const countDisplay = document.getElementById("totalSectionCount");

    if (countDisplay) {
        countDisplay.textContent = totalSections;
    }

    if (!warningBanner || numCourses === 0) return;

    const avgSectionsPerCourse = totalSections / numCourses;
    const estimatedCombinations = Math.pow(avgSectionsPerCourse, Math.min(targetK, numCourses));
    warningBanner.style.display = estimatedCombinations > 120000 ? "block" : "none";
}

// =================================================================
// 4. ACCORDION & GROUP LAYOUT CONTROLS
// =================================================================
function getSectionKey(sec) {
    const code = String(sec.class_code || "");
    const term = String(sec.term || getActiveTerm()).replace(/\s+/g, "_");
    return `${code}_${term}`;
}

function getGroupNeededMap() {
    const groupNeeded = {};
    if (Array.isArray(window.SERIALIZED_REQS)) {
        window.SERIALIZED_REQS.forEach((req, idx) => {
            const gid = `req-${String(idx + 1).padStart(2, "0")}`;
            groupNeeded[gid] = parseInt(req.courses_needed, 10) || 1;
        });
    }
    return groupNeeded;
}

function countLockedDistinctForGroup(gid) {
    const distinct = new Set();
    lockedSections.forEach(ls => {
        if (ls.course && (ls.course.req_id === gid)) {
            distinct.add(`${ls.course.subject_code}${ls.course.course_number}`.toUpperCase());
        }
    });
    return distinct.size;
}

function toggleAuditAccordion(contentId, triggerElem) {
    const content = document.getElementById(contentId);
    if (!content) return;
    const isCurrentlyCollapsed = content.classList.contains("collapsed");
    content.classList.toggle("collapsed", !isCurrentlyCollapsed);
    const arrow = triggerElem.querySelector(".accordion-arrow");
    if (arrow) {
        arrow.classList.toggle("arrow-rotated", isCurrentlyCollapsed);
    }
}

function toggleReqGroup(gid) {
    const grid = document.getElementById(`group-grid-${gid}`);
    const arrow = document.getElementById(`group-arrow-${gid}`);
    if (!grid) return;

    // Read actual DOM state to prevent double-click desynchronization
    const isCurrentlyOpen = !grid.classList.contains("collapsed");
    const willBeOpen = !isCurrentlyOpen;

    grid.classList.toggle("collapsed", !willBeOpen);
    if (arrow) arrow.classList.toggle("arrow-rotated", willBeOpen);

    if (willBeOpen) {
        openReqGroups.add(gid);
    } else {
        openReqGroups.delete(gid);
    }
}

// =================================================================
// 5. LOCKED SECTION MANAGEMENT
// =================================================================
function renderLockedCard(course, section) {
    const emptyLocked = document.getElementById("emptyLockedMsg");
    if (emptyLocked) emptyLocked.remove();

    const lockedList = document.getElementById("lockedList");
    if (!lockedList) return;

    const secKey = getSectionKey(section);
    const cardId = `locked-card-${secKey}`;
    let card = document.getElementById(cardId);
    const isNew = !card;

    if (isNew) {
        card = document.createElement("div");
        card.className = "candidate-item-card locked-card";
        card.id = cardId;
    }

    const daysStr = (section.days && section.days.length) ? section.days.join(", ") : "Online";
    const timeStr = (section.start_time && section.start_time !== "TBA" && section.end_time && section.end_time !== "TBA") 
        ? `${formatTimeTo12Hour(section.start_time)} - ${formatTimeTo12Hour(section.end_time)}` 
        : "Asynchronous";
    const isCustom = String(section.class_code).startsWith("CUST-") || Boolean(course.is_custom);
    const displayTitle = course.course_title || course.name || `${course.subject_code} ${course.course_number}`;
    const safeTitle = escapeHtml(displayTitle);

    card.innerHTML = `
        <div class="card-header-flex">
            <div class="candidate-code">${course.subject_code} ${course.course_number} <span class="locked-badge-pill">Sec ${section.section}</span></div>
            <div class="candidate-actions">
                ${isCustom 
                    ? `<button type="button" class="btn-details" onclick="openCustomModal('${section.class_code}')">Edit</button>` 
                    : `<button type="button" class="btn-details" onclick="openCourseModal('${course.subject_code}', '${course.course_number}', '${section.section}', '${safeTitle}')">Details</button>`}
                <button type="button" class="btn-remove-pool" onclick="unlockSection('${section.class_code}', '${section.term || getActiveTerm()}')" title="Unlock and return to course pool">✕</button>
            </div>
        </div>
        <div class="candidate-name" title="${safeTitle}">${safeTitle}</div>
        <div class="candidate-meta meta-top-spacing">
            <span>${daysStr} (${timeStr})</span>
            <span>#${section.class_code}</span>
        </div>
    `;

    if (isNew) {
        lockedList.appendChild(card);
    }
}

function renderAllLockedSections() {
    const lockedList = document.getElementById("lockedList");
    if (!lockedList) return;

    if (!Array.isArray(lockedSections) || lockedSections.length === 0) {
        lockedList.innerHTML = `
            <div class="empty-placeholder empty-grid-span" id="emptyLockedMsg">
                No locked sections. Select a section dropdown in your pool or use "Lock This Section" in Details.
            </div>
        `;
        syncLockedInput();
        return;
    }

    lockedList.innerHTML = "";
    lockedSections.forEach(item => {
        if (item.course && item.section) {
            renderLockedCard(item.course, item.section);
        }
    });
    syncLockedInput();
}

function lockSection(dept, num, sectionData) {
    const isDiscussion = String(sectionData.section).trim().toUpperCase().endsWith("D");

    const alreadyLockedThisCourse = lockedSections.filter(ls => 
        String(ls.course.subject_code).trim().toUpperCase() === String(dept).trim().toUpperCase() && 
        String(ls.course.course_number).trim().toUpperCase() === String(num).trim().toUpperCase()
    );

    const hasLockedLec = alreadyLockedThisCourse.some(ls => !String(ls.section.section).trim().toUpperCase().endsWith("D"));
    const hasLockedDisc = alreadyLockedThisCourse.some(ls => String(ls.section.section).trim().toUpperCase().endsWith("D"));

    if (!isDiscussion && hasLockedLec) {
        const oldLec = alreadyLockedThisCourse.find(ls => !String(ls.section.section).trim().toUpperCase().endsWith("D"));
        unlockSection(oldLec.section.class_code);
    }
    if (isDiscussion && hasLockedDisc) {
        const oldDisc = alreadyLockedThisCourse.find(ls => String(ls.section.section).trim().toUpperCase().endsWith("D"));
        unlockSection(oldDisc.section.class_code);
    }

    let courseData = candidatePool.find(c => 
        String(c.subject_code).trim().toUpperCase() === String(dept).trim().toUpperCase() && 
        String(c.course_number).trim().toUpperCase() === String(num).trim().toUpperCase()
    );

    if (!courseData && Array.isArray(window.MASTER_CATALOG_CANDIDATES)) {
        courseData = window.MASTER_CATALOG_CANDIDATES.find(c => 
            String(c.subject_code).trim().toUpperCase() === String(dept).trim().toUpperCase() && 
            String(c.course_number).trim().toUpperCase() === String(num).trim().toUpperCase()
        );
    }

    if (!courseData && activeModalCourse) {
        courseData = {
            subject_code: activeModalCourse.subject_code,
            course_number: activeModalCourse.course_number,
            name: activeModalCourse.course_title || `${activeModalCourse.subject_code} ${activeModalCourse.course_number}`,
            course_title: activeModalCourse.course_title || `${activeModalCourse.subject_code} ${activeModalCourse.course_number}`,
            credits: activeModalCourse.credits || 3,
            sections_count: activeModalCourse.sections_count || 1,
            req_id: "user-added",
            req_group_name: "Added Courses",
            req_needed: 1
        };
    }

    if (!courseData) return;

    lockedSections.push({
        course: courseData,
        section: sectionData
    });

    renderAllLockedSections();

    // Determine if course has lecture+discussion pairing
    const selectElem = document.getElementById(`section-select-${dept}-${num}`);
    let allSections = [];
    if (selectElem && selectElem.dataset.sectionsJson) {
        try { allSections = JSON.parse(selectElem.dataset.sectionsJson); } catch (e) {}
    }
    const hasDiscussions = allSections.some(s => String(s.section).trim().toUpperCase().endsWith("D"));
    const hasLectures = allSections.some(s => !String(s.section).trim().toUpperCase().endsWith("D"));
    const requiresBoth = hasDiscussions && hasLectures;

    const totalLockedForCourse = lockedSections.filter(ls => 
        String(ls.course.subject_code).trim().toUpperCase() === String(dept).trim().toUpperCase() && 
        String(ls.course.course_number).trim().toUpperCase() === String(num).trim().toUpperCase()
    ).length;

    if (!requiresBoth || totalLockedForCourse >= 2) {
        candidatePool = candidatePool.filter(c => !(
            String(c.subject_code).trim().toUpperCase() === String(dept).trim().toUpperCase() && 
            String(c.course_number).trim().toUpperCase() === String(num).trim().toUpperCase()
        ));
    }

    renderCandidatePoolUI();
}

function unlockSection(classCode, term = null) {
    const targetCode = String(classCode);
    const targetTerm = term || getActiveTerm();

    const lockedItem = lockedSections.find(ls => 
        String(ls.section.class_code) === targetCode && 
        (!term || String(ls.section.term || getActiveTerm()) === String(targetTerm))
    );
    if (!lockedItem) return;

    const secKey = getSectionKey(lockedItem.section);
    const isCustom = targetCode.startsWith("CUST-") || lockedItem.is_custom || (lockedItem.course && lockedItem.course.is_custom);
    const unlockedCourse = lockedItem.course;

    lockedSections = lockedSections.filter(ls => getSectionKey(ls.section) !== secKey);
    renderAllLockedSections();

    if (isCustom) {
        if (customCourseCounter > 1) {
            customCourseCounter--;
            window.customCourseCounter = customCourseCounter;
        }
    } else if (unlockedCourse) {
        const gid = unlockedCourse.req_id || "user-added";

        if (gid === "user-added") {
            const alreadyInPool = candidatePool.some(c => 
                String(c.subject_code).trim().toUpperCase() === String(unlockedCourse.subject_code).trim().toUpperCase() && 
                String(c.course_number).trim().toUpperCase() === String(unlockedCourse.course_number).trim().toUpperCase()
            );
            if (!alreadyInPool) {
                candidatePool.push(unlockedCourse);
            }
        } else if (Array.isArray(window.MASTER_CATALOG_CANDIDATES)) {
            window.MASTER_CATALOG_CANDIDATES.forEach(masterCourse => {
                if (masterCourse.req_id === gid) {
                    const isCurrentlyLocked = lockedSections.some(ls => 
                        ls.course && 
                        String(ls.course.subject_code).trim().toUpperCase() === String(masterCourse.subject_code).trim().toUpperCase() && 
                        String(ls.course.course_number).trim().toUpperCase() === String(masterCourse.course_number).trim().toUpperCase()
                    );
                    const alreadyInPool = candidatePool.some(c => 
                        String(c.subject_code).trim().toUpperCase() === String(masterCourse.subject_code).trim().toUpperCase() && 
                        String(c.course_number).trim().toUpperCase() === String(masterCourse.course_number).trim().toUpperCase()
                    );
                    if (!isCurrentlyLocked && !alreadyInPool) {
                        candidatePool.push(masterCourse);
                    }
                }
            });
        }
    }

    renderCandidatePoolUI();
    syncLockedInput();
    updateCatalogSearchButtons();
}

// =================================================================
// 6. CANDIDATE POOL UI & DYNAMIC RENDERING
// =================================================================
function renderCandidatePoolUI() {
    const container = document.getElementById("candidateList");
    if (!container) return;

    if (!candidatePool || candidatePool.length === 0) {
        container.innerHTML = `<div class="empty-placeholder empty-grid-span" id="emptyPoolMsg">No eligible courses in generator pool. Add courses below.</div>`;
        syncPoolInput();
        updateCatalogSearchButtons();
        return;
    }

    const groupNeededMap = getGroupNeededMap();
    const grouped = {};

    candidatePool.forEach(c => {
        const gid = c.req_id || "user-added";
        if (!grouped[gid]) {
            grouped[gid] = {
                req_id: gid,
                name: c.req_group_name || (gid === "user-added" ? "Added Courses" : "Requirement Group"),
                baseNeeded: c.req_needed || groupNeededMap[gid] || 1,
                courses: []
            };
        }
        grouped[gid].courses.push(c);
    });

    let html = "";
    let totalVisibleCourses = 0;

    Object.keys(grouped).forEach(gid => {
        const grp = grouped[gid];
        let netNeeded = grp.baseNeeded;

        if (gid === "user-added") {
            netNeeded = grp.courses.length;
        } else {
            const lockedCount = countLockedDistinctForGroup(gid);
            netNeeded = Math.max(0, grp.baseNeeded - lockedCount);
        }

        if (netNeeded <= 0) return;

        totalVisibleCourses += grp.courses.length;
        const totalGroupCourses = grp.courses.length;
        const totalGroupSections = grp.courses.reduce((sum, item) => sum + (parseInt(item.sections_count, 10) || 0), 0);
        const isOpen = openReqGroups.has(gid);

        html += `
            <div class="candidate-group-block" id="group-block-${gid}" data-req-id="${gid}" data-total-courses="${totalGroupCourses}" data-total-sections="${totalGroupSections}">
                <div class="candidate-group-header clickable" onclick="toggleReqGroup('${gid}')" role="button" tabindex="0">
                    <div class="group-header-left">
                        <span class="candidate-group-title">${escapeHtml(grp.name)}</span>
                        <span class="meta-pill pill-req">Need ${netNeeded}</span>
                    </div>
                    <div class="group-header-right">
                        <span class="meta-pill pill-neutral pill-course-count">${totalGroupCourses} Course${totalGroupCourses === 1 ? "" : "s"}</span>
                        <span class="meta-pill pill-neutral pill-section-count">${totalGroupSections} Section${totalGroupSections === 1 ? "" : "s"}</span>
                        <span class="accordion-arrow ${isOpen ? "arrow-rotated" : ""}" id="group-arrow-${gid}">▼</span>
                    </div>
                </div>
                <div class="candidate-group-grid ${isOpen ? "" : "collapsed"}" id="group-grid-${gid}">
        `;

        grp.courses.forEach(c => {
            const courseTitle = c.course_title || c.name || `${c.subject_code} ${c.course_number}`;
            const safeTitle = escapeHtml(courseTitle);
            const secCount = parseInt(c.sections_count, 10) || 1;
            
            // Incremental Prerequisite Warning Logic
            const hasMissingPrereqs = Array.isArray(c.missing_prereqs) && c.missing_prereqs.length > 0;
            const missingText = hasMissingPrereqs ? escapeHtml(c.missing_prereqs.join(", ")) : "";

            html += `
                <div class="candidate-item-card ${hasMissingPrereqs ? "has-prereq-warning" : ""}" 
                     id="candidate-card-${c.subject_code}-${c.course_number}"
                     data-code="${c.subject_code} ${c.course_number}"
                     data-dept="${c.subject_code}"
                     data-num="${c.course_number}"
                     data-sections-count="${secCount}">
                    <div class="card-header-flex">
                        <div class="candidate-code">${c.subject_code} ${c.course_number}</div>
                        <div class="candidate-actions">
                            <button type="button" class="btn-details" onclick="openCourseModal('${c.subject_code}', '${c.course_number}', null, '${safeTitle}')">Details</button>
                            <button type="button" class="btn-remove-pool" onclick="removeCourseFromPool('${c.subject_code}', '${c.course_number}')" title="Remove from generator pool">✕</button>
                        </div>
                    </div>
                    <div class="candidate-name" title="${safeTitle}">${safeTitle}</div>

                    ${hasMissingPrereqs ? `
                        <div class="prereq-warning-card" title="${escapeHtml(c.prereq_warning || ('Needs ' + missingText))}">
                            ⚠️ <strong>Missing Prereq:</strong> Needs ${missingText}
                        </div>
                    ` : ""}

                    <div class="candidate-meta">
                        <span class="meta-pill pill-blue">${c.credits || 3} cr</span>
                        ${!hasMissingPrereqs && c.prereq_status === "met" ? '<span class="meta-pill pill-prereq-met">✓ Prereqs Met</span>' : ""}
                    </div>
                    <select class="section-select-dropdown" id="section-select-${c.subject_code}-${c.course_number}" 
                            onchange="handleSectionSelectChange(this, '${c.subject_code}', '${c.course_number}')">
                        <option value="" selected disabled>Loading sections...</option>
                    </select>
                </div>
            `;
        });

        html += `
                </div>
            </div>
        `;
    });

    if (totalVisibleCourses === 0) {
        container.innerHTML = `<div class="empty-placeholder empty-grid-span" id="emptyPoolMsg">All requirement groups satisfied by locked courses.</div>`;
    } else {
        container.innerHTML = html;
        candidatePool.forEach(c => {
            loadSectionDropdown(c.subject_code, c.course_number);
        });
    }

    syncPoolInput();
    updateCatalogSearchButtons();
}

function addCourseToPool(courseData, btnElement) {
    const dept = String(courseData.subject_code).trim().toUpperCase();
    const num = String(courseData.course_number).trim().toUpperCase();

    const exists = candidatePool.some(c => 
        String(c.subject_code).trim().toUpperCase() === dept && 
        String(c.course_number).trim().toUpperCase() === num
    );
    const isLocked = lockedSections.some(ls => 
        ls.course && 
        String(ls.course.subject_code).trim().toUpperCase() === dept && 
        String(ls.course.course_number).trim().toUpperCase() === num
    );
    if (exists || isLocked) return;

    let fullCourse = null;
    if (Array.isArray(window.MASTER_CATALOG_CANDIDATES)) {
        fullCourse = window.MASTER_CATALOG_CANDIDATES.find(c => 
            String(c.subject_code).trim().toUpperCase() === dept && 
            String(c.course_number).trim().toUpperCase() === num
        );
    }

    if (fullCourse) {
        candidatePool.push(JSON.parse(JSON.stringify(fullCourse)));
    } else {
        const addedCourse = JSON.parse(JSON.stringify(courseData));
        addedCourse.req_id = "user-added";
        addedCourse.req_group_name = "Added Courses";
        addedCourse.req_needed = 1;
        candidatePool.push(addedCourse);
    }

    if (btnElement) {
        btnElement.textContent = "Added ✓";
        btnElement.classList.add("added");
        btnElement.disabled = true;
    }

    renderCandidatePoolUI();
    updateCatalogSearchButtons();
}

function removeCourseFromPool(dept, num) {
    candidatePool = candidatePool.filter(c => !(
        String(c.subject_code).trim().toUpperCase() === String(dept).trim().toUpperCase() && 
        String(c.course_number).trim().toUpperCase() === String(num).trim().toUpperCase()
    ));
    renderCandidatePoolUI();
    updateCatalogSearchButtons();
}

function clearAllCandidateCourses() {
    candidatePool = [];
    renderCandidatePoolUI();
    updateCatalogSearchButtons();
}

function loadSectionDropdown(dept, num) {
    const selectElem = document.getElementById(`section-select-${dept}-${num}`);
    if (!selectElem) return;

    const term = getActiveTerm();
    fetch(`/api/course-sections?name=${encodeURIComponent(dept)}&number=${encodeURIComponent(num)}&term=${encodeURIComponent(term)}`)
        .then(res => res.json())
        .then(data => {
            if (!data.sections || data.sections.length === 0) {
                selectElem.innerHTML = `<option value="" disabled selected>No active sections</option>`;
                selectElem.disabled = true;
                return;
            }

            selectElem.dataset.sectionsJson = JSON.stringify(data.sections);
            repopulateCandidateDropdown(dept, num);
        })
        .catch(err => {
            console.error("Failed to load sections for dropdown:", err);
            selectElem.innerHTML = `<option value="" disabled selected>Error loading sections</option>`;
        });
}

function repopulateCandidateDropdown(dept, num) {
    const selectElem = document.getElementById(`section-select-${dept}-${num}`);
    if (!selectElem || !selectElem.dataset.sectionsJson) return;

    let allSections = [];
    try {
        allSections = JSON.parse(selectElem.dataset.sectionsJson);
    } catch (e) {
        return;
    }

    const lockedForCourse = lockedSections.filter(ls => 
        String(ls.course.subject_code).trim().toUpperCase() === String(dept).trim().toUpperCase() && 
        String(ls.course.course_number).trim().toUpperCase() === String(num).trim().toUpperCase()
    );

    const hasLockedLec = lockedForCourse.some(ls => !String(ls.section.section).trim().toUpperCase().endsWith("D"));
    const hasLockedDisc = lockedForCourse.some(ls => String(ls.section.section).trim().toUpperCase().endsWith("D"));

    const hasDiscussionsInCatalog = allSections.some(s => String(s.section).trim().toUpperCase().endsWith("D"));
    const hasLecturesInCatalog = allSections.some(s => !String(s.section).trim().toUpperCase().endsWith("D"));
    const isHybridCourse = hasDiscussionsInCatalog && hasLecturesInCatalog;

    let filteredSections = allSections.map((s, idx) => ({ ...s, originalIndex: idx }));
    let placeholderText = `Select section to lock in (${allSections.length})...`;

    if (isHybridCourse) {
        if (hasLockedLec && !hasLockedDisc) {
            filteredSections = filteredSections.filter(s => String(s.section).trim().toUpperCase().endsWith("D"));
            placeholderText = `Lecture locked. Select Discussion (${filteredSections.length} available)...`;
        } else if (hasLockedDisc && !hasLockedLec) {
            filteredSections = filteredSections.filter(s => !String(s.section).trim().toUpperCase().endsWith("D"));
            placeholderText = `Discussion locked. Select Lecture (${filteredSections.length} available)...`;
        }
    }

    if (filteredSections.length === 0) {
        selectElem.innerHTML = `<option value="" disabled selected>All sections for this component locked</option>`;
        selectElem.disabled = true;
        return;
    }

    selectElem.disabled = false;
    let optionsHtml = `<option value="" selected disabled>${placeholderText}</option>`;
    filteredSections.forEach(s => {
        let timeStr = "Asynchronous / Online";
        if (s.start_time !== "TBA" && s.end_time !== "TBA") {
            timeStr = `${formatTimeTo12Hour(s.start_time)} - ${formatTimeTo12Hour(s.end_time)}`;
        }
        const daysStr = (s.days && s.days.length) ? s.days.join("") + " " : "";
        const tag = String(s.section).trim().toUpperCase().endsWith("D") ? "[Disc] " : "[Lec] ";
        const label = `${tag}Sec ${s.section}: ${daysStr}${timeStr} (#${s.class_code})`;

        optionsHtml += `<option value="${s.originalIndex}">${label}</option>`;
    });

    selectElem.innerHTML = optionsHtml;
}

function handleSectionSelectChange(selectElem, dept, num) {
    const selectedIdx = selectElem.value;
    if (selectedIdx === "" || !selectElem.dataset.sectionsJson) return;

    let sections = [];
    try {
        sections = JSON.parse(selectElem.dataset.sectionsJson);
    } catch (e) {
        return;
    }

    const sectionData = sections[parseInt(selectedIdx, 10)];
    if (!sectionData) return;

    const conflict = checkLockedSectionConflict(sectionData, `${dept} ${num}`);
    if (conflict) {
        const timeStr = (sectionData.start_time && sectionData.start_time !== "TBA") 
            ? `${formatTimeTo12Hour(sectionData.start_time)} - ${formatTimeTo12Hour(sectionData.end_time)}` 
            : "Asynchronous";
        const daysStr = (sectionData.days && sectionData.days.length) ? sectionData.days.join(", ") : "Online";

        alert(
            `⚠️ Time Conflict Detected:\n\n` +
            `${dept} ${num} (Sec ${sectionData.section}) on ${daysStr} at ${timeStr} overlaps with an already locked section:\n` +
            `• ${conflict}\n\n` +
            `Please select a different section or unlock the conflicting course first.`
        );

        selectElem.value = "";
        return;
    }

    lockSection(dept, num, sectionData);
}

function initCandidatePool() {
    try {
        const rawLockedInput = document.getElementById("locked_sections_json");
        if (rawLockedInput && rawLockedInput.value) {
            lockedSections = JSON.parse(rawLockedInput.value);
            window.lockedSections = lockedSections;
        }
    } catch (e) {
        lockedSections = [];
    }

    if (Array.isArray(window.MASTER_CATALOG_CANDIDATES) && window.MASTER_CATALOG_CANDIDATES.length > 0) {
        candidatePool = JSON.parse(JSON.stringify(window.MASTER_CATALOG_CANDIDATES));
    } else {
        candidatePool = [];
    }

    renderAllLockedSections();
    renderCandidatePoolUI();
}

// =================================================================
// 7. CUSTOM / UNLISTED SECTION MODAL
// =================================================================
let editingCustomClassCode = null;

function openCustomModal(classCodeToEdit = null) {
    const modal = document.getElementById("customCourseModal");
    if (!modal) return;

    editingCustomClassCode = (typeof classCodeToEdit === "string" && classCodeToEdit.trim().length > 0)
        ? classCodeToEdit.trim()
        : null;

    const heading = document.getElementById("customModalHeading");
    const submitBtn = document.getElementById("btnSubmitCustomSection");
    const deptInput = document.getElementById("customDeptInput");
    const numInput = document.getElementById("customNumInput");
    const startInput = document.getElementById("customStartTime");
    const endInput = document.getElementById("customEndTime");

    if (editingCustomClassCode) {
        const item = lockedSections.find(ls => String(ls.section.class_code) === editingCustomClassCode);
        if (item) {
            if (heading) heading.textContent = "✏️ Edit Custom Section";
            if (submitBtn) submitBtn.textContent = "Save Changes";
            if (deptInput) deptInput.value = item.course.subject_code || "";
            if (numInput) numInput.value = item.course.course_number || "";
            if (startInput) startInput.value = item.section.start_time || "09:00";
            if (endInput) endInput.value = item.section.end_time || "10:15";

            const itemDays = (item.section.days || []).map(d => String(d).trim().toUpperCase());
            document.querySelectorAll('input[name="custom_days"]').forEach(cb => {
                cb.checked = itemDays.includes(cb.value.toUpperCase());
            });
        }
    } else {
        if (heading) heading.textContent = "➕ Add Custom / Unlisted Section";
        if (submitBtn) submitBtn.textContent = "Lock Custom Section";
        if (deptInput) deptInput.value = "";
        if (numInput) {
            numInput.value = "";
            numInput.placeholder = String(window.customCourseCounter || 1);
        }
        if (startInput) startInput.value = "09:00";
        if (endInput) endInput.value = "10:15";
        document.querySelectorAll('input[name="custom_days"]').forEach(cb => cb.checked = false);
    }

    modal.style.display = "flex";
}

function closeCustomModal(e) {
    if (e && e.target) {
        const modal = document.getElementById("customCourseModal");
        if (e.target !== modal && !e.target.classList.contains("btn-close-modal") && e.target.id !== "btnCancelCustomCard") {
            return;
        }
    }

    const modal = document.getElementById("customCourseModal");
    if (modal) modal.style.display = "none";
    editingCustomClassCode = null;
}

function submitCustomSection() {
    const deptInput = document.getElementById("customDeptInput");
    const numInput = document.getElementById("customNumInput");
    const startInput = document.getElementById("customStartTime");
    const endInput = document.getElementById("customEndTime");

    const selectedDays = Array.from(document.querySelectorAll('input[name="custom_days"]:checked')).map(cb => cb.value);

    if (selectedDays.length === 0) {
        alert("Please select at least one meeting day for this custom course.");
        return;
    }

    const startTime = (startInput && startInput.value) ? startInput.value : "09:00";
    const endTime = (endInput && endInput.value) ? endInput.value : "10:15";

    const startMinutes = timeStrToMinutes(startTime);
    const endMinutes = timeStrToMinutes(endTime);

    if (endMinutes <= startMinutes) {
        alert(
            `⚠️ Invalid Time Range:\n\n` +
            `The end time (${formatTimeTo12Hour(endTime)}) cannot be earlier than or equal to the start time (${formatTimeTo12Hour(startTime)}).\n\n` +
            `Please specify an end time that occurs after the start time.`
        );
        return;
    }

    const deptVal = (deptInput && deptInput.value.trim()) ? deptInput.value.trim().toUpperCase() : "CUSTOM";
    const numVal = (numInput && numInput.value.trim()) ? numInput.value.trim().toUpperCase() : String(window.customCourseCounter || 1);

    const isEditing = typeof editingCustomClassCode === "string" && editingCustomClassCode.length > 0;
    const classCode = isEditing ? editingCustomClassCode : `CUST-${Date.now()}`;

    const customCourseObj = {
        subject_code: deptVal,
        course_number: numVal,
        course_title: `Custom Added Course (${deptVal} ${numVal})`,
        name: `Custom Added Course (${deptVal} ${numVal})`,
        credits: 3,
        sections_count: 1,
        is_custom: true,
        req_id: "user-added",
        req_group_name: "Added Courses",
        req_needed: 1
    };

    const customSectionObj = {
        class_code: classCode,
        section: "01",
        days: selectedDays,
        start_time: startTime,
        end_time: endTime,
        location: "Self-Enrolled (WISER)",
        is_online: false,
        status: "Open",
        term: getActiveTerm()
    };

    const conflict = checkLockedSectionConflict(customSectionObj, `${deptVal} ${numVal}`, isEditing ? classCode : null);
    if (conflict) {
        alert(
            `⚠️ Time Conflict Detected:\n\n` +
            `Custom Section ${deptVal} ${numVal} (${selectedDays.join(", ")} from ${formatTimeTo12Hour(startTime)} to ${formatTimeTo12Hour(endTime)}) overlaps with an already locked section:\n` +
            `• ${conflict}\n\n` +
            `Please adjust the meeting days or hours.`
        );
        return;
    }

    if (isEditing) {
        const idx = lockedSections.findIndex(ls => String(ls.section.class_code) === String(classCode));
        if (idx !== -1) {
            lockedSections[idx].course = customCourseObj;
            lockedSections[idx].section = customSectionObj;
            lockedSections[idx].is_custom = true;
        }
    } else {
        lockedSections.push({
            course: customCourseObj,
            section: customSectionObj,
            is_custom: true
        });
        customCourseCounter++;
        window.customCourseCounter = customCourseCounter;
    }

    renderAllLockedSections();
    renderCandidatePoolUI();
    closeCustomModal();
}

// =================================================================
// 8. CATALOG LIVE SEARCH & GEN ED FILTERS
// =================================================================
let searchDebounceTimer = null;
let currentSearchResults = [];
let searchAbortController = null;
let lastSearchQuery = "";

function updateCatalogSearchButtons() {
    const resultsList = document.getElementById("catalogResultsList");
    if (!resultsList) return;

    const poolSet = new Set(
        candidatePool.map(c => `${String(c.subject_code).trim().toUpperCase()} ${String(c.course_number).trim().toUpperCase()}`)
    );
    const lockedSet = new Set(
        lockedSections.map(ls => `${String(ls.course.subject_code).trim().toUpperCase()} ${String(ls.course.course_number).trim().toUpperCase()}`)
    );

    const searchCards = resultsList.querySelectorAll(".candidate-item-card");
    searchCards.forEach(card => {
        const codeElem = card.querySelector(".candidate-code");
        const btn = card.querySelector(".btn-add-pool");
        if (!codeElem || !btn) return;

        const courseCode = codeElem.textContent.trim().toUpperCase();

        if (lockedSet.has(courseCode)) {
            btn.textContent = "Locked";
            btn.className = "btn-add-pool added";
            btn.disabled = true;
        } else if (poolSet.has(courseCode)) {
            btn.textContent = "In Pool";
            btn.className = "btn-add-pool added";
            btn.disabled = true;
        } else {
            btn.textContent = "+ Add";
            btn.className = "btn-add-pool";
            btn.disabled = false;
        }
    });
}

function toggleGenEdDropdown(e) {
    if (e) e.stopPropagation();
    const menu = document.getElementById("genEdDropdownMenu");
    if (menu) menu.classList.toggle("show");
}

function getSelectedGenEds() {
    return Array.from(document.querySelectorAll(".gened-checkbox:checked")).map(cb => cb.value);
}

function onGenEdChange() {
    const selected = getSelectedGenEds();
    const countBadge = document.getElementById("genEdSelectedCount");
    if (countBadge) countBadge.textContent = selected.length;
    executeCatalogSearch();
}

function executeCatalogSearch() {
    clearTimeout(searchDebounceTimer);
    const catalogInput = document.getElementById("catalogLiveSearch");
    const resultsList = document.getElementById("catalogResultsList");
    const catalogCountBadge = document.getElementById("catalogResultCount");

    const query = catalogInput ? catalogInput.value.trim() : "";
    const selectedGenEds = getSelectedGenEds();
    const activeTerm = getActiveTerm();

    if (!query && selectedGenEds.length === 0) {
        if (searchAbortController) searchAbortController.abort();
        if (resultsList) {
            resultsList.innerHTML = `<div class="empty-placeholder empty-grid-span">Type a course name/number or select Gen Ed categories to view offerings.</div>`;
        }
        if (catalogCountBadge) catalogCountBadge.textContent = "0 Found";
        currentSearchResults = [];
        lastSearchQuery = "";
        return;
    }

    const searchKey = `${query}|${selectedGenEds.join(",")}|${activeTerm}`;
    if (searchKey === lastSearchQuery) return;

    searchDebounceTimer = setTimeout(() => {
        lastSearchQuery = searchKey;

        if (searchAbortController) searchAbortController.abort();
        searchAbortController = new AbortController();

        const params = new URLSearchParams();
        if (query) params.append("q", query);
        if (selectedGenEds.length > 0) params.append("geneds", selectedGenEds.join(","));
        params.append("term", activeTerm);

        // Send completed courses and credits for real-time incremental prerequisite checking
        if (Array.isArray(window.COMPLETED_COURSES) && window.COMPLETED_COURSES.length > 0) {
            params.append("completed", window.COMPLETED_COURSES.join(","));
        }
        if (window.EARNED_CREDITS) {
            params.append("credits", window.EARNED_CREDITS);
        }

        fetch(`/api/courses?${params.toString()}`, { signal: searchAbortController.signal })
            .then(res => res.json())
            .then(data => {
                // Rule: If course has 0 active sections, exclude completely
                data = data.filter(c => (c.sections_count || 0) > 0);

                currentSearchResults = data;
                if (catalogCountBadge) catalogCountBadge.textContent = `${data.length} Found`;
                if (!resultsList) return;

                if (data.length === 0) {
                    resultsList.innerHTML = `<div class="empty-placeholder empty-grid-span">No matching courses with active sections found in catalog for ${escapeHtml(activeTerm)}.</div>`;
                    return;
                }

                const poolSet = new Set(
                    candidatePool.map(p => `${String(p.subject_code).trim().toUpperCase()} ${String(p.course_number).trim().toUpperCase()}`)
                );
                const lockedSet = new Set(
                    lockedSections.map(ls => `${String(ls.course.subject_code).trim().toUpperCase()} ${String(ls.course.course_number).trim().toUpperCase()}`)
                );
                const completedSet = new Set(
                    (Array.isArray(window.COMPLETED_COURSES) ? window.COMPLETED_COURSES : []).map(c => String(c).replace(/\s+/g, "").toUpperCase())
                );

                resultsList.innerHTML = data.map((c, idx) => {
                    const courseKey = `${String(c.subject_code).trim().toUpperCase()} ${String(c.course_number).trim().toUpperCase()}`;
                    const compactKey = `${String(c.subject_code).trim().toUpperCase()}${String(c.course_number).trim().toUpperCase()}`;
                    
                    const inPool = poolSet.has(courseKey);
                    const isLocked = lockedSet.has(courseKey);
                    const isAdded = inPool || isLocked;
                    const isAlreadyCompleted = completedSet.has(compactKey);
                    const courseTitle = c.course_title || c.name || `${c.subject_code} ${c.course_number}`;
                    const safeTitle = escapeHtml(courseTitle);

                    const hasMissingPrereqs = Array.isArray(c.missing_prereqs) && c.missing_prereqs.length > 0;
                    const missingText = hasMissingPrereqs ? escapeHtml(c.missing_prereqs.join(", ")) : "";

                    return `
                        <div class="candidate-item-card ${hasMissingPrereqs ? "has-prereq-warning" : ""}">
                            <div class="card-header-flex">
                                <div class="candidate-code">${c.code}</div>
                                <div class="candidate-actions">
                                    <button type="button" class="btn-details" onclick="openCourseModal('${c.subject_code}', '${c.course_number}', null, '${safeTitle}')">Details</button>
                                    <button type="button" class="btn-add-pool ${isAdded ? "added" : ""}" 
                                            onclick="addCourseByIndex(${idx}, this)" ${isAdded ? "disabled" : ""}>
                                        ${isAdded ? (isLocked ? "Locked" : "In Pool") : "+ Add"}
                                    </button>
                                </div>
                            </div>
                            
                            <div class="candidate-name" title="${safeTitle}">${safeTitle}</div>
                            
                            ${isAlreadyCompleted ? `
                                <div class="completed-warning-card">
                                    ⚠️ <strong>Already Taken:</strong> Scanned as completed in your degree audit. You can still add it if repeating.
                                </div>
                            ` : ""}

                            ${hasMissingPrereqs && !isAlreadyCompleted ? `
                                <div class="prereq-warning-card" title="${escapeHtml(c.prereq_warning || ('Needs ' + missingText))}">
                                    ⚠️ <strong>Missing Prereq:</strong> Needs ${missingText}
                                </div>
                            ` : ""}

                            <div class="candidate-meta">
                                <span class="meta-pill pill-blue">${c.credits} cr</span>
                                <span class="meta-pill" title="${escapeHtml(c.gen_ed)}">${c.gen_ed !== "None" ? escapeHtml(c.gen_ed) : "No Gen Ed"}</span>
                                ${!hasMissingPrereqs && !isAlreadyCompleted && c.prereqs && c.prereqs !== "None" ? '<span class="meta-pill pill-prereq-met">✓ Prereqs Met</span>' : ""}
                                <span>${c.sections_count} sections</span>
                            </div>
                        </div>
                    `;
                }).join("");
            })
            .catch(err => {
                if (err.name !== "AbortError") console.error("Error searching courses:", err);
            });
    }, 300);
}

function addCourseByIndex(index, btnElement) {
    const courseData = currentSearchResults[index];
    if (courseData) addCourseToPool(courseData, btnElement);
}

// =================================================================
// 9. WEEKLY TIMETABLE GRID VISUALIZER
// =================================================================
const DAY_MAP = {
    "MO": "Mo", "M": "Mo", "MON": "Mo", "MONDAY": "Mo",
    "TU": "Tu", "T": "Tu", "TUE": "Tu", "TUESDAY": "Tu",
    "WE": "We", "W": "We", "WED": "We", "WEDNESDAY": "We",
    "TH": "Th", "R": "Th", "THU": "Th", "THUR": "Th", "THURS": "Th", "THURSDAY": "Th",
    "FR": "Fr", "F": "Fr", "FRI": "Fr", "FRIDAY": "Fr",
    "SA": "Sa", "S": "Sa", "SAT": "Sa", "SATURDAY": "Sa",
    "SU": "Su", "U": "Su", "SUN": "Su", "SUNDAY": "Su"
};

function renderTimetableGrid() {
    const rawSchedule = window.SERVER_SCHEDULE_DATA || [];
    const PIXELS_PER_HOUR = 50;

    if (!Array.isArray(rawSchedule) || rawSchedule.length === 0) return;

    const timedItems = rawSchedule.filter(i => 
        i.start_time && 
        i.end_time && 
        i.start_time !== "TBA" && 
        i.end_time !== "TBA" && 
        Array.isArray(i.days) && 
        i.days.length > 0
    );

    if (timedItems.length === 0) return;

    let minStartMin = 1440;
    let maxEndMin = 0;

    timedItems.forEach(item => {
        const s = timeStrToMinutes(item.start_time);
        const e = timeStrToMinutes(item.end_time);
        if (s < minStartMin) minStartMin = s;
        if (e > maxEndMin) maxEndMin = e;
    });

    const startHour = Math.max(0, Math.floor(minStartMin / 60) - 1);
    const endHour = Math.min(24, Math.ceil(maxEndMin / 60) + 1);
    const totalHours = endHour - startHour;

    const timeContainer = document.getElementById("timeLabelsContainer");
    if (timeContainer) {
        timeContainer.innerHTML = "";
        for (let h = startHour; h < endHour; h++) {
            const slot = document.createElement("div");
            slot.className = "time-slot";
            const displayHour = h === 0 ? 12 : (h > 12 ? h - 12 : h);
            const ampm = h < 12 ? "AM" : "PM";
            slot.textContent = `${displayHour} ${ampm}`;
            timeContainer.appendChild(slot);
        }
    }

    const gridHeight = totalHours * PIXELS_PER_HOUR;
    ["col-Mo", "col-Tu", "col-We", "col-Th", "col-Fr", "col-Sa", "col-Su"].forEach(colId => {
        const col = document.getElementById(colId);
        if (col) {
            col.style.height = `${gridHeight}px`;
            col.innerHTML = "";
        }
    });

    timedItems.forEach(item => {
        const startMin = timeStrToMinutes(item.start_time);
        const endMin = timeStrToMinutes(item.end_time);
        const baseMin = startHour * 60;

        const topOffset = ((startMin - baseMin) / 60) * PIXELS_PER_HOUR;
        const blockHeight = ((endMin - startMin) / 60) * PIXELS_PER_HOUR;
        const formattedTimeRange = `${formatTimeTo12Hour(item.start_time)} - ${formatTimeTo12Hour(item.end_time)}`;

        item.days.forEach(rawDay => {
            const cleanDay = String(rawDay).trim().toUpperCase();
            const standardDay = DAY_MAP[cleanDay] || rawDay;
            const col = document.getElementById(`col-${standardDay}`);
            if (!col) return;

            const block = document.createElement("div");
            block.className = "event-block";
            block.style.top = `${topOffset}px`;
            block.style.height = `${blockHeight}px`;

            const displayName = item.course_id || `${item.course_name} ${item.course_number}`;
            const displayLoc = item.location || (item.is_custom ? "Custom Event" : "TBA");

            block.innerHTML = `
                <div class="event-title">${escapeHtml(displayName)} (Sec ${escapeHtml(item.section)})</div>
                <div class="event-time">${formattedTimeRange}</div>
                <div class="event-loc">${escapeHtml(displayLoc)}</div>
            `;
            col.appendChild(block);
        });
    });
}

// =================================================================
// 10. COURSE DETAILS MODAL
// =================================================================
function openCourseModal(dept, num, targetSection = null, preloadedTitle = null) {
    const modal = document.getElementById("courseDetailsModal");
    if (!modal) return;

    activeModalCourse = { 
        subject_code: String(dept).trim().toUpperCase(), 
        course_number: String(num).trim().toUpperCase(),
        course_title: preloadedTitle || ""
    };
    activeModalSectionIndex = 0;

    modal.style.display = "flex";
    
    const initialTitle = preloadedTitle ? `${dept} ${num} - ${preloadedTitle}` : `${dept} ${num}`;
    document.getElementById("modalCourseTitle").textContent = initialTitle;
    document.getElementById("modalCourseMeta").textContent = "Loading section details...";
    document.getElementById("modalCourseDesc").textContent = "";
    document.getElementById("modalSectionTabs").innerHTML = "";
    document.getElementById("modalSectionContent").innerHTML = '<div class="empty-placeholder">Fetching sections from catalog...</div>';

    const term = getActiveTerm();
    const completedParam = Array.isArray(window.COMPLETED_COURSES) ? window.COMPLETED_COURSES.join(",") : "";
    const creditsParam = window.EARNED_CREDITS || 0;

    fetch(`/api/course-sections?name=${encodeURIComponent(dept)}&number=${encodeURIComponent(num)}&term=${encodeURIComponent(term)}&completed=${encodeURIComponent(completedParam)}&credits=${encodeURIComponent(creditsParam)}`)
        .then(res => res.json())
        .then(data => {
            if (data.error) {
                document.getElementById("modalSectionContent").innerHTML = `<div class="empty-placeholder">${escapeHtml(data.error)}</div>`;
                return;
            }

            const resolvedTitle = data.course_title || preloadedTitle;
            if (resolvedTitle && resolvedTitle.toLowerCase() !== `${dept} ${num}`.toLowerCase()) {
                document.getElementById("modalCourseTitle").textContent = `${dept} ${num} - ${resolvedTitle}`;
            } else {
                document.getElementById("modalCourseTitle").textContent = `${dept} ${num}`;
            }

            activeModalCourse.name = resolvedTitle || data.description;
            activeModalCourse.course_title = resolvedTitle;
            activeModalCourse.credits = data.sections.length ? data.sections[0].credits : 3;
            activeModalCourse.sections_count = data.sections.length;

            document.getElementById("modalCourseMeta").textContent = `${data.sections.length} Active Section${data.sections.length === 1 ? "" : "s"}`;
            document.getElementById("modalCourseDesc").textContent = data.description;
            document.getElementById("modalCourseGenEd").textContent = `Gen Ed: ${data.gen_ed}`;

            // Incremental Prerequisite Breakdown Display
            const prereqEl = document.getElementById("modalCoursePrereq");
            if (prereqEl) {
                if (data.prereq_eval && data.prereq_eval.missing && data.prereq_eval.missing.length > 0) {
                    prereqEl.className = "meta-pill pill-prereq-missing";
                    prereqEl.innerHTML = `⚠️ <strong>Prereqs:</strong> ${escapeHtml(data.prerequisites)} <span class="missing-highlight">(Missing: Needs ${escapeHtml(data.prereq_eval.missing.join(", "))})</span>`;
                } else if (data.prerequisites && data.prerequisites !== "None") {
                    prereqEl.className = "meta-pill pill-prereq-met";
                    prereqEl.innerHTML = `✓ <strong>Prereqs Met:</strong> ${escapeHtml(data.prerequisites)}`;
                } else {
                    prereqEl.className = "meta-pill";
                    prereqEl.textContent = "Prereqs: None";
                }
            }

            currentModalSections = data.sections;

            const lockBtn = document.getElementById("btnModalLockSection");
            if (data.sections.length === 0) {
                document.getElementById("modalSectionContent").innerHTML = `<div class="empty-placeholder">No active sections offered for ${escapeHtml(term)}.</div>`;
                if (lockBtn) lockBtn.style.display = "none";
                return;
            }

            if (lockBtn) lockBtn.style.display = "inline-block";

            let sectionToSelect = targetSection;
            if (!sectionToSelect) {
                const lockedMatch = lockedSections.find(ls => 
                    String(ls.course.subject_code).trim().toUpperCase() === activeModalCourse.subject_code &&
                    String(ls.course.course_number).trim().toUpperCase() === activeModalCourse.course_number
                );
                if (lockedMatch && lockedMatch.section) {
                    sectionToSelect = lockedMatch.section.section;
                }
            }

            let initialIndex = 0;
            if (sectionToSelect) {
                const cleanTarget = String(sectionToSelect).trim().toUpperCase();
                const foundIndex = data.sections.findIndex(s => String(s.section).trim().toUpperCase() === cleanTarget);
                if (foundIndex !== -1) initialIndex = foundIndex;
            }

            activeModalSectionIndex = initialIndex;

            const tabsContainer = document.getElementById("modalSectionTabs");
            tabsContainer.innerHTML = data.sections.map((s, idx) => {
                const isDisc = String(s.section).trim().toUpperCase().endsWith("D");
                const badge = isDisc ? " [Disc]" : " [Lec]";
                return `
                    <button type="button" class="section-tab-btn ${idx === initialIndex ? "active" : ""}" 
                            onclick="switchSectionTab(${idx})">
                        Sec ${escapeHtml(s.section)}${badge}
                    </button>
                `;
            }).join("");

            renderActiveSectionTab(initialIndex);
        })
        .catch(err => {
            console.error("Failed to load section details:", err);
            document.getElementById("modalSectionContent").innerHTML = '<div class="empty-placeholder">Failed to load section details.</div>';
        });
}

function switchSectionTab(index) {
    activeModalSectionIndex = index;
    document.querySelectorAll(".section-tab-btn").forEach((btn, idx) => {
        btn.classList.toggle("active", idx === index);
    });
    renderActiveSectionTab(index);
}

function renderActiveSectionTab(index) {
    const s = currentModalSections[index];
    if (!s || !activeModalCourse) return;

    const formattedTime = (s.start_time && s.start_time !== "TBA") 
        ? `${formatTimeTo12Hour(s.start_time)} - ${formatTimeTo12Hour(s.end_time)}` 
        : "";

    const statusText = String(s.status || (s.is_full ? "Closed" : "Open")).trim();
    const isWaitlist = statusText.toLowerCase().includes("wait");
    const isClosed = statusText.toLowerCase() === "closed";

    let statusBadgeColor = "#16a34a";
    if (isWaitlist) statusBadgeColor = "#d97706";
    if (isClosed) statusBadgeColor = "#dc2626";

    const content = document.getElementById("modalSectionContent");
    content.innerHTML = `
        <div class="section-info-grid">
            <div class="info-item">
                <div class="info-label">Class Number / Code</div>
                <div class="info-value">#${escapeHtml(s.class_code)}</div>
            </div>
            <div class="info-item">
                <div class="info-label">Section & Term</div>
                <div class="info-value">Section ${escapeHtml(s.section)} (${escapeHtml(s.term)})</div>
            </div>
            <div class="info-item">
                <div class="info-label">Schedule & Days</div>
                <div class="info-value">${s.days.length ? escapeHtml(s.days.join(", ")) : "Online"} ${formattedTime ? `(${formattedTime})` : ""}</div>
            </div>
            <div class="info-item">
                <div class="info-label">Meeting Location</div>
                <div class="info-value">${escapeHtml(s.location)}</div>
            </div>
            <div class="info-item">
                <div class="info-label">Instructor(s)</div>
                <div class="info-value">${escapeHtml(s.instructors.join(", "))}</div>
            </div>
            <div class="info-item">
                <div class="info-label">Credits & Delivery</div>
                <div class="info-value">${s.credits} Credits • ${escapeHtml(s.delivery_type)}</div>
            </div>
            <div class="info-item" style="grid-column: 1 / -1;">
                <div class="info-label">Enrollment Status</div>
                <div class="info-value">
                    <span style="color: ${statusBadgeColor}; font-weight: 700;">${escapeHtml(statusText)}</span>
                </div>
            </div>
        </div>
    `;

    const lockBtn = document.getElementById("btnModalLockSection");
    if (!lockBtn) return;

    const targetDept = String(activeModalCourse.subject_code || "").trim().toUpperCase();
    const targetNum = String(activeModalCourse.course_number || "").trim().toUpperCase();
    const isDisc = String(s.section).trim().toUpperCase().endsWith("D");

    const lockedItem = lockedSections.find(ls => {
        if (!ls.course || !ls.section) return false;
        const curDept = String(ls.course.subject_code || "").trim().toUpperCase();
        const curNum = String(ls.course.course_number || "").trim().toUpperCase();
        const curIsDisc = String(ls.section.section).trim().toUpperCase().endsWith("D");
        return curDept === targetDept && curNum === targetNum && curIsDisc === isDisc;
    });

    if (lockedItem) {
        if (String(lockedItem.section.class_code) === String(s.class_code)) {
            lockBtn.textContent = "🔒 Current Active Section";
            lockBtn.disabled = true;
            lockBtn.className = "btn-secondary btn-modal-lock";
        } else {
            lockBtn.textContent = `🔄 Switch to Sec ${s.section}`;
            lockBtn.disabled = false;
            lockBtn.className = "btn-primary btn-modal-lock";
        }
    } else {
        lockBtn.textContent = "🔒 Lock This Section";
        lockBtn.disabled = false;
        lockBtn.className = "btn-primary btn-modal-lock";
    }
}

function lockSectionFromModal() {
    if (!activeModalCourse || !currentModalSections[activeModalSectionIndex]) return;

    const newSection = currentModalSections[activeModalSectionIndex];
    const targetDept = String(activeModalCourse.subject_code || "").trim().toUpperCase();
    const targetNum = String(activeModalCourse.course_number || "").trim().toUpperCase();
    const courseCode = `${targetDept} ${targetNum}`;

    const newIsDisc = String(newSection.section).trim().toUpperCase().endsWith("D");
    const existingIndex = lockedSections.findIndex(ls => {
        if (!ls.course || !ls.section) return false;
        const curDept = String(ls.course.subject_code || "").trim().toUpperCase();
        const curNum = String(ls.course.course_number || "").trim().toUpperCase();
        const curIsDisc = String(ls.section.section).trim().toUpperCase().endsWith("D");
        return curDept === targetDept && curNum === targetNum && curIsDisc === newIsDisc;
    });

    const isSwapping = existingIndex !== -1;
    const oldSection = isSwapping ? lockedSections[existingIndex].section : null;
    const oldClassCode = oldSection ? String(oldSection.class_code) : null;

    const conflict = checkLockedSectionConflict(newSection, courseCode, oldClassCode);
    if (conflict) {
        alert(
            `⚠️ Time Conflict Detected:\n\n` +
            `${courseCode} (Sec ${newSection.section}) overlaps with an already locked section:\n` +
            `• ${conflict}\n\n` +
            `Please select a different section or unlock the conflicting course first.`
        );
        return;
    }

    if (isSwapping) {
        lockedSections[existingIndex].section = newSection;
        renderAllLockedSections();
        syncLockedInput();
    } else {
        lockSection(targetDept, targetNum, newSection);
    }

    closeCourseModal();
    updateCatalogSearchButtons();
}

function closeCourseModal(e) {
    if (e && e.target) {
        const modal = document.getElementById("courseDetailsModal");
        if (e.target !== modal && !e.target.closest(".btn-close-modal")) return;
    }

    const modal = document.getElementById("courseDetailsModal");
    if (modal) modal.style.display = "none";
    activeModalCourse = null;
    currentModalSections = [];
}

// =================================================================
// 11. CLIPBOARD & HELPER UTILITIES
// =================================================================
function copyClassCode(buttonElement, classCode) {
    if (!classCode) return;
    navigator.clipboard.writeText(classCode).then(() => {
        const originalHtml = buttonElement.innerHTML;
        buttonElement.innerHTML = `<span>#${classCode}</span> ✓`;
        buttonElement.classList.add("copied");
        setTimeout(() => {
            buttonElement.innerHTML = originalHtml;
            buttonElement.classList.remove("copied");
        }, 1500);
    }).catch(err => {
        console.error("Failed to copy class code:", err);
    });
}

// Explicit window bindings for HTML inline events
window.openCourseModal = openCourseModal;
window.closeCourseModal = closeCourseModal;
window.switchSectionTab = switchSectionTab;
window.lockSectionFromModal = lockSectionFromModal;
window.copyClassCode = copyClassCode;
window.addCourseByIndex = addCourseByIndex;
window.removeCourseFromPool = removeCourseFromPool;
window.clearAllCandidateCourses = clearAllCandidateCourses;
window.handleSectionSelectChange = handleSectionSelectChange;
window.unlockSection = unlockSection;
window.toggleGenEdDropdown = toggleGenEdDropdown;
window.onGenEdChange = onGenEdChange;
window.openCustomModal = openCustomModal;
window.closeCustomModal = closeCustomModal;
window.submitCustomSection = submitCustomSection;
window.toggleAuditAccordion = toggleAuditAccordion;
window.toggleReqGroup = toggleReqGroup;

// =================================================================
// 12. DOM READY INITIALIZATION & EVENT BINDING
// =================================================================
document.addEventListener("DOMContentLoaded", () => {
    initCandidatePool();
    renderTimetableGrid();

    // Degree Audit Drag-and-Drop Uploader
    const dropZone = document.getElementById("dropZone");
    const fileInput = document.getElementById("auditInput");
    const fileNameDisplay = document.getElementById("fileName");
    const MAX_FILE_SIZE = 100 * 1024; // 100 KB limit for WISER text PDFs

    function validateFileSize(file) {
        if (file && file.size > MAX_FILE_SIZE) {
            alert("The selected PDF file is larger than 100 KB. Please upload an official text-based audit downloaded from WISER.");
            if (fileInput) fileInput.value = "";
            if (fileNameDisplay) fileNameDisplay.textContent = "";
            return false;
        }
        return true;
    }

    if (dropZone && fileInput) {
        dropZone.addEventListener("click", () => fileInput.click());

        ["dragenter", "dragover"].forEach(evt => {
            dropZone.addEventListener(evt, e => {
                e.preventDefault();
                dropZone.classList.add("dragover");
            });
        });

        ["dragleave", "drop"].forEach(evt => {
            dropZone.addEventListener(evt, e => {
                e.preventDefault();
                dropZone.classList.remove("dragover");
            });
        });

        dropZone.addEventListener("drop", e => {
            if (e.dataTransfer.files.length > 0 && validateFileSize(e.dataTransfer.files[0])) {
                fileInput.files = e.dataTransfer.files;
                if (fileNameDisplay) fileNameDisplay.textContent = `Selected: ${e.dataTransfer.files[0].name}`;
            }
        });

        fileInput.addEventListener("change", () => {
            if (fileInput.files.length > 0 && validateFileSize(fileInput.files[0])) {
                if (fileNameDisplay) fileNameDisplay.textContent = `Selected: ${fileInput.files[0].name}`;
            }
        });
    }

    // Option 2 Snapshot & Restore Search in Candidate Pool
    const searchInput = document.getElementById("candidateSearchInput");
    let preSearchOpenGroups = null;

    if (searchInput) {
        searchInput.addEventListener("input", function() {
            const query = this.value.trim().toLowerCase();
            const queryCompact = query.replace(/\s+/g, "");
            const groupBlocks = document.querySelectorAll(".candidate-group-block");
            let totalVisibleCourses = 0;

            if (query) {
                // Save layout snapshot on first character typed
                if (preSearchOpenGroups === null) {
                    preSearchOpenGroups = new Set(openReqGroups);
                }

                groupBlocks.forEach(block => {
                    const cards = block.querySelectorAll(".candidate-item-card");
                    const grid = block.querySelector(".candidate-group-grid");
                    const arrow = block.querySelector(".accordion-arrow");
                    const courseCountPill = block.querySelector(".pill-course-count");
                    const sectionCountPill = block.querySelector(".pill-section-count");

                    let matchingCourses = 0;
                    let matchingSections = 0;

                    cards.forEach(card => {
                        const rawCode = (card.getAttribute("data-code") || "").toLowerCase();
                        const compactCode = rawCode.replace(/\s+/g, "");
                        const cardTitle = (card.querySelector(".candidate-name")?.textContent || "").toLowerCase();
                        const secs = parseInt(card.getAttribute("data-sections-count"), 10) || 1;

                        if (rawCode.includes(query) || compactCode.includes(queryCompact) || cardTitle.includes(query)) {
                            card.style.display = "block";
                            matchingCourses++;
                            matchingSections += secs;
                            totalVisibleCourses++;
                        } else {
                            card.style.display = "none";
                        }
                    });

                    if (matchingCourses > 0) {
                        block.style.display = "block";
                        if (grid) grid.classList.remove("collapsed");
                        if (arrow) arrow.classList.add("arrow-rotated");
                        if (courseCountPill) courseCountPill.textContent = `${matchingCourses} Course${matchingCourses === 1 ? "" : "s"}`;
                        if (sectionCountPill) sectionCountPill.textContent = `${matchingSections} Section${matchingSections === 1 ? "" : "s"}`;
                    } else {
                        block.style.display = "none";
                    }
                });
            } else {
                // Restore pre-search layout snapshot when cleared
                if (preSearchOpenGroups !== null) {
                    openReqGroups = new Set(preSearchOpenGroups);
                    preSearchOpenGroups = null;
                }

                groupBlocks.forEach(block => {
                    const gid = block.getAttribute("data-req-id");
                    const cards = block.querySelectorAll(".candidate-item-card");
                    const grid = block.querySelector(".candidate-group-grid");
                    const arrow = block.querySelector(".accordion-arrow");
                    const courseCountPill = block.querySelector(".pill-course-count");
                    const sectionCountPill = block.querySelector(".pill-section-count");

                    cards.forEach(card => card.style.display = "block");
                    block.style.display = "block";
                    totalVisibleCourses += cards.length;

                    const shouldBeOpen = openReqGroups.has(gid);
                    if (grid) grid.classList.toggle("collapsed", !shouldBeOpen);
                    if (arrow) arrow.classList.toggle("arrow-rotated", shouldBeOpen);

                    const totalCourses = block.getAttribute("data-total-courses");
                    const totalSections = block.getAttribute("data-total-sections");
                    if (courseCountPill && totalCourses) courseCountPill.textContent = `${totalCourses} Course${totalCourses === "1" ? "" : "s"}`;
                    if (sectionCountPill && totalSections) sectionCountPill.textContent = `${totalSections} Section${totalSections === "1" ? "" : "s"}`;
                });
            }

            const countBadge = document.getElementById("candidateCount");
            if (countBadge) countBadge.textContent = `${totalVisibleCourses} Courses`;
        });
    }

    // Catalog Search Listener
    const catalogInput = document.getElementById("catalogLiveSearch");
    if (catalogInput) {
        catalogInput.addEventListener("input", executeCatalogSearch);
    }

    // Click outside to dismiss Gen Ed dropdown
    document.addEventListener("click", e => {
        const menu = document.getElementById("genEdDropdownMenu");
        const btn = document.getElementById("genEdDropdownBtn");
        if (menu && menu.classList.contains("show")) {
            if (!menu.contains(e.target) && !btn?.contains(e.target)) {
                menu.classList.remove("show");
            }
        }
    });

    // Custom Section Modal Actions
    document.getElementById("btnOpenCustomModal")?.addEventListener("click", openCustomModal);
    document.getElementById("btnCloseCustomCard")?.addEventListener("click", closeCustomModal);
    document.getElementById("btnCancelCustomCard")?.addEventListener("click", closeCustomModal);
    document.getElementById("btnSubmitCustomSection")?.addEventListener("click", submitCustomSection);

    // Target count solver complexity calculation
    const targetCountInput = document.getElementById("target_course_count");
    if (targetCountInput) {
        targetCountInput.addEventListener("input", verifyPoolComplexity);
    }

    // Preserve scroll position across solver form POST requests
    const restrictionsForm = document.querySelector("form.restrictions-card");
    if (restrictionsForm) {
        restrictionsForm.addEventListener("submit", () => {
            sessionStorage.setItem("planner_scroll_pos", window.scrollY);
        });
    }

    const savedScrollPos = sessionStorage.getItem("planner_scroll_pos");
    if (savedScrollPos !== null) {
        sessionStorage.removeItem("planner_scroll_pos");
        const timetableElem = document.getElementById("timetableSection");
        if (timetableElem) {
            timetableElem.scrollIntoView({ behavior: "smooth", block: "start" });
        } else {
            window.scrollTo({ top: parseInt(savedScrollPos, 10), behavior: "instant" });
        }
    }

    // Floating Back-To-Top Button
    const backToTopBtn = document.getElementById("btnBackToTop");
    if (backToTopBtn) {
        window.addEventListener("scroll", () => {
            backToTopBtn.classList.toggle("show", window.scrollY > 300);
        }, { passive: true });

        backToTopBtn.addEventListener("click", () => {
            window.scrollTo({ top: 0, behavior: "smooth" });
        });
    }
});