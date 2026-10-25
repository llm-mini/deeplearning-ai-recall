import React, { useState, useEffect } from 'react';
import {
  BookOpen,
  Compass,
  ExternalLink,
  LogOut,
  PlusCircle,
  Menu,
  X,
  CheckCircle,
  Inbox,
  AlertCircle,
  Trash2,
  FileText,
  Copy,
  ChevronDown,
  ChevronRight,
  Layers,
  GraduationCap,
  Network,
  List
} from 'lucide-react';
import styles from './App.module.css';
import MindMap from './MindMap';

const API_BASE_URL = 'http://localhost:8000';

interface User {
  id: number;
  email: string;
  name: string;
  picture: string;
}

type ItemType = 'catalog' | 'specialization';
type CourseKind = 'short_course' | 'course' | 'specialization';
type CourseStatus = 'scraping' | 'ready' | 'error';

interface Course {
  id: number;
  item_type: ItemType;
  kind: CourseKind;
  title: string;
  slug: string;
  url: string;
  status: CourseStatus;
  total_lessons: number;
  fetched_count: number;
  members_total?: number;
  members_ready?: number;
  current_title?: string | null;
  current_fetched?: number;
  current_total?: number;
  error?: string | null;
  cached?: boolean;
  syllabus: any[];
  transcripts?: { [lessonTitle: string]: string };
}

// Progress is per-course-lesson for catalog items, per-member-course for a
// specialization (so the ring advances monotonically, 1 step per finished course).
function progressOf(c: Course): { fetched: number; total: number; unit: string } {
  if (c.item_type === 'specialization') {
    return { fetched: c.members_ready ?? 0, total: c.members_total ?? 0, unit: 'courses' };
  }
  return { fetched: c.fetched_count, total: c.total_lessons, unit: 'lessons' };
}

const SECTION_META: { kind: CourseKind; label: string; Icon: typeof BookOpen }[] = [
  { kind: 'short_course', label: 'Short Courses', Icon: BookOpen },
  { kind: 'course', label: 'Courses', Icon: GraduationCap },
  { kind: 'specialization', label: 'Specializations', Icon: Layers },
];

const courseKey = (c: { item_type: ItemType; id: number }) => `${c.item_type}:${c.id}`;

// Small SVG donut showing scrape progress (fetched / total).
function ProgressRing({ fetched, total, size = 36 }: { fetched: number; total: number; size?: number }) {
  const stroke = 3;
  const r = (size - stroke) / 2;
  const circ = 2 * Math.PI * r;
  const pct = total > 0 ? Math.min(1, fetched / total) : 0;
  return (
    <div style={{ position: 'relative', width: size, height: size, flexShrink: 0 }} title={`${fetched}/${total} fetched`}>
      <svg width={size} height={size} style={{ transform: 'rotate(-90deg)' }}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth={stroke} />
        <circle
          cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#ff6b00" strokeWidth={stroke}
          strokeDasharray={circ} strokeDashoffset={circ * (1 - pct)} strokeLinecap="round"
          style={{ transition: 'stroke-dashoffset 0.6s ease' }}
        />
      </svg>
      <span style={{
        position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: size > 30 ? '0.55rem' : '0.5rem', fontWeight: 700, color: '#ff6b00'
      }}>
        {total > 0 ? `${Math.round(pct * 100)}%` : '…'}
      </span>
    </div>
  );
}

