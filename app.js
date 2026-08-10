// Core app state
let state = {
  courses: [],
  selectedCourse: null,
  selectedLessonIndex: 0,
  currentFlashcardIndex: 0,
  flashcards: []
};

// DOM Elements
const views = {
  dashboard: document.getElementById('view-dashboard'),
  courseDetail: document.getElementById('view-course-detail'),
  register: document.getElementById('view-register')
};

const menuButtons = {
  dashboard: document.getElementById('btn-dashboard'),
  register: document.getElementById('btn-register')
};

const searchInput = document.getElementById('search-input');
const coursesContainer = document.getElementById('courses-container');

// Stats
const statCoursesCount = document.getElementById('stat-courses-count');
const statLessonsCount = document.getElementById('stat-lessons-count');
const statCardsCount = document.getElementById('stat-cards-count');

// Detail elements
const detailTitle = document.getElementById('detail-title');
const detailDesc = document.getElementById('detail-desc');
const detailLink = document.getElementById('detail-link');
const lessonsList = document.getElementById('lessons-list');
const lessonDetailsContent = document.getElementById('lesson-details-content');
const lessonDetailsPlaceholder = document.getElementById('lesson-details-placeholder');
const lessonTitle = document.getElementById('lesson-title');
const lessonSummary = document.getElementById('lesson-summary');
const lessonTakeaways = document.getElementById('lesson-takeaways');

// Tab elements
const tabButtons = document.querySelectorAll('.tab-btn');
const tabContents = document.querySelectorAll('.tab-content');

// Flashcard elements
const flashcard = document.getElementById('flashcard');
const cardQuestion = document.getElementById('card-question');
const cardAnswer = document.getElementById('card-answer');
const btnPrevCard = document.getElementById('btn-prev-card');
const btnNextCard = document.getElementById('btn-next-card');
const cardIndicator = document.getElementById('card-indicator');

// Register Elements
const registerForm = document.getElementById('register-form');
const courseUrlInput = document.getElementById('course-url');
const courseTypeInput = document.getElementById('course-type');
const instructionsBox = document.getElementById('instructions-box');
const paramUrl = document.getElementById('param-url');
const paramType = document.getElementById('param-type');
const btnTriggerIssue = document.getElementById('btn-trigger-issue');

// Initialize Lucide icons
function initIcons() {
  if (window.lucide) {
    window.lucide.createIcons();
  }
}

// Navigation & Routing
function switchView(viewName) {
  Object.keys(views).forEach(key => {
    if (key === viewName) {
      views[key].classList.add('active');
    } else {
      views[key].classList.remove('active');
    }
  });

  // Update active sidebar button
  Object.keys(menuButtons).forEach(key => {
    if (key === viewName) {
      menuButtons[key].classList.add('active');
    } else {
      menuButtons[key].classList.remove('active');
    }
  });
  
  initIcons();
}

// Attach menu clicks
menuButtons.dashboard.addEventListener('click', () => switchView('dashboard'));
menuButtons.register.addEventListener('click', () => switchView('register'));
document.getElementById('btn-back-to-dashboard').addEventListener('click', () => switchView('dashboard'));

// Fetch Courses data
async function loadCourses() {
  try {
    const response = await fetch('courses.json');
    if (!response.ok) {
      throw new Error("No database found yet.");
    }
    state.courses = await response.json();
    renderStats();
    renderCourses(state.courses);
  } catch (error) {
    console.log("No courses found, rendering default empty layout:", error);
    renderStats();
  }
}

// Render Stats
function renderStats() {
  statCoursesCount.textContent = state.courses.length;
  
  let totalLessons = 0;
  let totalCards = 0;
  
  state.courses.forEach(c => {
    if (c.lessons) {
      totalLessons += c.lessons.length;
      c.lessons.forEach(l => {
        if (l.revise_recall) {
          totalCards += l.revise_recall.length;
        }
      });
    }
  });
  
  statLessonsCount.textContent = totalLessons;
  statCardsCount.textContent = totalCards;
}

// Render Course Cards
function renderCourses(coursesToRender) {
  if (!coursesToRender || coursesToRender.length === 0) {
    coursesContainer.innerHTML = `
      <div class="empty-state glass-card">
        <i data-lucide="inbox"></i>
        <p>No courses registered yet. Click "Register Course" in the sidebar to add your first course!</p>
      </div>
    `;
    initIcons();
    return;
  }
  
  coursesContainer.innerHTML = coursesToRender.map(course => `
    <div class="course-card glass-card" onclick="openCourseDetail('${course.slug}')">
      <div>
        <div class="course-card-header">
          <span class="course-badge ${course.is_specialization ? 'specialization' : ''}">
            ${course.is_specialization ? 'Specialization' : 'Course'}
          </span>
          <i data-lucide="arrow-right-circle" style="color: var(--accent-purple)"></i>
        </div>
        <h3>${course.title}</h3>
        <p>${course.description || 'No description provided.'}</p>
      </div>
      <div class="course-card-footer">
        <span><i data-lucide="video" style="display:inline; width:14px; vertical-align:middle; margin-right:4px;"></i> ${course.lessons ? course.lessons.length : 0} Lessons</span>
        <span>Tracked</span>
      </div>
    </div>
  `).join('');
  
  initIcons();
}

