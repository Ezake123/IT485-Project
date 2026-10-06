// =================================================================
// 0. GLOBAL CORE STATE & TOPOLOGICAL DEFINITIONS
// =================================================================
let candidatePool = [];
let lockedSections = [];
let currentModalSections = [];
let activeModalCourse = null;
let activeModalSectionIndex = 0;
let customCourseCounter = 1;

window.candidatePool = candidatePool;
window.lockedSections = lockedSections;
window.customCourseCounter = customCourseCounter;

// Dynamic active term retrieval from initial page state
function getActiveTerm() {
    //Known good term
    return window.ACTIVE_TERM || "Fall 2026";
}

// =================================================================
// 1. TIME CONVERSION & FORMATTING HELPERS
// =================================================================
function minutesToTimeStr(totalMinutes) {
    if (totalMinutes >= 1440) return "24:00";
    const h = Math.floor(totalMinutes / 60);
    const m = totalMinutes % 60;
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

function timeStrToMinutes(str) {
    if (!str || str === 'TBA') return 0;
    const clean = str.trim().toUpperCase();

    const isPM = clean.includes('PM');
    const isAM = clean.includes('AM');
    const numericPart = clean.replace(/[AP]M/, '').trim();

    const parts = numericPart.split(':').map(Number);
    if (parts.length < 2 || isNaN(parts[0]) || isNaN(parts[1])) return 0;

    let hours = parts[0];
    const minutes = parts[1];

    if (isAM && hours === 12) hours = 0;
    if (isPM && hours < 12) hours += 12;

    return (hours * 60) + minutes;
}

function formatTimeTo12Hour(timeStr) {
    if (!timeStr || timeStr === 'TBA') return '';
    const clean = timeStr.trim();
    
    if (clean.toUpperCase().includes('AM') || clean.toUpperCase().includes('PM')) {
        return clean;
    }

    const parts = clean.split(':');
    if (parts.length < 2) return clean;

    let hour = parseInt(parts[0], 10);
    const minutePart = parseInt(parts[1], 10);

    if (isNaN(hour) || isNaN(minutePart)) return clean;

    const minutes = String(minutePart).padStart(2, '0');
    const ampm = hour >= 12 ? 'PM' : 'AM';

    hour = hour % 12;
    if (hour === 0) hour = 12;

    return `${hour}:${minutes} ${ampm}`;
}

function checkLockedSectionConflict(candidateSection, candidateTitle = "", ignoreClassCode = null) {
    if (!Array.isArray(lockedSections) || lockedSections.length === 0) return null;
    if (!candidateSection || !Array.isArray(candidateSection.days) || candidateSection.days.length === 0) return null;

    const candStart = timeStrToMinutes(candidateSection.start_time);
    const candEnd = timeStrToMinutes(candidateSection.end_time);

    if (candStart === 0 && candEnd === 0) return null;
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
            if (Math.max(candStart, secStart) < Math.min(candEnd, secEnd)) {
                const lockedTitle = `${item.course.subject_code} ${item.course.course_number} (Sec ${sec.section})`;
                const daysList = sec.days.join(', ');
                const timeStr = `${formatTimeTo12Hour(sec.start_time)} - ${formatTimeTo12Hour(sec.end_time)}`;
                return `${lockedTitle} on ${daysList} at ${timeStr}`;
            }
        }
    }

    return null;
}

// =================================================================
// 2. STATE SYNCHRONIZATION HELPERS
// =================================================================
function syncPoolInput() {
    const input = document.getElementById('selected_candidate_courses_json');
    if (input) {
        input.value = JSON.stringify(candidatePool);
    }
    const countBadge = document.getElementById('candidateCount');
    if (countBadge) {
        countBadge.textContent = `${candidatePool.length} Courses`;
    }
    verifyPoolComplexity();
}

function syncLockedInput() {
    const input = document.getElementById('locked_sections_json');
    if (input) {
        input.value = JSON.stringify(lockedSections);
    }
    const badge = document.getElementById('lockedCount');
    if (badge) {
        badge.textContent = `${lockedSections.length} Locked`;
    }
}

function verifyPoolComplexity() {
    const targetInput = document.getElementById('target_course_count');
    const targetK = parseInt(targetInput ? targetInput.value : 5, 10) || 5;
    
    const totalSections = candidatePool.reduce((sum, item) => sum + (parseInt(item.sections_count, 10) || 0), 0);
    const numCourses = candidatePool.length;

    const warningBanner = document.getElementById('complexityWarning');
    const countDisplay = document.getElementById('totalSectionCount');

    if (countDisplay) {
        countDisplay.textContent = totalSections;
    }

    if (!warningBanner || numCourses === 0) return;

    const avgSectionsPerCourse = totalSections / numCourses;
    const estimatedCombinations = Math.pow(avgSectionsPerCourse, Math.min(targetK, numCourses));
    const isOverThreshold = estimatedCombinations > 120000;

    warningBanner.style.display = isOverThreshold ? 'block' : 'none';
}