function App() {
  const [user, setUser] = useState<User | null>(null);
  const [courses, setCourses] = useState<Course[]>([]);
  const [selectedCourse, setSelectedCourse] = useState<Course | null>(null);
  const [activeCourseTab, setActiveCourseTab] = useState<string>('');
  const [activeModuleIdx, setActiveModuleIdx] = useState<number>(0);
  const [activeTranscript, setActiveTranscript] = useState<{ lessonTitle: string, text: string } | null>(null);
  const [detailView, setDetailView] = useState<'outline' | 'mindmap'>('outline');
  const [urlInput, setUrlInput] = useState('');
  const [courseType, setCourseType] = useState('short_course');
  
  const [isInitializing, setIsInitializing] = useState(true);
  const [isRegistering, setIsRegistering] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  const [successMsg, setSuccessMsg] = useState('');
  
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [openSections, setOpenSections] = useState<Record<CourseKind, boolean>>({
    short_course: false,
    course: false,
    specialization: false,
  });

  useEffect(() => {
    if (!selectedCourse || selectedCourse.item_type !== 'specialization') return;
    const syl = selectedCourse.syllabus;
    if (!syl || syl.length === 0 || typeof syl[0] !== 'object') return;
    const titles = Array.from(new Set(syl.map((m: any) => {
      const parts = (m.title || '').split(' - ');
      return parts.length > 1 ? parts[0] : 'General';
    })));
    // Keep the user's current tab if still valid; only (re)set when missing.
    // Avoids polling (which replaces selectedCourse) snapping back to course 1.
    setActiveCourseTab(prev => (prev && titles.includes(prev)) ? prev : (titles[0] as string || ''));
  }, [selectedCourse]);

  useEffect(() => {
    if (selectedCourse) {
      const updated = courses.find(c => courseKey(c) === courseKey(selectedCourse));
      if (updated) {
        setSelectedCourse(updated);
      }
    }
  }, [courses]);

  // Reset the selected module when switching course / course-tab.
  useEffect(() => {
    setActiveModuleIdx(0);
  }, [activeCourseTab, selectedCourse?.id, selectedCourse?.item_type]);

  // Reset the detail view (outline/mind map) when opening a different course.
  useEffect(() => {
    setDetailView('outline');
  }, [selectedCourse?.id, selectedCourse?.item_type]);

  // Fetch current user details on mount
  useEffect(() => {
    fetch(`${API_BASE_URL}/api/auth/me`, { credentials: 'include' })
      .then(res => {
        if (!res.ok) throw new Error('Not logged in');
        return res.json();
      })
      .then(data => {
        setUser(data);
        fetchCourses();
      })
      .catch(() => {
        setUser(null);
      })
      .finally(() => {
        setIsInitializing(false);
      });
  }, []);

  // Auto-dismiss toast messages so stale text (e.g. "Fetching…") doesn't linger.
  useEffect(() => {
    if (!successMsg && !errorMsg) return;
    const t = setTimeout(() => { setSuccessMsg(''); setErrorMsg(''); }, 6000);
    return () => clearTimeout(t);
  }, [successMsg, errorMsg]);

  // Poll for background scrape progress. Poll fast while anything is scraping,
  // then back off to a slow idle poll.
  const anyScraping = courses.some(c => c.status === 'scraping');
  useEffect(() => {
    if (!user) return;
    const interval = setInterval(() => {
      fetchCourses();
    }, anyScraping ? 2500 : 10000);
    return () => clearInterval(interval);
  }, [user, anyScraping]);

  const fetchCourses = () => {
    fetch(`${API_BASE_URL}/api/courses`, { credentials: 'include' })
      .then(res => {
        if (!res.ok) throw new Error('Failed to load courses');
        return res.json();
      })
      .then(data => {
        setCourses(data);
      })
      .catch(err => {
        console.error('Error fetching courses:', err);
      });
  };

  const handleRegister = (e: React.FormEvent) => {
    e.preventDefault();
    if (!urlInput.trim()) return;

    setErrorMsg('');
    setSuccessMsg('');
    setIsRegistering(true);

    fetch(`${API_BASE_URL}/api/register`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      credentials: 'include',
      body: JSON.stringify({ url: urlInput, type: courseType }),
    })
      .then(async res => {
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data.detail || 'Could not register course');
        }
        return data;
      })
      .then(newCourse => {
        if (newCourse.cached) {
          setSuccessMsg(`"${newCourse.title}" is already available — loaded from library.`);
        } else {
          setSuccessMsg(`Registered "${newCourse.title}". Fetching transcripts in the background…`);
        }
        setOpenSections(prev => ({ ...prev, [newCourse.kind as CourseKind]: true }));
        setUrlInput('');
        fetchCourses();
      })
      .catch(err => {
        setErrorMsg(err.message || 'Scraping failed. Make sure URL is correct.');
      })
      .finally(() => {
        setIsRegistering(false);
      });
  };

  const handleDeleteCourse = (course: Course, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!window.confirm("Remove this course from your dashboard?")) return;

    fetch(`${API_BASE_URL}/api/courses/${course.item_type}/${course.id}`, {
      method: 'DELETE',
      credentials: 'include',
    })
      .then(async res => {
        if (!res.ok) {
          const data = await res.json();
          throw new Error(data.detail || 'Could not remove course');
        }
        setSuccessMsg('Course removed from your dashboard.');
        if (selectedCourse && courseKey(selectedCourse) === courseKey(course)) {
          setSelectedCourse(null);
        }
        fetchCourses();
      })
      .catch(err => {
        setErrorMsg(err.message || 'Failed to remove course');
      });
  };

  const openCourse = (course: Course) => {
    if (course.status !== 'ready') return; // gate: can't open until fetched
    setSelectedCourse(course);
    setMobileMenuOpen(false);
  };

  const handleLogout = () => {
    fetch(`${API_BASE_URL}/api/auth/logout`, { method: 'POST', credentials: 'include' })
      .then(() => {
        setUser(null);
        setCourses([]);
        setSelectedCourse(null);
      });
  };

  const handleLoginClick = () => {
    window.location.href = `${API_BASE_URL}/api/auth/login`;
  };

  if (isInitializing) {
    return (
      <div className={styles.loginView}>
        <div className={styles.loginCard}>
          <div className={styles.spinner} style={{ margin: '0 auto 1.5rem auto', width: '30px', height: '30px' }} />
          <h1>Loading Dashboard...</h1>
          <p>Please wait while we initialize your workspace</p>
        </div>
      </div>
    );
  }

  // Not Authenticated: Render Login Page
  if (!user) {
    return (
      <div className={styles.loginView}>
        <div className={styles.loginCard}>
          <div className={styles.logoIcon}>
            <Compass size={40} />
          </div>
          <h1>DeepLearning.AI Recall</h1>
          <p>Synchronize your courses, track topics & revise concepts efficiently.</p>
          
          <button onClick={handleLoginClick} className={styles.googleBtn}>
            <svg width="18" height="18" viewBox="0 0 18 18">
              <path fill="#4285F4" d="M17.64 9.2c0-.63-.06-1.25-.16-1.84H9v3.47h4.84c-.21 1.12-.84 2.07-1.79 2.7v2.24h2.9c1.7-1.57 2.69-3.88 2.69-6.57z" />
              <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.9-2.24c-.8.54-1.84.87-3.06.87-2.35 0-4.33-1.59-5.05-3.73H.95v2.3C2.43 15.98 5.48 18 9 18z" />
              <path fill="#FBBC05" d="M3.95 10.72A5.4 5.4 0 0 1 3.6 9c0-.6.1-1.18.27-1.72V4.98H.95A8.99 8.99 0 0 0 0 9c0 1.45.35 2.82.95 4.02l3-2.3z" />
              <path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58C13.46.8 11.43 0 9 0 5.48 0 2.43 2.02.95 4.98l3 2.3C4.67 5.17 6.65 3.58 9 3.58z" />
            </svg>
            Continue with Google
          </button>
        </div>
      </div>
    );
  }

  // Calculate stats
  const registeredCount = courses.length;
  const topicsCount = courses.reduce((acc, c) => acc + (c.syllabus?.length || 0), 0);

  return (
    <div className={styles.appContainer}>
      {/* Sidebar Navigation */}
      <aside className={styles.sidebar}>
        <div className={styles.sidebarHeader}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <Compass size={24} style={{ color: '#ff6b00' }} />
            <span className={styles.sidebarLogoText}>DL.AI<span>Recall</span></span>
          </div>
          <button className={styles.menuToggle} onClick={() => setMobileMenuOpen(!mobileMenuOpen)}>
            {mobileMenuOpen ? <X size={20} /> : <Menu size={20} />}
          </button>
        </div>

        <nav className={`${styles.navMenu} ${mobileMenuOpen ? styles.open : ''}`}>
          <button
            className={`${styles.navBtn} ${!selectedCourse ? styles.navBtnActive : ''}`}
            onClick={() => { setSelectedCourse(null); setMobileMenuOpen(false); }}
          >
            <BookOpen size={18} /> Dashboard
          </button>

          {SECTION_META.map(({ kind, label, Icon }) => {
            const group = courses.filter(c => c.kind === kind);
            if (group.length === 0) return null;
            const isOpen = openSections[kind];
            return (
              <div key={kind} style={{ marginTop: '0.5rem' }}>
                <button
                  className={styles.navBtn}
                  onClick={() => setOpenSections(prev => ({ ...prev, [kind]: !prev[kind] }))}
                  style={{ fontWeight: 700, color: '#a1a1aa' }}
                >
                  {isOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                  <Icon size={16} style={{ minWidth: '16px' }} />
                  <span style={{ flex: 1, textAlign: 'left' }}>{label}</span>
                  <span style={{ fontSize: '0.75rem', color: '#71717a' }}>{group.length}</span>
                </button>
                {isOpen && group.map(course => {
                  const isReady = course.status === 'ready';
                  return (
                    <button
                      key={courseKey(course)}
                      className={`${styles.navBtn} ${selectedCourse && courseKey(selectedCourse) === courseKey(course) ? styles.navBtnActive : ''}`}
                      onClick={() => openCourse(course)}
                      style={{ paddingLeft: '1.75rem', opacity: isReady ? 1 : 0.7, cursor: isReady ? 'pointer' : 'default' }}
                      title={isReady ? course.title : `${course.title} — fetching…`}
                    >
                      {course.status === 'scraping' ? (
                        <ProgressRing fetched={progressOf(course).fetched} total={progressOf(course).total} size={18} />
                      ) : course.status === 'error' ? (
                        <AlertCircle size={14} style={{ minWidth: '14px', color: '#ef4444' }} />
                      ) : (
                        <CheckCircle size={14} style={{ minWidth: '14px' }} />
                      )}
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{course.title}</span>
                    </button>
                  );
                })}
              </div>
            );
          })}
        </nav>

        <div className={`${styles.sidebarFooter} ${mobileMenuOpen ? styles.open : ''}`}>
          <div className={styles.userInfo}>
            <img src={user.picture} alt={user.name} className={styles.avatar} />
            <div className={styles.userName} title={user.name}>{user.name}</div>
          </div>
          <button onClick={handleLogout} className={styles.logoutBtn} title="Sign Out">
            <LogOut size={16} />
          </button>
        </div>
      </aside>

      {/* Main Content Area */}
      <main className={styles.mainContent}>
        <header className={styles.topBar}>
          <div className={styles.pageTitle}>
            {selectedCourse ? `Courses / ${selectedCourse.title}` : 'Student Workspace'}
          </div>
          <div className={styles.userInfo} style={{ display: 'flex' }}>
            <span style={{ fontSize: '0.85rem', color: '#71717a' }}>{user.email}</span>
          </div>
        </header>

        <div className={styles.contentBody}>
          {/* VIEW: Course Details */}
          {selectedCourse ? (
            <div>
              <button className={styles.backBtn} onClick={() => setSelectedCourse(null)}>
                &larr; Back to Dashboard
              </button>
              
              <div className={styles.detailHeader}>
                <h1>{selectedCourse.title}</h1>
                <div style={{ display: 'flex', gap: '1.5rem', alignItems: 'center', marginTop: '1rem' }}>
                  <a href={selectedCourse.url} target="_blank" rel="noopener noreferrer" className={styles.externalLink}>
                    Open Original Course Link <ExternalLink size={16} />
                  </a>
                  <button
                    onClick={(e) => handleDeleteCourse(selectedCourse, e)}
                    style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5rem', color: '#ef4444', backgroundColor: 'transparent', border: 'none', cursor: 'pointer', fontWeight: 600, fontSize: '0.9rem' }}
                  >
                    Remove Course <Trash2 size={16} />
                  </button>
                </div>
              </div>

              {/* Outline vs Mind Map toggle */}
              <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1.25rem' }}>
                {([['outline', 'Outline', List], ['mindmap', 'Mind Map', Network]] as const).map(([key, label, Icon]) => {
                  const active = detailView === key;
                  return (
                    <button
                      key={key}
                      onClick={() => setDetailView(key)}
                      style={{
                        display: 'inline-flex', alignItems: 'center', gap: '0.4rem',
                        padding: '0.5rem 1rem', borderRadius: '8px', cursor: 'pointer',
                        fontWeight: 600, fontSize: '0.85rem', transition: 'all 0.2s',
                        border: active ? '1px solid rgba(255,107,0,0.5)' : '1px solid rgba(255,255,255,0.08)',
                        backgroundColor: active ? 'rgba(255,107,0,0.12)' : 'rgba(255,255,255,0.02)',
                        color: active ? '#ff6b00' : '#a1a1aa',
                      }}
                    >
                      <Icon size={15} /> {label}
                    </button>
                  );
                })}
              </div>

              {detailView === 'mindmap' ? (
                selectedCourse.kind === 'short_course' ? (
                  <MindMap catalogId={selectedCourse.id} />
                ) : (
                  <div className={styles.sectionBox} style={{ textAlign: 'center', padding: '3rem 1.5rem', color: '#a1a1aa' }}>
                    <Network size={36} style={{ color: '#ff6b00', marginBottom: '1rem', opacity: 0.8 }} />
                    <p style={{ margin: 0, fontSize: '1rem', fontWeight: 600 }}>Mind maps start with short courses</p>
                    <p style={{ margin: '0.25rem 0 0 0', fontSize: '0.85rem', color: '#71717a' }}>Support for full courses and specializations is coming next.</p>
                  </div>
                )
              ) : (
              <div className={styles.detailBody}>
                <div className={styles.sectionBox}>
                  <h2>
                    {selectedCourse.syllabus && selectedCourse.syllabus.length > 0 && typeof selectedCourse.syllabus[0] === 'object' 
                      ? `Course Outline (${selectedCourse.syllabus.length} Modules)` 
                      : selectedCourse.syllabus && selectedCourse.syllabus[0] === 'Upcoming...'
                      ? 'Course Outline'
                      : `Syllabus Outline (${selectedCourse.syllabus?.length || 0} Lessons)`}
                  </h2>
                  
                  <div>
                    {(() => {
                      const isSpecialization = selectedCourse.item_type === 'specialization';
                      let coursesMap: { [courseTitle: string]: any[] } = {};
                      let courseTitles: string[] = [];

                      if (isSpecialization && selectedCourse.syllabus && selectedCourse.syllabus.length > 0 && typeof selectedCourse.syllabus[0] === 'object') {
                        (selectedCourse.syllabus as any[]).forEach(module => {
                          const parts = module.title.split(' - ');
                          const courseTitle = parts.length > 1 ? parts[0] : 'General';
                          const displayModuleTitle = parts.length > 1 ? parts.slice(1).join(' - ') : module.title;
                          
                          if (!coursesMap[courseTitle]) {
                            coursesMap[courseTitle] = [];
                            courseTitles.push(courseTitle);
                          }
                          coursesMap[courseTitle].push({
                            ...module,
                            displayTitle: displayModuleTitle
                          });
                        });
                      }

                      return (
                        <div>
                          {isSpecialization && courseTitles.length > 0 && (
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', borderBottom: '1px solid rgba(255, 255, 255, 0.05)', paddingBottom: '0.75rem', marginBottom: '1.5rem', maxWidth: '100%' }}>
                              {courseTitles.map((title, cIdx) => {
                                const active = activeCourseTab === title;
                                return (
                                <button
                                  key={title}
                                  onClick={() => setActiveCourseTab(title)}
                                  style={{
                                    padding: '0.55rem 1rem',
                                    borderRadius: '24px',
                                    border: 'none',
                                    backgroundColor: active ? '#ff6b00' : 'rgba(255, 255, 255, 0.05)',
                                    color: active ? '#ffffff' : '#a1a1aa',
                                    cursor: 'pointer',
                                    fontWeight: 600,
                                    fontSize: '0.85rem',
                                    transition: 'all 0.2s ease',
                                    outline: 'none',
                                    display: 'inline-flex',
                                    alignItems: 'center',
                                    gap: '0.5rem',
                                    textAlign: 'left'
                                  }}
                                >
                                  <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', minWidth: '20px', height: '20px', borderRadius: '50%', fontSize: '0.72rem', fontWeight: 700, flexShrink: 0, backgroundColor: active ? 'rgba(255,255,255,0.25)' : 'rgba(255, 107, 0, 0.15)', color: active ? '#ffffff' : '#ff6b00' }}>{cIdx + 1}</span>
                                  {title}
                                </button>
                                );
                              })}
                            </div>
                          )}

                          {selectedCourse.syllabus && selectedCourse.syllabus.length > 0 && typeof selectedCourse.syllabus[0] === 'object' ? (
                            (() => {
                              const modules = isSpecialization ? (coursesMap[activeCourseTab] || []) : (selectedCourse.syllabus as any[]);
                              const safeIdx = modules.length ? Math.min(activeModuleIdx, modules.length - 1) : 0;
                              const currentModule = modules[safeIdx];
                              return (
                                <div>
                                  {modules.length > 1 && (
                                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', marginBottom: '1.5rem' }}>
                                      {modules.map((m, i) => {
                                        const active = i === safeIdx;
                                        return (
                                          <button
                                            key={i}
                                            onClick={() => setActiveModuleIdx(i)}
                                            style={{
                                              padding: '0.45rem 0.9rem',
                                              borderRadius: '8px',
                                              border: active ? '1px solid rgba(255, 107, 0, 0.5)' : '1px solid rgba(255, 255, 255, 0.08)',
                                              backgroundColor: active ? 'rgba(255, 107, 0, 0.12)' : 'rgba(255, 255, 255, 0.02)',
                                              color: active ? '#ff6b00' : '#a1a1aa',
                                              cursor: 'pointer',
                                              fontWeight: 600,
                                              fontSize: '0.8rem',
                                              transition: 'all 0.2s ease',
                                              outline: 'none'
                                            }}
                                          >
                                            {isSpecialization ? m.displayTitle : m.title}
                                          </button>
                                        );
                                      })}
                                    </div>
                                  )}

                                  {currentModule && (
                                <div key={safeIdx} style={{ border: '1px solid rgba(255, 255, 255, 0.05)', borderRadius: '8px', padding: '1.5rem', backgroundColor: 'rgba(255, 255, 255, 0.01)' }}>
                                  <h3 style={{ fontSize: '1.1rem', fontWeight: 700, color: '#ff6b00', marginBottom: '1.25rem', marginTop: 0 }}>
                                    {isSpecialization ? currentModule.displayTitle : currentModule.title}
                                  </h3>
                                  <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
                                    {currentModule.topics && currentModule.topics.map((topic: any, tIdx: number) => {
                                      const isSingleRedundantLesson = 
                                        topic.lessons && 
                                        topic.lessons.length === 1 && 
                                        (topic.lessons[0].toLowerCase().trim() === topic.title.toLowerCase().trim() || 
                                         topic.title.toLowerCase().trim().includes(topic.lessons[0].toLowerCase().trim()) ||
                                         topic.lessons[0].toLowerCase().trim().includes(topic.title.toLowerCase().trim()));

                                      if (isSingleRedundantLesson) {
                                        const lessonName = topic.lessons[0];
                                        const hasTranscript = selectedCourse.transcripts && selectedCourse.transcripts[lessonName];
                                        return (
                                          <div key={tIdx} className={styles.syllabusItem} style={{ paddingLeft: '0.5rem', minHeight: 'auto', padding: '0.4rem 0', display: 'flex', alignItems: 'center', width: '100%' }}>
                                            <div className={styles.itemNum} style={{ fontSize: '0.75rem', width: '18px', height: '18px', minWidth: '18px', backgroundColor: 'rgba(255, 107, 0, 0.1)', color: '#ff6b00' }}>{tIdx + 1}</div>
                                            <div style={{ fontSize: '0.9rem', color: '#ffffff', fontWeight: 500 }}>{topic.title}</div>
                                            {hasTranscript && (
                                              <button
                                                onClick={() => setActiveTranscript({ lessonTitle: lessonName, text: selectedCourse.transcripts![lessonName] })}
                                                style={{
                                                  marginLeft: 'auto',
                                                  display: 'inline-flex',
                                                  alignItems: 'center',
                                                  gap: '0.25rem',
                                                  backgroundColor: 'rgba(255, 107, 0, 0.1)',
                                                  border: '1px solid rgba(255, 107, 0, 0.2)',
                                                  borderRadius: '4px',
                                                  color: '#ff6b00',
                                                  padding: '0.2rem 0.5rem',
                                                  fontSize: '0.75rem',
                                                  fontWeight: 600,
                                                  cursor: 'pointer',
                                                  transition: 'all 0.2s'
                                                }}
                                                title="View Captured Transcript"
                                              >
                                                <FileText size={12} />
                                                <span>Transcript</span>
                                              </button>
                                            )}
                                          </div>
                                        );
                                      }

                                      return (
                                        <div key={tIdx} style={{ paddingLeft: '0.75rem', borderLeft: '2px solid rgba(255, 107, 0, 0.2)' }}>
                                          <h4 style={{ fontSize: '0.95rem', fontWeight: 600, color: '#ffffff', margin: '0 0 0.5rem 0' }}>
                                            {topic.title}
                                          </h4>
                                          <div className={styles.syllabusList}>
                                            {topic.lessons && topic.lessons.map((lesson: string, lIdx: number) => {
                                              const hasTranscript = selectedCourse.transcripts && selectedCourse.transcripts[lesson];
                                              return (
                                                <div key={lIdx} className={styles.syllabusItem} style={{ paddingLeft: '0.5rem', minHeight: 'auto', padding: '0.4rem 0', display: 'flex', alignItems: 'center', width: '100%' }}>
                                                  <div className={styles.itemNum} style={{ fontSize: '0.75rem', width: '18px', height: '18px', minWidth: '18px' }}>{lIdx + 1}</div>
                                                  <div style={{ fontSize: '0.85rem', color: '#a1a1aa' }}>{lesson}</div>
                                                  {hasTranscript && (
                                                    <button
                                                      onClick={() => setActiveTranscript({ lessonTitle: lesson, text: selectedCourse.transcripts![lesson] })}
                                                      style={{
                                                        marginLeft: 'auto',
                                                        display: 'inline-flex',
                                                        alignItems: 'center',
                                                        gap: '0.25rem',
                                                        backgroundColor: 'rgba(255, 107, 0, 0.1)',
                                                        border: '1px solid rgba(255, 107, 0, 0.2)',
                                                        borderRadius: '4px',
                                                        color: '#ff6b00',
                                                        padding: '0.2rem 0.5rem',
                                                        fontSize: '0.75rem',
                                                        fontWeight: 600,
                                                        cursor: 'pointer',
                                                        transition: 'all 0.2s'
                                                      }}
                                                      title="View Captured Transcript"
                                                    >
                                                      <FileText size={12} />
                                                      <span>Transcript</span>
                                                    </button>
                                                  )}
                                                </div>
                                              );
                                            })}
                                          </div>
                                        </div>
                                      );
                                    })}
                                  </div>
                                </div>
                                  )}
                                </div>
                              );
                            })()
                          ) : selectedCourse.syllabus && selectedCourse.syllabus[0] === 'Upcoming...' ? (
                            <div style={{ textAlign: 'center', padding: '3rem 1.5rem', color: '#a1a1aa' }}>
                              <Compass size={36} style={{ color: '#ff6b00', marginBottom: '1rem', opacity: 0.8 }} />
                              <p style={{ margin: 0, fontSize: '1rem', fontWeight: 600 }}>Specialization tracker is upcoming...</p>
                              <p style={{ margin: '0.25rem 0 0 0', fontSize: '0.85rem', color: '#71717a' }}>Weekly module breakdowns and tracking will be supported soon.</p>
                            </div>
                          ) : (
                            <div className={styles.syllabusList}>
                              {(selectedCourse.syllabus as string[]).map((lesson, idx) => {
                                const hasTranscript = selectedCourse.transcripts && selectedCourse.transcripts[lesson];
                                return (
                                  <div key={idx} className={styles.syllabusItem} style={{ display: 'flex', alignItems: 'center', width: '100%' }}>
                                    <div className={styles.itemNum}>{idx + 1}</div>
                                    <div style={{ fontSize: '0.9rem', color: '#ffffff' }}>{lesson}</div>
                                    {hasTranscript && (
                                      <button
                                        onClick={() => setActiveTranscript({ lessonTitle: lesson, text: selectedCourse.transcripts![lesson] })}
                                        style={{
                                          marginLeft: 'auto',
                                          display: 'inline-flex',
                                          alignItems: 'center',
                                          gap: '0.25rem',
                                          backgroundColor: 'rgba(255, 107, 0, 0.1)',
                                          border: '1px solid rgba(255, 107, 0, 0.2)',
                                          borderRadius: '4px',
                                          color: '#ff6b00',
                                          padding: '0.2rem 0.5rem',
                                          fontSize: '0.75rem',
                                          fontWeight: 600,
                                          cursor: 'pointer',
                                          transition: 'all 0.2s'
                                        }}
                                        title="View Captured Transcript"
                                      >
                                        <FileText size={12} />
                                        <span>Transcript</span>
                                      </button>
                                    )}
                                  </div>
                                );
                              })}
                            </div>
                          )}
                        </div>
                      );
                    })()}
                  </div>
                </div>
              </div>
              )}
            </div>
          ) : (
            /* VIEW: Dashboard */
            <div>
              {/* Stats Bar */}
              <div className={styles.statsGrid}>
                <div className={styles.statCard}>
                  <div className={`${styles.statIcon} ${styles.orange}`}>
                    <BookOpen size={24} />
                  </div>
                  <div>
                    <h3>{registeredCount}</h3>
                    <p>Registered Courses</p>
                  </div>
                </div>
                <div className={styles.statCard}>
                  <div className={`${styles.statIcon} ${styles.purple}`}>
                    <Compass size={24} />
                  </div>
                  <div>
                    <h3>{topicsCount}</h3>
                    <p>Tracked Lessons</p>
                  </div>
                </div>
              </div>

              {/* Course Registration Area */}
              <div className={styles.registerSection}>
                <h2>Register New Course</h2>
                <p>Register a DeepLearning.AI course by pasting its URL. We will extract its layout and syllabus.</p>
                
                {errorMsg && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: '#ef4444', backgroundColor: 'rgba(239, 68, 68, 0.08)', padding: '0.75rem 1rem', borderRadius: '8px', marginBottom: '1.5rem', fontSize: '0.9rem' }}>
                    <AlertCircle size={16} /> {errorMsg}
                  </div>
                )}
                {successMsg && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: '#22c55e', backgroundColor: 'rgba(34, 197, 94, 0.08)', padding: '0.75rem 1rem', borderRadius: '8px', marginBottom: '1.5rem', fontSize: '0.9rem' }}>
                    <CheckCircle size={16} /> {successMsg}
                  </div>
                )}

                <form onSubmit={handleRegister} className={styles.inputGroup}>
                  <input
                    type="url"
                    placeholder="https://learn.deeplearning.ai/courses/ai-agents-in-langgraph"
                    required
                    value={urlInput}
                    onChange={(e) => setUrlInput(e.target.value)}
                    className={styles.urlInput}
                    disabled={isRegistering}
                  />
                  <select
                    value={courseType}
                    onChange={(e) => setCourseType(e.target.value)}
                    style={{
                      backgroundColor: '#121214',
                      border: '1px solid rgba(255, 255, 255, 0.08)',
                      borderRadius: '8px',
                      padding: '0.75rem 1.25rem',
                      color: '#ffffff',
                      fontSize: '0.95rem',
                      outline: 'none',
                      cursor: 'pointer'
                    }}
                    disabled={isRegistering}
                  >
                    <option value="short_course">Short Course</option>
                    <option value="course">Course</option>
                    <option value="specialization">Specialization</option>
                  </select>
                  <button type="submit" className={styles.submitBtn} disabled={isRegistering}>
                    {isRegistering ? (
                      <>
                        <div className={styles.spinner} /> Scraping...
                      </>
                    ) : (
                      <>
                        <PlusCircle size={18} /> Register
                      </>
                    )}
                  </button>
                </form>
              </div>

              {/* Registered Courses Grid */}
              <h2 style={{ fontSize: '1.25rem', marginBottom: '1.25rem', color: '#ffffff' }}>Your Courses</h2>
              <div className={styles.coursesGrid}>
                {courses.length > 0 ? (
                  courses.map(course => {
                    const isReady = course.status === 'ready';
                    const isScraping = course.status === 'scraping';
                    const isError = course.status === 'error';
                    return (
                    <div
                      key={courseKey(course)}
                      className={styles.courseCard}
                      onClick={() => openCourse(course)}
                      style={{ cursor: isReady ? 'pointer' : 'default', opacity: isReady ? 1 : 0.85 }}
                    >
                      <div>
                        <div className={styles.courseCardHeader}>
                          <h3 style={{ marginRight: '1rem' }}>{course.title}</h3>
                          <button
                            onClick={(e) => handleDeleteCourse(course, e)}
                            style={{ background: 'transparent', border: 'none', color: '#71717a', cursor: 'pointer', padding: '4px', display: 'flex', alignItems: 'center', transition: 'color 0.2s' }}
                            onMouseEnter={(e) => e.currentTarget.style.color = '#ef4444'}
                            onMouseLeave={(e) => e.currentTarget.style.color = '#71717a'}
                            title="Remove Course"
                          >
                            <Trash2 size={16} />
                          </button>
                        </div>
                        {isScraping && (() => {
                          const p = progressOf(course);
                          const isSpec = course.item_type === 'specialization';
                          return (
                          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginTop: '0.5rem' }}>
                            <ProgressRing fetched={p.fetched} total={p.total} />
                            <div style={{ fontSize: '0.85rem', color: '#a1a1aa' }}>
                              {isSpec ? 'Fetching courses…' : 'Fetching transcripts…'}
                              <div style={{ fontSize: '0.8rem', color: '#71717a' }}>
                                {p.fetched}/{p.total || '…'} {p.unit}
                              </div>
                              {isSpec && course.current_title && (
                                <div style={{ fontSize: '0.78rem', color: '#71717a', marginTop: '0.15rem' }}>
                                  {course.current_title}: {course.current_fetched ?? 0}/{course.current_total || '…'} transcripts
                                </div>
                              )}
                            </div>
                          </div>
                          );
                        })()}
                        {isError && (
                          <p style={{ color: '#ef4444', fontSize: '0.85rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                            <AlertCircle size={14} /> {course.error || 'Fetch failed'}
                          </p>
                        )}
                        {isReady && course.cached && (
                          <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.35rem', fontSize: '0.75rem', fontWeight: 600, color: '#22c55e', backgroundColor: 'rgba(34,197,94,0.1)', borderRadius: '4px', padding: '0.2rem 0.5rem', marginTop: '0.5rem' }}>
                            <CheckCircle size={12} /> Already Available
                          </span>
                        )}
                      </div>
                      <div className={styles.courseCardFooter}>
                        <span className={styles.syllabusBadge}>
                          {course.item_type === 'specialization' ? 'Specialization' : `${course.syllabus?.length || 0} Lessons`}
                        </span>
                        <span>{isReady ? 'Click to explore' : isError ? 'Error' : 'Fetching…'}</span>
                      </div>
                    </div>
                    );
                  })
                ) : (
                  <div className={styles.emptyState}>
                    <Inbox size={48} />
                    <h3 style={{ color: '#a1a1aa', margin: '0.5rem 0' }}>No Registered Courses</h3>
                    <p style={{ margin: 0, fontSize: '0.9rem' }}>Get started by registering your first DeepLearning.AI course above.</p>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </main>

      {activeTranscript && (
        <div style={{
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          backgroundColor: 'rgba(9, 9, 11, 0.85)',
          backdropFilter: 'blur(8px)',
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          zIndex: 1000,
          padding: '1.5rem'
        }}>
          <div style={{
            backgroundColor: '#121214',
            border: '1px solid rgba(255, 255, 255, 0.08)',
            borderRadius: '12px',
            width: '100%',
            maxWidth: '650px',
            maxHeight: '80vh',
            display: 'flex',
            flexDirection: 'column',
            boxShadow: '0 20px 25px -5px rgba(0, 0, 0, 0.5), 0 10px 10px -5px rgba(0, 0, 0, 0.5)'
          }}>
            <div style={{
              display: 'flex',
              alignItems: 'center',
              padding: '1.25rem 1.5rem',
              borderBottom: '1px solid rgba(255, 255, 255, 0.08)'
            }}>
              <h3 style={{ margin: 0, fontSize: '1.15rem', fontWeight: 700, color: '#ffffff', wordBreak: 'break-word', maxWidth: '70%' }}>
                Transcript: {activeTranscript.lessonTitle}
              </h3>
              <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', marginLeft: 'auto' }}>
                <button
                  onClick={() => {
                    navigator.clipboard.writeText(activeTranscript.text);
                    alert("Transcript copied to clipboard!");
                  }}
                  style={{
                    backgroundColor: 'rgba(255, 255, 255, 0.05)',
                    border: '1px solid rgba(255, 255, 255, 0.08)',
                    borderRadius: '6px',
                    color: '#ffffff',
                    padding: '0.4rem 0.75rem',
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.35rem',
                    fontSize: '0.8rem',
                    fontWeight: 600
                  }}
                >
                  <Copy size={14} /> Copy
                </button>
                <button
                  onClick={() => setActiveTranscript(null)}
                  style={{
                    backgroundColor: 'transparent',
                    border: 'none',
                    color: '#71717a',
                    cursor: 'pointer',
                    padding: '4px',
                    display: 'flex',
                    alignItems: 'center'
                  }}
                >
                  <X size={20} />
                </button>
              </div>
            </div>
            <div style={{
              padding: '1.5rem',
              overflowY: 'auto',
              color: '#d4d4d8',
              fontSize: '0.95rem',
              lineHeight: 1.6,
              whiteSpace: 'pre-wrap',
              fontFamily: 'inherit'
            }}>
              {activeTranscript.text}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
