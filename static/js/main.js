// =================================================================
// 1. FILE UPLOAD & DRAG-AND-DROP VALIDATION
// =================================================================
const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('auditInput');
const fileNameDisplay = document.getElementById('fileName');
const MAX_FILE_SIZE = 100 * 1024; // 100 KB upload limit

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

// =================================================================
// 2. CANDIDATE POOL FILTER & UTILITIES
// =================================================================
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

function copyClassCode(buttonElement, classCode) {
    navigator.clipboard.writeText(classCode).then(() => {
        const originalText = buttonElement.innerHTML;
        buttonElement.innerHTML = `<span>#${classCode}</span> ✓`;
        buttonElement.classList.add('copied');
        
        setTimeout(() => {
            buttonElement.innerHTML = originalText;
            buttonElement.classList.remove('copied');
        }, 1500);
    }).catch(err => {
        console.error('Failed to copy class code: ', err);
    });
}

// =================================================================
// 3. TIME SYNCHRONIZATION (SLIDERS & TEXT INPUTS)
// =================================================================
function minutesToTimeStr(totalMinutes) {
    if (totalMinutes >= 1440) return "24:00";
    const h = Math.floor(totalMinutes / 60);
    const m = totalMinutes % 60;
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

function timeStrToMinutes(str) {
    if (!str) return 0;
    const parts = str.trim().split(':').map(Number);
    if (parts.length >= 2) {
        return (parts[0] * 60) + parts[1];
    }
    return 0;
}

function setupTimeSync(sliderId, textId) {
    const slider = document.getElementById(sliderId);
    const text = document.getElementById(textId);
    if (!slider || !text) return;

    slider.value = timeStrToMinutes(text.value);

    slider.addEventListener('input', () => {
        text.value = minutesToTimeStr(parseInt(slider.value, 10));
    });

    text.addEventListener('change', () => {
        const mins = timeStrToMinutes(text.value);
        if (!isNaN(mins) && mins >= 0 && mins <= 1440) {
            slider.value = mins;
        }
    });
}

setupTimeSync('start_slider', 'start_text');
setupTimeSync('end_slider', 'end_text');

// =================================================================
// 4. TIMETABLE VISUALIZER (12-HOUR AM/PM FORMAT)
// =================================================================
const rawSchedule = window.SERVER_SCHEDULE_DATA || [];
const PIXELS_PER_HOUR = 50;

const DAY_MAP = {
    'MO': 'Mo', 'M': 'Mo', 'MON': 'Mo', 'MONDAY': 'Mo',
    'TU': 'Tu', 'T': 'Tu', 'TUE': 'Tu', 'TUESDAY': 'Tu',
    'WE': 'We', 'W': 'We', 'WED': 'We', 'WEDNESDAY': 'We',
    'TH': 'Th', 'R': 'Th', 'THU': 'Th', 'THUR': 'Th', 'THURS': 'Th', 'THURSDAY': 'Th',
    'FR': 'Fr', 'F': 'Fr', 'FRI': 'Fr', 'FRIDAY': 'Fr',
    'SA': 'Sa', 'S': 'Sa', 'SAT': 'Sa', 'SATURDAY': 'Sa',
    'SU': 'Su', 'U': 'Su', 'SUN': 'Su', 'SUNDAY': 'Su'
};

// Convert time strings to clean "h:mm AM/PM" (handles both 24-hr "14:30" and pre-formatted "2:30 PM")
function formatTimeTo12Hour(timeStr) {
    if (!timeStr || timeStr === 'TBA') return '';
    const clean = timeStr.trim();
    
    // If already in 12-hour format, return directly
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

if (Array.isArray(rawSchedule) && rawSchedule.length > 0) {
    const inPersonItems = rawSchedule.filter(i => !i.is_online && i.start_time && i.end_time && i.days && i.days.length > 0);

    if (inPersonItems.length > 0) {
        let minStartMin = 1440;
        let maxEndMin = 0;

        inPersonItems.forEach(item => {
            const s = timeStrToMinutes(item.start_time);
            const e = timeStrToMinutes(item.end_time);
            if (s < minStartMin) minStartMin = s;
            if (e > maxEndMin) maxEndMin = e;
        });

        // 1. Calculate base bounds BEFORE rendering blocks
        const startHour = Math.max(0, Math.floor(minStartMin / 60) - 1);
        const endHour = Math.min(24, Math.ceil(maxEndMin / 60) + 1);
        const totalHours = endHour - startHour;

        // 2. Render Left-Hand Time Axis Labels
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

        // 3. Set Columns Height
        const gridHeight = totalHours * PIXELS_PER_HOUR;
        ['col-Mo', 'col-Tu', 'col-We', 'col-Th', 'col-Fr', 'col-Sa', 'col-Su'].forEach(colId => {
            const col = document.getElementById(colId);
            if (col) {
                col.style.height = `${gridHeight}px`;
            }
        });

        // 4. Render Event Blocks
        inPersonItems.forEach(item => {
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

                block.innerHTML = `
                    <div class="event-title">${item.course_id} (${item.section})</div>
                    <div class="event-time">${formattedTimeRange}</div>
                    <div class="event-loc">${item.location}</div>
                `;

                col.appendChild(block);
            });
        });
    }
}