// Open Course Details
window.openCourseDetail = function(slug) {
  const course = state.courses.find(c => c.slug === slug);
  if (!course) return;
  
  state.selectedCourse = course;
  state.selectedLessonIndex = 0;
  
  detailTitle.textContent = course.title;
  detailDesc.textContent = course.description || 'No description provided.';
  detailLink.href = course.url;
  
  // Render Syllabus/Lessons Sidebar
  if (course.lessons && course.lessons.length > 0) {
    lessonsList.innerHTML = course.lessons.map((lesson, idx) => `
      <li>
        <button class="lesson-item-btn ${idx === 0 ? 'active' : ''}" onclick="selectLesson(${idx})">
          <i data-lucide="play-circle" style="width:16px;"></i>
          ${lesson.title}
        </button>
      </li>
    `).join('');
    
    selectLesson(0);
  } else {
    lessonsList.innerHTML = '<li>No lessons summarized yet.</li>';
    lessonDetailsContent.classList.add('hidden');
    lessonDetailsPlaceholder.classList.remove('hidden');
  }
  
  // Compile Flashcards
  state.flashcards = [];
  if (course.lessons) {
    course.lessons.forEach(l => {
      if (l.revise_recall) {
        l.revise_recall.forEach(card => {
          state.flashcards.push({
            lessonTitle: l.title,
            question: card.question,
            answer: card.answer
          });
        });
      }
    });
  }
  
  state.currentFlashcardIndex = 0;
  renderFlashcard();
  
  switchView('course-detail');
};

// Select Lesson Summary
window.selectLesson = function(index) {
  state.selectedLessonIndex = index;
  
  // Update active syllabus button
  const buttons = lessonsList.querySelectorAll('.lesson-item-btn');
  buttons.forEach((btn, idx) => {
    if (idx === index) {
      btn.classList.add('active');
    } else {
      btn.classList.remove('active');
    }
  });
  
  const lesson = state.selectedCourse.lessons[index];
  if (!lesson) return;
  
  lessonTitle.textContent = lesson.title;
  lessonSummary.textContent = lesson.summary;
  
  lessonTakeaways.innerHTML = lesson.key_takeaways.map(t => `
    <li>${t}</li>
  `).join('');
  
  lessonDetailsPlaceholder.classList.add('hidden');
  lessonDetailsContent.classList.remove('hidden');
  initIcons();
};

// Tab Switching
tabButtons.forEach(btn => {
  btn.addEventListener('click', () => {
    tabButtons.forEach(b => b.classList.remove('active'));
    tabContents.forEach(c => c.classList.remove('active'));
    
    btn.classList.add('active');
    const tabId = `tab-${btn.getAttribute('data-tab')}`;
    document.getElementById(tabId).classList.add('active');
  });
});

// Flashcard Controls
function renderFlashcard() {
  if (state.flashcards.length === 0) {
    cardQuestion.textContent = "No flashcards found for this course.";
    cardAnswer.textContent = "Add transcripts containing questions or lessons to build flashcards.";
    cardIndicator.textContent = "0 of 0";
    return;
  }
  
  flashcard.classList.remove('flipped');
  const card = state.flashcards[state.currentFlashcardIndex];
  
  cardQuestion.textContent = card.question;
  cardAnswer.textContent = card.answer;
  cardIndicator.textContent = `Card ${state.currentFlashcardIndex + 1} of ${state.flashcards.length}`;
}

flashcard.addEventListener('click', () => {
  flashcard.classList.toggle('flipped');
});

btnPrevCard.addEventListener('click', (e) => {
  e.stopPropagation();
  if (state.flashcards.length === 0) return;
  state.currentFlashcardIndex = (state.currentFlashcardIndex - 1 + state.flashcards.length) % state.flashcards.length;
  renderFlashcard();
});

btnNextCard.addEventListener('click', (e) => {
  e.stopPropagation();
  if (state.flashcards.length === 0) return;
  state.currentFlashcardIndex = (state.currentFlashcardIndex + 1) % state.flashcards.length;
  renderFlashcard();
});

// Search functionality
searchInput.addEventListener('input', (e) => {
  const query = e.target.value.toLowerCase();
  const filtered = state.courses.filter(course => 
    course.title.toLowerCase().includes(query) || 
    (course.description && course.description.toLowerCase().includes(query))
  );
  renderCourses(filtered);
});

// Registration Form Handler
registerForm.addEventListener('submit', (e) => {
  e.preventDefault();
  const url = courseUrlInput.value.trim();
  const type = courseTypeInput.value;
  
  paramUrl.textContent = url;
  paramType.textContent = type === 'specialization' ? 'Specialization' : 'Single Course';
  
  // Generate GitHub Issue prefilled URL
  const repoUrl = window.location.origin + window.location.pathname; // standard GH Pages path
  // Guess the GitHub repository URL from location
  let githubRepo = "your-username/your-repo-name";
  const pathParts = window.location.pathname.split('/');
  if (window.location.hostname.includes('github.io')) {
    const username = window.location.hostname.split('.')[0];
    const repo = pathParts[1] || '';
    githubRepo = `${username}/${repo}`;
  }
  
  const issueTitle = encodeURIComponent(`[Register]: ${type === 'specialization' ? 'Specialization' : 'Course'}`);
  const issueBody = encodeURIComponent(`Please register the following DeepLearning.AI resource:\n\n**URL**: ${url}\n**Type**: ${type}\n\nOur GitHub Actions workflow will process this request automatically.`);
  
  const githubIssueUrl = `https://github.com/${githubRepo}/issues/new?title=${issueTitle}&body=${issueBody}`;
  btnTriggerIssue.href = githubIssueUrl;
  
  instructionsBox.classList.remove('hidden');
});

// Start app
document.addEventListener('DOMContentLoaded', () => {
  loadCourses();
});