// =================================================================
// 3. CANDIDATE POOL & LOCKED SECTIONS MANAGEMENT
// =================================================================
function renderLockedCard(course, section) {
    const emptyLocked = document.getElementById('emptyLockedMsg');
    if (emptyLocked) emptyLocked.remove();

    const lockedList = document.getElementById('lockedList');
    if (!lockedList) return;

    const cardId = `locked-card-${section.class_code}`;
    let card = document.getElementById(cardId);
    const isNew = !card;

    if (isNew) {
        card = document.createElement('div');
        card.className = 'candidate-item-card locked-card';
        card.id = cardId;
    }

    const daysStr = (section.days && section.days.length) ? section.days.join(', ') : 'Online';
    const timeStr = (section.start_time !== 'TBA' && section.end_time !== 'TBA') 
        ? `${formatTimeTo12Hour(section.start_time)} - ${formatTimeTo12Hour(section.end_time)}` 
        : 'Asynchronous';
    const isCustom = String(section.class_code).startsWith('CUST-') || Boolean(course.is_custom);

    card.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: flex-start;">
            <div class="candidate-code">${course.subject_code} ${course.course_number} <span class="locked-badge-pill">Sec ${section.section}</span></div>
            <div style="display: flex; gap: 6px; align-items: center;">
                ${isCustom 
                    ? `<button type="button" class="btn-details" onclick="openCustomModal('${section.class_code}')">Edit</button>` 
                    : `<button type="button" class="btn-details" onclick="openCourseModal('${course.subject_code}', '${course.course_number}', '${section.section}')">Details</button>`}
                <button type="button" class="btn-remove-pool" onclick="unlockSection('${section.class_code}')" title="Unlock and remove section">✕</button>
            </div>
        </div>
        <div class="candidate-name" title="${course.name}">${course.name}</div>
        <div class="candidate-meta" style="margin-top: 4px;">
            <span>${daysStr} (${timeStr})</span>
            <span>#${section.class_code}</span>
        </div>
    `;

    if (isNew) {
        lockedList.appendChild(card);
    }
}

function lockSection(dept, num, sectionData) {
    const isDiscussion = String(sectionData.section).trim().toUpperCase().endsWith('D');

    const alreadyLockedThisCourse = lockedSections.filter(ls => 
        ls.course.subject_code === dept && 
        ls.course.course_number === num
    );

    const hasLockedLec = alreadyLockedThisCourse.some(ls => !String(ls.section.section).trim().toUpperCase().endsWith('D'));
    const hasLockedDisc = alreadyLockedThisCourse.some(ls => String(ls.section.section).trim().toUpperCase().endsWith('D'));

    if (!isDiscussion && hasLockedLec) {
        const oldLec = alreadyLockedThisCourse.find(ls => !String(ls.section.section).trim().toUpperCase().endsWith('D'));
        unlockSection(oldLec.section.class_code);
    }
    if (isDiscussion && hasLockedDisc) {
        const oldDisc = alreadyLockedThisCourse.find(ls => String(ls.section.section).trim().toUpperCase().endsWith('D'));
        unlockSection(oldDisc.section.class_code);
    }

    let courseData = null;
    const poolIdx = candidatePool.findIndex(c => c.subject_code === dept && c.course_number === num);
    if (poolIdx !== -1) {
        courseData = candidatePool[poolIdx];
    } else if (activeModalCourse && activeModalCourse.subject_code === dept && activeModalCourse.course_number === num) {
        courseData = activeModalCourse;
    }

    if (!courseData) return;

    lockedSections.push({
        course: courseData,
        section: sectionData
    });

    renderLockedCard(courseData, sectionData);
    syncLockedInput();

    const selectElem = document.getElementById(`section-select-${dept}-${num}`);
    let allSections = [];
    if (selectElem && selectElem.dataset.sectionsJson) {
        try { allSections = JSON.parse(selectElem.dataset.sectionsJson); } catch (e) {}
    }

    const hasDiscussions = allSections.some(s => String(s.section).trim().toUpperCase().endsWith('D'));
    const hasLectures = allSections.some(s => !String(s.section).trim().toUpperCase().endsWith('D'));
    const requiresBoth = hasDiscussions && hasLectures;

    const totalLockedForCourse = lockedSections.filter(ls => 
        ls.course.subject_code === dept && 
        ls.course.course_number === num
    ).length;

    if (!requiresBoth || totalLockedForCourse >= 2) {
        if (poolIdx !== -1) candidatePool.splice(poolIdx, 1);
        const poolCard = document.getElementById(`candidate-card-${dept}-${num}`);
        if (poolCard) poolCard.remove();
        syncPoolInput();
    } else {
        repopulateCandidateDropdown(dept, num);
    }

    updateCatalogSearchButtons();
}

function unlockSection(classCode) {
    const targetCode = String(classCode);
    const lockedItem = lockedSections.find(ls => String(ls.section.class_code) === targetCode);
    const isCustom = targetCode.startsWith("CUST-") || (lockedItem && (lockedItem.is_custom || (lockedItem.course && lockedItem.course.is_custom)));
    const courseToRestore = (lockedItem && !isCustom) ? lockedItem.course : null;

    lockedSections = lockedSections.filter(ls => String(ls.section.class_code) !== targetCode);
    window.lockedSections = lockedSections;

    document.querySelectorAll(`[id="locked-card-${targetCode}"]`).forEach(el => el.remove());

    if (lockedSections.length === 0) {
        const lockedList = document.getElementById('lockedList');
        if (lockedList) {
            lockedList.innerHTML = `
                <div class="empty-placeholder" id="emptyLockedMsg" style="grid-column: 1 / -1;">
                    No locked sections. Select a section dropdown in your pool or use "Lock This Section" in Details.
                </div>
            `;
        }
    }

    if (isCustom) {
        if (customCourseCounter > 1) {
            customCourseCounter--;
            window.customCourseCounter = customCourseCounter;
            const numInput = document.getElementById('customNumInput');
            if (numInput && !numInput.value) {
                numInput.placeholder = String(customCourseCounter);
            }
        }
    } else if (courseToRestore) {
        const dept = courseToRestore.subject_code;
        const num = courseToRestore.course_number;

        const existingCard = document.getElementById(`candidate-card-${dept}-${num}`);
        if (existingCard) {
            repopulateCandidateDropdown(dept, num);
        } else {
            addCourseToPool(courseToRestore);
            repopulateCandidateDropdown(dept, num);
        }
    }

    syncLockedInput();
    updateCatalogSearchButtons();
}

function addCourseToPool(courseData, btnElement) {
    const exists = candidatePool.some(c => c.subject_code === courseData.subject_code && c.course_number === courseData.course_number);
    const isLocked = lockedSections.some(ls => ls.course.subject_code === courseData.subject_code && ls.course.course_number === courseData.course_number);
    if (exists || isLocked) return;

    candidatePool.push(courseData);

    const emptyMsg = document.getElementById('emptyPoolMsg');
    if (emptyMsg) emptyMsg.remove();

    const list = document.getElementById('candidateList');
    if (list) {
        const card = document.createElement('div');
        card.className = 'candidate-item-card';
        card.id = `candidate-card-${courseData.subject_code}-${courseData.course_number}`;
        card.setAttribute('data-code', `${courseData.subject_code} ${courseData.course_number}`);
        card.setAttribute('data-dept', courseData.subject_code);
        card.setAttribute('data-num', courseData.course_number);
        card.setAttribute('data-name', courseData.name || '');
        card.setAttribute('data-credits', courseData.credits);
        card.setAttribute('data-sections', courseData.sections_count);

        const safeTitle = (courseData.name || '').replace(/"/g, '&quot;');

        card.innerHTML = `
            <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                <div class="candidate-code">${courseData.subject_code} ${courseData.course_number}</div>
                <div style="display: flex; gap: 6px; align-items: center;">
                    <button type="button" class="btn-details" onclick="openCourseModal('${courseData.subject_code}', '${courseData.course_number}')">Details</button>
                    <button type="button" class="btn-remove-pool" onclick="removeCourseFromPool('${courseData.subject_code}', '${courseData.course_number}')" title="Remove from generator pool">✕</button>
                </div>
            </div>
            <div class="candidate-name" title="${safeTitle}">${safeTitle}</div>
            <div class="candidate-meta">
                <span class="meta-pill pill-blue">${courseData.credits} cr</span>
            </div>
            <select class="section-select-dropdown" id="section-select-${courseData.subject_code}-${courseData.course_number}" 
                    onchange="handleSectionSelectChange(this, '${courseData.subject_code}', '${courseData.course_number}')">
                <option value="" selected disabled>Loading sections...</option>
            </select>
        `;
        list.appendChild(card);
        loadSectionDropdown(courseData.subject_code, courseData.course_number);
    }

    if (btnElement) {
        btnElement.textContent = "Added ✓";
        btnElement.classList.add('added');
        btnElement.disabled = true;
    }
    syncPoolInput();
    updateCatalogSearchButtons();
}

function removeCourseFromPool(dept, num) {
    candidatePool = candidatePool.filter(c => !(c.subject_code === dept && c.course_number === num));
    
    const card = document.getElementById(`candidate-card-${dept}-${num}`);
    if (card) card.remove();

    const list = document.getElementById('candidateList');
    if (candidatePool.length === 0 && list) {
        list.innerHTML = `<div class="empty-placeholder" id="emptyPoolMsg" style="grid-column: 1 / -1;">No eligible courses in generator pool. Add courses below.</div>`;
    }
    syncPoolInput();
    updateCatalogSearchButtons();
}

function clearAllCandidateCourses() {
    if (candidatePool.length === 0) return;

    candidatePool = [];

    const list = document.getElementById('candidateList');
    if (list) {
        list.innerHTML = `<div class="empty-placeholder" id="emptyPoolMsg" style="grid-column: 1 / -1;">No eligible courses in generator pool. Add courses below.</div>`;
    }

    document.querySelectorAll('.btn-add-pool').forEach(btn => {
        btn.textContent = '+ Add';
        btn.classList.remove('added');
        btn.disabled = false;
    });

    syncPoolInput();
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

    const hasLockedLec = lockedForCourse.some(ls => !String(ls.section.section).trim().toUpperCase().endsWith('D'));
    const hasLockedDisc = lockedForCourse.some(ls => String(ls.section.section).trim().toUpperCase().endsWith('D'));

    const hasDiscussionsInCatalog = allSections.some(s => String(s.section).trim().toUpperCase().endsWith('D'));
    const hasLecturesInCatalog = allSections.some(s => !String(s.section).trim().toUpperCase().endsWith('D'));
    const isHybridCourse = hasDiscussionsInCatalog && hasLecturesInCatalog;

    let filteredSections = allSections.map((s, idx) => ({ ...s, originalIndex: idx }));
    let placeholderText = `Select section to lock in (${allSections.length})...`;

    if (isHybridCourse) {
        if (hasLockedLec && !hasLockedDisc) {
            filteredSections = filteredSections.filter(s => String(s.section).trim().toUpperCase().endsWith('D'));
            placeholderText = `Lecture locked. Select Discussion (${filteredSections.length} available)...`;
        } else if (hasLockedDisc && !hasLockedLec) {
            filteredSections = filteredSections.filter(s => !String(s.section).trim().toUpperCase().endsWith('D'));
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
        const daysStr = (s.days && s.days.length) ? s.days.join('') + ' ' : '';
        const tag = String(s.section).trim().toUpperCase().endsWith('D') ? '[Disc] ' : '[Lec] ';
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
        console.error("Failed to parse sections data:", e);
        return;
    }

    const sectionData = sections[parseInt(selectedIdx, 10)];
    if (!sectionData) return;

    const conflict = checkLockedSectionConflict(sectionData, `${dept} ${num}`);
    if (conflict) {
        const timeStr = (sectionData.start_time && sectionData.start_time !== 'TBA') 
            ? `${formatTimeTo12Hour(sectionData.start_time)} - ${formatTimeTo12Hour(sectionData.end_time)}` 
            : 'Asynchronous';
        const daysStr = (sectionData.days && sectionData.days.length) ? sectionData.days.join(', ') : 'Online';

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
        const rawLockedInput = document.getElementById('locked_sections_json');
        if (rawLockedInput && rawLockedInput.value) {
            const parsed = JSON.parse(rawLockedInput.value);
            
            const seenCodes = new Set();
            lockedSections = [];
            for (const item of parsed) {
                const code = String(item.section ? item.section.class_code : '');
                if (code && !seenCodes.has(code)) {
                    seenCodes.add(code);
                    lockedSections.push(item);
                }
            }
            window.lockedSections = lockedSections;
            rawLockedInput.value = JSON.stringify(lockedSections);
        }
    } catch (e) {
        console.error("Failed to parse locked sections JSON:", e);
        lockedSections = [];
        window.lockedSections = [];
    }

    const lockedCountBadge = document.getElementById('lockedCount');
    if (lockedCountBadge) {
        lockedCountBadge.textContent = `${lockedSections.length} Locked`;
    }

    candidatePool = [];
    document.querySelectorAll('#candidateList .candidate-item-card').forEach(card => {
        const dept = card.getAttribute('data-dept');
        const num = card.getAttribute('data-num');
        const name = card.getAttribute('data-name');
        const credits = parseInt(card.getAttribute('data-credits'), 10) || 3;
        const sections_count = parseInt(card.getAttribute('data-sections'), 10) || 0;

        const lockedForCourse = lockedSections.filter(ls => 
            String(ls.course.subject_code).toUpperCase() === String(dept).toUpperCase() && 
            String(ls.course.course_number).toUpperCase() === String(num).toUpperCase()
        );

        if (lockedForCourse.length > 0) {
            const hasLecLocked = lockedForCourse.some(ls => !String(ls.section.section).trim().toUpperCase().endsWith('D'));
            const hasDiscLocked = lockedForCourse.some(ls => String(ls.section.section).trim().toUpperCase().endsWith('D'));

            let rawSecs = [];
            try {
                const sel = card.querySelector('.section-select-dropdown');
                if (sel && sel.dataset.sectionsJson) {
                    rawSecs = JSON.parse(sel.dataset.sectionsJson);
                }
            } catch (e) {}

            const hasDiscInCatalog = rawSecs.some(s => String(s.section).trim().toUpperCase().endsWith('D'));

            if (!hasDiscInCatalog || (hasLecLocked && hasDiscLocked)) {
                card.remove();
                return;
            }
        }

        candidatePool.push({
            subject_code: dept,
            course_number: num,
            name: name,
            credits: credits,
            sections_count: sections_count
        });

        loadSectionDropdown(dept, num);
    });

    window.candidatePool = candidatePool;
    syncPoolInput();
}

// =================================================================
// 4. CUSTOM UNLISTED SECTIONS LOGIC (POPUP MODAL & EDIT MODE)
// =================================================================
let editingCustomClassCode = null;

function openCustomModal(classCodeToEdit = null) {
    const modal = document.getElementById('customCourseModal');
    if (!modal) return;

    if (typeof classCodeToEdit === 'string' && classCodeToEdit.trim().length > 0) {
        editingCustomClassCode = classCodeToEdit.trim();
    } else {
        editingCustomClassCode = null;
    }

    const heading = document.getElementById('customModalHeading');
    const submitBtn = document.getElementById('btnSubmitCustomSection');
    const deptInput = document.getElementById('customDeptInput');
    const numInput = document.getElementById('customNumInput');
    const startInput = document.getElementById('customStartTime');
    const endInput = document.getElementById('customEndTime');

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

    modal.style.display = 'flex';
}

function closeCustomModal(e) {
    if (e && e.target) {
        const modal = document.getElementById('customCourseModal');
        if (e.target !== modal && !e.target.classList.contains('btn-close-modal') && e.target.id !== 'btnCancelCustomCard') {
            return;
        }
    }

    const modal = document.getElementById('customCourseModal');
    if (modal) {
        modal.style.display = 'none';
    }
    editingCustomClassCode = null;
}

function closeCourseModal(e) {
    if (e && e.target) {
        const modal = document.getElementById('courseDetailsModal');
        const isBackdrop = e.target === modal;
        const isCloseBtn = e.target.closest('.btn-close-modal') !== null;

        if (!isBackdrop && !isCloseBtn) {
            return;
        }
    }

    const modal = document.getElementById('courseDetailsModal');
    if (modal) {
        modal.style.display = 'none';
        modal.classList.remove('show');
    }

    activeModalCourse = null;
    currentModalSections = [];
}

function submitCustomSection() {
    const deptInput = document.getElementById('customDeptInput');
    const numInput = document.getElementById('customNumInput');
    const startInput = document.getElementById('customStartTime');
    const endInput = document.getElementById('customEndTime');

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

    const isEditing = typeof editingCustomClassCode === 'string' && editingCustomClassCode.length > 0;
    const classCode = isEditing ? editingCustomClassCode : `CUST-${Date.now()}`;

    const customCourseObj = {
        subject_code: deptVal,
        course_number: numVal,
        name: `Custom Added Course (${deptVal} ${numVal})`,
        credits: 3,
        sections_count: 1,
        is_custom: true
    };

    const customSectionObj = {
        class_code: classCode,
        section: "01",
        days: selectedDays,
        start_time: startTime,
        end_time: endTime,
        is_online: false
    };

    const conflict = checkLockedSectionConflict(customSectionObj, `${deptVal} ${numVal}`, isEditing ? classCode : null);
    if (conflict) {
        alert(
            `⚠️ Time Conflict Detected:\n\n` +
            `Custom Section ${deptVal} ${numVal} (${selectedDays.join(', ')} from ${formatTimeTo12Hour(startTime)} to ${formatTimeTo12Hour(endTime)}) overlaps with an already locked section:\n` +
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

    renderLockedCard(customCourseObj, customSectionObj);
    syncLockedInput();

    closeCustomModal();
}

// =================================================================
// 5. UNIVERSITY CATALOG SEARCH & GEN ED FILTERS (HASH SET OPTIMIZED)
// =================================================================
let searchDebounceTimer = null;
let currentSearchResults = [];
let searchAbortController = null;
let lastSearchQuery = '';

function updateCatalogSearchButtons() {
    const resultsList = document.getElementById('catalogResultsList');
    if (!resultsList) return;

    const poolSet = new Set(
        candidatePool.map(c => `${String(c.subject_code).trim().toUpperCase()} ${String(c.course_number).trim().toUpperCase()}`)
    );
    const lockedSet = new Set(
        lockedSections.map(ls => `${String(ls.course.subject_code).trim().toUpperCase()} ${String(ls.course.course_number).trim().toUpperCase()}`)
    );

    const searchCards = resultsList.querySelectorAll('.candidate-item-card');
    searchCards.forEach(card => {
        const codeElem = card.querySelector('.candidate-code');
        const btn = card.querySelector('.btn-add-pool');
        if (!codeElem || !btn) return;

        const courseCode = codeElem.textContent.trim().toUpperCase();

        if (lockedSet.has(courseCode)) {
            btn.textContent = 'Locked';
            btn.className = 'btn-add-pool added';
            btn.disabled = true;
        } else if (poolSet.has(courseCode)) {
            btn.textContent = 'In Pool';
            btn.className = 'btn-add-pool added';
            btn.disabled = true;
        } else {
            btn.textContent = '+ Add';
            btn.className = 'btn-add-pool';
            btn.disabled = false;
        }
    });
}

function toggleGenEdDropdown(e) {
    if (e) e.stopPropagation();
    const menu = document.getElementById('genEdDropdownMenu');
    if (menu) {
        menu.classList.toggle('show');
    }
}

function getSelectedGenEds() {
    return Array.from(document.querySelectorAll('.gened-checkbox:checked')).map(cb => cb.value);
}

function onGenEdChange() {
    const selected = getSelectedGenEds();
    const countBadge = document.getElementById('genEdSelectedCount');
    if (countBadge) {
        countBadge.textContent = selected.length;
    }
    executeCatalogSearch();
}

function executeCatalogSearch() {
    clearTimeout(searchDebounceTimer);
    const catalogInput = document.getElementById('catalogLiveSearch');
    const resultsList = document.getElementById('catalogResultsList');
    const catalogCountBadge = document.getElementById('catalogResultCount');

    const query = catalogInput ? catalogInput.value.trim() : '';
    const selectedGenEds = getSelectedGenEds();
    const activeTerm = getActiveTerm();

    if (!query && selectedGenEds.length === 0) {
        if (searchAbortController) searchAbortController.abort();
        if (resultsList) {
            resultsList.innerHTML = `<div class="empty-placeholder" style="grid-column: 1 / -1;">Type a course name/number or select Gen Ed categories to view offerings.</div>`;
        }
        if (catalogCountBadge) catalogCountBadge.textContent = "0 Found";
        currentSearchResults = [];
        lastSearchQuery = '';
        return;
    }

    const searchKey = `${query}|${selectedGenEds.join(',')}|${activeTerm}`;
    if (searchKey === lastSearchQuery) return;

    searchDebounceTimer = setTimeout(() => {
        lastSearchQuery = searchKey;

        if (searchAbortController) {
            searchAbortController.abort();
        }
        searchAbortController = new AbortController();

        const params = new URLSearchParams();
        if (query) params.append('q', query);
        if (selectedGenEds.length > 0) params.append('geneds', selectedGenEds.join(','));
        params.append('term', activeTerm);

        fetch(`/api/courses?${params.toString()}`, { signal: searchAbortController.signal })
            .then(res => res.json())
            .then(data => {
                currentSearchResults = data;
                if (catalogCountBadge) {
                    catalogCountBadge.textContent = `${data.length} Found`;
                }

                if (!resultsList) return;

                if (data.length === 0) {
                    resultsList.innerHTML = `<div class="empty-placeholder" style="grid-column: 1 / -1;">No matching courses found in catalog for ${activeTerm}.</div>`;
                    return;
                }

                const poolSet = new Set(
                    candidatePool.map(p => `${String(p.subject_code).trim().toUpperCase()} ${String(p.course_number).trim().toUpperCase()}`)
                );
                const lockedSet = new Set(
                    lockedSections.map(ls => `${String(ls.course.subject_code).trim().toUpperCase()} ${String(ls.course.course_number).trim().toUpperCase()}`)
                );

                resultsList.innerHTML = data.map((c, idx) => {
                    const courseKey = `${String(c.subject_code).trim().toUpperCase()} ${String(c.course_number).trim().toUpperCase()}`;
                    const inPool = poolSet.has(courseKey);
                    const isLocked = lockedSet.has(courseKey);
                    const isAdded = inPool || isLocked;
                    const safeTitle = (c.description || '').replace(/"/g, '&quot;');

                    return `
                        <div class="candidate-item-card">
                            <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                                <div class="candidate-code">${c.code}</div>
                                <div style="display: flex; gap: 6px; align-items: center;">
                                    <button type="button" class="btn-details" onclick="openCourseModal('${c.subject_code}', '${c.course_number}')">Details</button>
                                    <button type="button" class="btn-add-pool ${isAdded ? 'added' : ''}" 
                                            onclick="addCourseByIndex(${idx}, this)" ${isAdded ? 'disabled' : ''}>
                                        ${isAdded ? (isLocked ? 'Locked' : 'In Pool') : '+ Add'}
                                    </button>
                                </div>
                            </div>
                            <div class="candidate-name" style="font-size: 11px; color: #475569; margin: 4px 0;">${safeTitle}</div>
                            <div class="candidate-meta">
                                <span class="meta-pill pill-blue">${c.credits} cr</span>
                                <span class="meta-pill">${c.gen_ed !== 'None' ? c.gen_ed : 'No Gen Ed'}</span>
                                <span>${c.sections_count} sections</span>
                            </div>
                        </div>
                    `;
                }).join('');
            })
            .catch(err => {
                if (err.name !== 'AbortError') {
                    console.error("Error searching courses:", err);
                }
            });
    }, 300);
}

function addCourseByIndex(index, btnElement) {
    const courseData = currentSearchResults[index];
    if (!courseData) return;
    addCourseToPool(courseData, btnElement);
}

// =================================================================
// 6. TIMETABLE VISUALIZER (12-HOUR FORMAT)
// =================================================================
const DAY_MAP = {
    'MO': 'Mo', 'M': 'Mo', 'MON': 'Mo', 'MONDAY': 'Mo',
    'TU': 'Tu', 'T': 'Tu', 'TUE': 'Tu', 'TUESDAY': 'Tu',
    'WE': 'We', 'W': 'We', 'WED': 'We', 'WEDNESDAY': 'We',
    'TH': 'Th', 'R': 'Th', 'THU': 'Th', 'THUR': 'Th', 'THURS': 'Th', 'THURSDAY': 'Th',
    'FR': 'Fr', 'F': 'Fr', 'FRI': 'Fr', 'FRIDAY': 'Fr',
    'SA': 'Sa', 'S': 'Sa', 'SAT': 'Sa', 'SATURDAY': 'Sa',
    'SU': 'Su', 'U': 'Su', 'SUN': 'Su', 'SUNDAY': 'Su'
};

function renderTimetableGrid() {
    const rawSchedule = window.SERVER_SCHEDULE_DATA || [];
    const PIXELS_PER_HOUR = 50;

    if (!Array.isArray(rawSchedule) || rawSchedule.length === 0) return;

    const timedItems = rawSchedule.filter(i => 
        i.start_time && 
        i.end_time && 
        i.start_time !== 'TBA' && 
        i.end_time !== 'TBA' && 
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

    const timeContainer = document.getElementById('timeLabelsContainer');
    if (timeContainer) {
        timeContainer.innerHTML = '';
        for (let h = startHour; h < endHour; h++) {
            const slot = document.createElement('div');
            slot.className = 'time-slot';
            const displayHour = h === 0 ? 12 : (h > 12 ? h - 12 : h);
            const ampm = h < 12 ? 'AM' : 'PM';
            slot.textContent = `${displayHour} ${ampm}`;
            timeContainer.appendChild(slot);
        }
    }

    const gridHeight = totalHours * PIXELS_PER_HOUR;
    ['col-Mo', 'col-Tu', 'col-We', 'col-Th', 'col-Fr', 'col-Sa', 'col-Su'].forEach(colId => {
        const col = document.getElementById(colId);
        if (col) {
            col.style.height = `${gridHeight}px`;
            col.innerHTML = '';
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

            const block = document.createElement('div');
            block.className = 'event-block';
            block.style.top = `${topOffset}px`;
            block.style.height = `${blockHeight}px`;

            const displayName = item.course_id || `${item.course_name} ${item.course_number}`;
            const displayLoc = item.location || (item.is_custom ? 'Custom Event' : 'TBA');

            block.innerHTML = `
                <div class="event-title">${displayName} (Sec ${item.section})</div>
                <div class="event-time">${formattedTimeRange}</div>
                <div class="event-loc">${displayLoc}</div>
            `;

            col.appendChild(block);
        });
    });
}

// =================================================================
// 7. COURSE DETAILS MODAL LOGIC
// =================================================================
function openCourseModal(dept, num, targetSection = null) {
    const modal = document.getElementById('courseDetailsModal');
    if (!modal) return;

    activeModalCourse = { 
        subject_code: String(dept).trim().toUpperCase(), 
        course_number: String(num).trim().toUpperCase() 
    };
    activeModalSectionIndex = 0;

    modal.style.display = 'flex';
    document.getElementById('modalCourseTitle').textContent = `${dept} ${num}`;
    document.getElementById('modalCourseMeta').textContent = 'Loading section details...';
    document.getElementById('modalCourseDesc').textContent = '';
    document.getElementById('modalSectionTabs').innerHTML = '';
    document.getElementById('modalSectionContent').innerHTML = '<div class="empty-placeholder">Fetching sections from catalog...</div>';

    const term = getActiveTerm();
    fetch(`/api/course-sections?name=${encodeURIComponent(dept)}&number=${encodeURIComponent(num)}&term=${encodeURIComponent(term)}`)
        .then(res => res.json())
        .then(data => {
            if (data.error) {
                document.getElementById('modalSectionContent').innerHTML = `<div class="empty-placeholder">${data.error}</div>`;
                return;
            }

            activeModalCourse.name = data.description;
            activeModalCourse.credits = data.sections.length ? data.sections[0].credits : 3;
            activeModalCourse.sections_count = data.sections.length;

            document.getElementById('modalCourseMeta').textContent = `${data.sections.length} Active Section${data.sections.length === 1 ? '' : 's'}`;
            document.getElementById('modalCourseDesc').textContent = data.description;
            document.getElementById('modalCourseGenEd').textContent = `Gen Ed: ${data.gen_ed}`;
            document.getElementById('modalCoursePrereq').textContent = `Prereqs: ${data.prerequisites}`;

            currentModalSections = data.sections;

            const lockBtn = document.getElementById('btnModalLockSection');
            if (data.sections.length === 0) {
                document.getElementById('modalSectionContent').innerHTML = `<div class="empty-placeholder">No active sections offered for ${term}.</div>`;
                if (lockBtn) lockBtn.style.display = 'none';
                return;
            }

            if (lockBtn) lockBtn.style.display = 'inline-block';

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
                if (foundIndex !== -1) {
                    initialIndex = foundIndex;
                }
            }

            activeModalSectionIndex = initialIndex;

            const tabsContainer = document.getElementById('modalSectionTabs');
            tabsContainer.innerHTML = data.sections.map((s, idx) => {
                const isDisc = String(s.section).trim().toUpperCase().endsWith('D');
                const badge = isDisc ? ' [Disc]' : ' [Lec]';
                return `
                    <button type="button" class="section-tab-btn ${idx === initialIndex ? 'active' : ''}" 
                            onclick="switchSectionTab(${idx})">
                        Sec ${s.section}${badge}
                    </button>
                `;
            }).join('');

            renderActiveSectionTab(initialIndex);
        })
        .catch(err => {
            console.error(err);
            document.getElementById('modalSectionContent').innerHTML = '<div class="empty-placeholder">Failed to load section details.</div>';
        });
}

function switchSectionTab(index) {
    activeModalSectionIndex = index;
    document.querySelectorAll('.section-tab-btn').forEach((btn, idx) => {
        btn.classList.toggle('active', idx === index);
    });
    renderActiveSectionTab(index);
}

function renderActiveSectionTab(index) {
    const s = currentModalSections[index];
    if (!s || !activeModalCourse) return;

    const formattedTime = (s.start_time !== 'TBA') ? `${formatTimeTo12Hour(s.start_time)} - ${formatTimeTo12Hour(s.end_time)}` : '';

    const content = document.getElementById('modalSectionContent');
    content.innerHTML = `
        <div class="section-info-grid">
            <div class="info-item">
                <div class="info-label">Class Number / Code</div>
                <div class="info-value">#${s.class_code}</div>
            </div>
            <div class="info-item">
                <div class="info-label">Section & Term</div>
                <div class="info-value">Section ${s.section} (${s.term})</div>
            </div>
            <div class="info-item">
                <div class="info-label">Schedule & Days</div>
                <div class="info-value">${s.days.length ? s.days.join(', ') : 'Online'} ${formattedTime ? `(${formattedTime})` : ''}</div>
            </div>
            <div class="info-item">
                <div class="info-label">Meeting Location</div>
                <div class="info-value">${s.location}</div>
            </div>
            <div class="info-item">
                <div class="info-label">Instructor(s)</div>
                <div class="info-value">${s.instructors.join(', ')}</div>
            </div>
            <div class="info-item">
                <div class="info-label">Credits & Delivery</div>
                <div class="info-value">${s.credits} Credits • ${s.delivery_type}</div>
            </div>
            <div class="info-item" style="grid-column: 1 / -1;">
                <div class="info-label">Enrollment Status</div>
                <div class="info-value">
                    ${s.enrolled} / ${s.maximum_capacity} enrolled 
                    ${s.is_full ? '<span style="color: #dc2626; font-weight: 700; margin-left: 8px;">(Full / Waitlist)</span>' : '<span style="color: #16a34a; font-weight: 700; margin-left: 8px;">(Open)</span>'}
                </div>
            </div>
        </div>
    `;

    const lockBtn = document.getElementById('btnModalLockSection');
    if (!lockBtn) return;

    const targetDept = String(activeModalCourse.subject_code || activeModalCourse.course_name || "").trim().toUpperCase();
    const targetNum = String(activeModalCourse.course_number || "").trim().toUpperCase();

    const isDisc = String(s.section).trim().toUpperCase().endsWith('D');
    const lockedItem = lockedSections.find(ls => {
        if (!ls.course || !ls.section) return false;
        const curDept = String(ls.course.subject_code || ls.course.course_name || "").trim().toUpperCase();
        const curNum = String(ls.course.course_number || "").trim().toUpperCase();
        const curIsDisc = String(ls.section.section).trim().toUpperCase().endsWith('D');
        return curDept === targetDept && curNum === targetNum && curIsDisc === isDisc;
    });

    if (lockedItem) {
        if (String(lockedItem.section.class_code) === String(s.class_code)) {
            lockBtn.textContent = '🔒 Current Active Section';
            lockBtn.disabled = true;
            lockBtn.classList.remove('btn-primary');
            lockBtn.classList.add('btn-secondary');
        } else {
            lockBtn.textContent = `🔄 Switch to Sec ${s.section}`;
            lockBtn.disabled = false;
            lockBtn.classList.remove('btn-secondary');
            lockBtn.classList.add('btn-primary');
        }
    } else {
        lockBtn.textContent = '🔒 Lock This Section';
        lockBtn.disabled = false;
        lockBtn.classList.remove('btn-secondary');
        lockBtn.classList.add('btn-primary');
    }
}

function lockSectionFromModal() {
    if (!activeModalCourse || !currentModalSections[activeModalSectionIndex]) return;

    const newSection = currentModalSections[activeModalSectionIndex];
    const targetDept = String(activeModalCourse.subject_code || activeModalCourse.course_name || "").trim().toUpperCase();
    const targetNum = String(activeModalCourse.course_number || "").trim().toUpperCase();
    const courseCode = `${targetDept} ${targetNum}`;

    // A course can hold one lecture AND one discussion, so only swap with the same kind
    const newIsDisc = String(newSection.section).trim().toUpperCase().endsWith('D');
    const existingIndex = lockedSections.findIndex(ls => {
        if (!ls.course || !ls.section) return false;
        const curDept = String(ls.course.subject_code || ls.course.course_name || "").trim().toUpperCase();
        const curNum = String(ls.course.course_number || "").trim().toUpperCase();
        const curIsDisc = String(ls.section.section).trim().toUpperCase().endsWith('D');
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
        if (oldClassCode) {
            const oldCard = document.getElementById(`locked-card-${oldClassCode}`);
            if (oldCard) oldCard.remove();
        }

        lockedSections[existingIndex].section = newSection;
        renderLockedCard(lockedSections[existingIndex].course, newSection);
        syncLockedInput();
    } else {
        lockSection(targetDept, targetNum, newSection);
    }

    closeCourseModal();
    updateCatalogSearchButtons();
}

function copyClassCode(buttonElement, classCode) {
    if (!classCode) return;
    navigator.clipboard.writeText(classCode).then(() => {
        const originalText = buttonElement.innerHTML;
        buttonElement.innerHTML = `<span>#${classCode}</span> ✓`;
        buttonElement.classList.add('copied');
        
        setTimeout(() => {
            buttonElement.innerHTML = originalText;
            buttonElement.classList.remove('copied');
        }, 1500);
    }).catch(err => {
        console.error('Failed to copy class code:', err);
    });
}