// =================================================================
// 5. GEN ED MULTI-SELECT DROPDOWN
// =================================================================
function toggleGenEdDropdown(e) {
    e.stopPropagation();
    const menu = document.getElementById('genEdDropdownMenu');
    if (menu) {
        menu.classList.toggle('show');
    }
}

document.addEventListener('click', (e) => {
    const menu = document.getElementById('genEdDropdownMenu');
    const btn = document.getElementById('genEdDropdownBtn');
    if (menu && menu.classList.contains('show')) {
        if (!menu.contains(e.target) && !btn.contains(e.target)) {
            menu.classList.remove('show');
        }
    }
});

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

// =================================================================
// 6. UNIVERSITY CATALOG SEARCH
// =================================================================
let searchDebounceTimer = null;
let currentSearchResults = [];
const catalogInput = document.getElementById('catalogLiveSearch');
const resultsList = document.getElementById('catalogResultsList');
const catalogCountBadge = document.getElementById('catalogResultCount');

function executeCatalogSearch() {
    clearTimeout(searchDebounceTimer);
    const query = catalogInput ? catalogInput.value.trim() : '';
    const selectedGenEds = getSelectedGenEds();

    if (!query && selectedGenEds.length === 0) {
        if (resultsList) {
            resultsList.innerHTML = `<div class="empty-placeholder" style="grid-column: 1 / -1;">Type a course name/number or select Gen Ed categories to view offerings.</div>`;
        }
        if (catalogCountBadge) catalogCountBadge.textContent = "0 Found";
        currentSearchResults = [];
        return;
    }

    searchDebounceTimer = setTimeout(() => {
        const params = new URLSearchParams();
        if (query) params.append('q', query);
        if (selectedGenEds.length > 0) params.append('geneds', selectedGenEds.join(','));

        fetch(`/api/courses?${params.toString()}`)
            .then(res => res.json())
            .then(data => {
                currentSearchResults = data;
                if (catalogCountBadge) {
                    catalogCountBadge.textContent = `${data.length} Found`;
                }

                if (!resultsList) return;

                if (data.length === 0) {
                    resultsList.innerHTML = `<div class="empty-placeholder" style="grid-column: 1 / -1;">No matching courses found in catalog.</div>`;
                    return;
                }

                resultsList.innerHTML = data.map((c, idx) => {
                    const inPool = candidatePool.some(p => p.subject_code === c.subject_code && p.course_number === c.course_number);
                    const isLocked = lockedSections.some(ls => ls.course.subject_code === c.subject_code && ls.course.course_number === c.course_number);
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
            .catch(err => console.error("Error searching courses:", err));
    }, 200);
}

if (catalogInput) {
    catalogInput.addEventListener('input', executeCatalogSearch);
}

function addCourseByIndex(index, btnElement) {
    const courseData = currentSearchResults[index];
    if (!courseData) return;
    addCourseToPool(courseData, btnElement);
}

// =================================================================
// 7. CANDIDATE POOL, SECTION DROPDOWNS & LOCKED SECTIONS STATE
// =================================================================
let candidatePool = [];
let lockedSections = [];
let currentModalSections = [];
let activeModalCourse = null;
let activeModalSectionIndex = 0;

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

function loadSectionDropdown(dept, num) {
    const selectElem = document.getElementById(`section-select-${dept}-${num}`);
    if (!selectElem) return;

    fetch(`/api/course-sections?name=${encodeURIComponent(dept)}&number=${encodeURIComponent(num)}`)
        .then(res => res.json())
        .then(data => {
            if (!data.sections || data.sections.length === 0) {
                selectElem.innerHTML = `<option value="" disabled selected>No active sections</option>`;
                selectElem.disabled = true;
                return;
            }

            let optionsHtml = `<option value="" selected disabled>Select section to lock in (${data.sections.length})...</option>`;
            data.sections.forEach((s, idx) => {
                let timeStr = "Asynchronous / Online";
                if (s.start_time !== "TBA" && s.end_time !== "TBA") {
                    timeStr = `${formatTimeTo12Hour(s.start_time)} - ${formatTimeTo12Hour(s.end_time)}`;
                }
                const daysStr = (s.days && s.days.length) ? s.days.join('') + ' ' : '';
                const label = `Sec ${s.section}: ${daysStr}${timeStr} (#${s.class_code})`;

                optionsHtml += `<option value="${idx}">${label}</option>`;
            });

            selectElem.innerHTML = optionsHtml;
            selectElem.dataset.sectionsJson = JSON.stringify(data.sections);
        })
        .catch(err => {
            console.error("Failed to load sections for dropdown:", err);
            selectElem.innerHTML = `<option value="" disabled selected>Error loading sections</option>`;
        });
}

function handleSectionSelectChange(selectElem, dept, num) {
    const selectedIdx = selectElem.value;
    if (selectedIdx === "" || !selectElem.dataset.sectionsJson) return;

    const sections = JSON.parse(selectElem.dataset.sectionsJson);
    const sectionData = sections[parseInt(selectedIdx, 10)];
    if (!sectionData) return;

    lockSection(dept, num, sectionData);
}

function lockSection(dept, num, sectionData) {
    const courseIdx = candidatePool.findIndex(c => c.subject_code === dept && c.course_number === num);
    let courseData = null;

    if (courseIdx !== -1) {
        courseData = candidatePool[courseIdx];
        candidatePool.splice(courseIdx, 1);
    } else if (activeModalCourse && activeModalCourse.subject_code === dept && activeModalCourse.course_number === num) {
        courseData = activeModalCourse;
    }

    if (!courseData) return;

    const poolCard = document.getElementById(`candidate-card-${dept}-${num}`);
    if (poolCard) poolCard.remove();

    lockedSections.push({
        course: courseData,
        section: sectionData
    });

    renderLockedCard(courseData, sectionData);
    syncPoolInput();
    syncLockedInput();

    const emptyMsg = document.getElementById('emptyPoolMsg');
    if (candidatePool.length === 0 && !emptyMsg) {
        const poolList = document.getElementById('candidateList');
        if (poolList) {
            poolList.innerHTML = `<div class="empty-placeholder" id="emptyPoolMsg" style="grid-column: 1 / -1;">No eligible courses in generator pool. Add courses below.</div>`;
        }
    }
}

function renderLockedCard(course, section) {
    const emptyLocked = document.getElementById('emptyLockedMsg');
    if (emptyLocked) emptyLocked.remove();

    const lockedList = document.getElementById('lockedList');
    if (!lockedList) return;

    const card = document.createElement('div');
    card.className = 'candidate-item-card locked-card';
    card.id = `locked-card-${section.class_code}`;

    const daysStr = (section.days && section.days.length) ? section.days.join(', ') : 'Online';
    const timeStr = (section.start_time !== 'TBA') ? `${formatTimeTo12Hour(section.start_time)} - ${formatTimeTo12Hour(section.end_time)}` : 'Asynchronous';

    card.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: flex-start;">
            <div class="candidate-code">${course.subject_code} ${course.course_number} <span class="locked-badge-pill">Sec ${section.section}</span></div>
            <div style="display: flex; gap: 6px; align-items: center;">
                <button type="button" class="btn-details" onclick="openCourseModal('${course.subject_code}', '${course.course_number}')">Details</button>
                <button type="button" class="btn-remove-pool" onclick="unlockSection('${section.class_code}')" title="Unlock and return to course pool">✕</button>
            </div>
        </div>
        <div class="candidate-name" title="${course.name}">${course.name}</div>
        <div class="candidate-meta" style="margin-top: 4px;">
            <span>${daysStr} (${timeStr})</span>
            <span>#${section.class_code}</span>
        </div>
    `;
    lockedList.appendChild(card);
}

function unlockSection(classCode) {
    const idx = lockedSections.findIndex(ls => ls.section.class_code === classCode);
    if (idx === -1) return;

    const { course } = lockedSections[idx];
    lockedSections.splice(idx, 1);

    const card = document.getElementById(`locked-card-${classCode}`);
    if (card) card.remove();

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

    addCourseToPool(course);
    syncLockedInput();
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
}

function initCandidatePool() {
    // 1. Restore server-persisted locked sections from hidden form input
    try {
        const rawLockedInput = document.getElementById('locked_sections_json');
        if (rawLockedInput && rawLockedInput.value) {
            lockedSections = JSON.parse(rawLockedInput.value);
        }
    } catch (e) {
        console.error("Failed to parse locked sections JSON:", e);
        lockedSections = [];
    }

    // 2. Sync locked badge counter on initial page load
    const lockedCountBadge = document.getElementById('lockedCount');
    if (lockedCountBadge) {
        lockedCountBadge.textContent = `${lockedSections.length} Locked`;
    }

    // 3. Populate candidate pool from active cards (exclude any course already locked)
    candidatePool = [];
    document.querySelectorAll('#candidateList .candidate-item-card').forEach(card => {
        const dept = card.getAttribute('data-dept');
        const num = card.getAttribute('data-num');
        const name = card.getAttribute('data-name');
        const credits = parseInt(card.getAttribute('data-credits'), 10) || 3;
        const sections_count = parseInt(card.getAttribute('data-sections'), 10) || 0;

        const isLocked = lockedSections.some(ls => ls.course.subject_code === dept && ls.course.course_number === num);
        if (isLocked) {
            card.remove();
            return;
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

    syncPoolInput();
}

// =================================================================
// 8. COURSE DETAILS MODAL & MODAL LOCK ACTION
// =================================================================
function openCourseModal(dept, num) {
    const modal = document.getElementById('courseDetailsModal');
    if (!modal) return;

    activeModalCourse = { subject_code: dept, course_number: num };
    activeModalSectionIndex = 0;

    modal.style.display = 'flex';
    document.getElementById('modalCourseTitle').textContent = `${dept} ${num}`;
    document.getElementById('modalCourseMeta').textContent = 'Loading section details...';
    document.getElementById('modalCourseDesc').textContent = '';
    document.getElementById('modalSectionTabs').innerHTML = '';
    document.getElementById('modalSectionContent').innerHTML = '<div class="empty-placeholder">Fetching sections from catalog...</div>';

    fetch(`/api/course-sections?name=${encodeURIComponent(dept)}&number=${encodeURIComponent(num)}`)
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
                document.getElementById('modalSectionContent').innerHTML = '<div class="empty-placeholder">No active sections offered for this term.</div>';
                if (lockBtn) lockBtn.style.display = 'none';
                return;
            }

            if (lockBtn) lockBtn.style.display = 'inline-block';

            const tabsContainer = document.getElementById('modalSectionTabs');
            tabsContainer.innerHTML = data.sections.map((s, idx) => `
                <button type="button" class="section-tab-btn ${idx === 0 ? 'active' : ''}" 
                        onclick="switchSectionTab(${idx})">
                    Sec ${s.section}
                </button>
            `).join('');

            renderActiveSectionTab(0);
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
    if (!s) return;

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
}

function lockSectionFromModal() {
    if (!activeModalCourse || !currentModalSections[activeModalSectionIndex]) return;

    const section = currentModalSections[activeModalSectionIndex];
    lockSection(activeModalCourse.subject_code, activeModalCourse.course_number, section);
    closeCourseModal();
}

function closeCourseModal(e) {
    const modal = document.getElementById('courseDetailsModal');
    if (modal) modal.style.display = 'none';
}

// =================================================================
// 9. APP INITIALIZATION
// =================================================================
initCandidatePool();

const targetCountInput = document.getElementById('target_course_count');
if (targetCountInput) {
    targetCountInput.addEventListener('input', verifyPoolComplexity);
}