// =================================================================
// 8. BIND GLOBAL INTERFACE TO WINDOW
// =================================================================
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

// =================================================================
// 9. APP INITIALIZATION & EVENT LISTENERS ON DOM LOAD
// =================================================================
document.addEventListener('DOMContentLoaded', () => {
    initCandidatePool();
    renderTimetableGrid();

    const dropZone = document.getElementById('dropZone');
    const fileInput = document.getElementById('auditInput');
    const fileNameDisplay = document.getElementById('fileName');
    const MAX_FILE_SIZE = 100 * 1024;

    function validateFileSize(file) {
        if (file && file.size > MAX_FILE_SIZE) {
            alert("The selected PDF file is larger than 100 KB. Please upload a smaller audit file.");
            if (fileInput) fileInput.value = "";
            if (fileNameDisplay) fileNameDisplay.textContent = "";
            return false;
        }
        return true;
    }

    if (dropZone && fileInput) {
        dropZone.addEventListener('click', () => fileInput.click());

        ['dragenter', 'dragover'].forEach(eventName => {
            dropZone.addEventListener(eventName, (e) => {
                e.preventDefault();
                dropZone.classList.add('dragover');
            });
        });

        ['dragleave', 'drop'].forEach(eventName => {
            dropZone.addEventListener(eventName, (e) => {
                e.preventDefault();
                dropZone.classList.remove('dragover');
            });
        });

        dropZone.addEventListener('drop', (e) => {
            if (e.dataTransfer.files.length > 0) {
                if (validateFileSize(e.dataTransfer.files[0])) {
                    fileInput.files = e.dataTransfer.files;
                    if (fileNameDisplay) {
                        fileNameDisplay.textContent = `Selected: ${e.dataTransfer.files[0].name}`;
                    }
                }
            }
        });
    }

    if (fileInput) {
        fileInput.addEventListener('change', () => {
            if (fileInput.files.length > 0) {
                if (validateFileSize(fileInput.files[0])) {
                    if (fileNameDisplay) {
                        fileNameDisplay.textContent = `Selected: ${fileInput.files[0].name}`;
                    }
                }
            }
        });
    }

    const searchInput = document.getElementById('candidateSearchInput');
    if (searchInput) {
        searchInput.addEventListener('input', function() {
            const query = this.value.trim().toLowerCase();
            const queryCompact = query.replace(/\s+/g, '');
            const cards = document.querySelectorAll('#candidateList .candidate-item-card');
            let visibleCount = 0;

            cards.forEach(card => {
                const rawCode = (card.getAttribute('data-code') || '').toLowerCase();
                const compactCode = rawCode.replace(/\s+/g, '');

                if (!query || rawCode.includes(query) || compactCode.includes(queryCompact)) {
                    card.style.display = 'block';
                    visibleCount++;
                } else {
                    card.style.display = 'none';
                }
            });

            const countBadge = document.getElementById('candidateCount');
            if (countBadge) {
                countBadge.textContent = `${visibleCount} Courses`;
            }
        });
    }

    const catalogInput = document.getElementById('catalogLiveSearch');
    if (catalogInput) {
        catalogInput.addEventListener('input', executeCatalogSearch);
    }

    document.addEventListener('click', (e) => {
        const menu = document.getElementById('genEdDropdownMenu');
        const btn = document.getElementById('genEdDropdownBtn');
        if (menu && menu.classList.contains('show')) {
            if (!menu.contains(e.target) && !btn?.contains(e.target)) {
                menu.classList.remove('show');
            }
        }
    });

    document.getElementById('btnOpenCustomModal')?.addEventListener('click', openCustomModal);
    document.getElementById('btnCloseCustomCard')?.addEventListener('click', closeCustomModal);
    document.getElementById('btnCancelCustomCard')?.addEventListener('click', closeCustomModal);
    document.getElementById('btnSubmitCustomSection')?.addEventListener('click', submitCustomSection);

    const targetCountInput = document.getElementById('target_course_count');
    if (targetCountInput) {
        targetCountInput.addEventListener('input', verifyPoolComplexity);
    }
});