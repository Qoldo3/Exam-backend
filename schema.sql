CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    full_name TEXT NOT NULL,
    national_id TEXT NOT NULL UNIQUE,
    phone TEXT NOT NULL UNIQUE,
    token_version INTEGER NOT NULL DEFAULT 0,
    role TEXT NOT NULL DEFAULT 'user',
    is_blocked INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME
);

CREATE TABLE IF NOT EXISTS exams (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    duration_secs INTEGER NOT NULL,
    price INTEGER NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY,
    exam_id TEXT NOT NULL REFERENCES exams (id),
    body TEXT NOT NULL,
    option_a TEXT,
    option_b TEXT,
    option_c TEXT,
    option_d TEXT,
    correct_opt TEXT NOT NULL,
    order_num INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users (id),
    exam_id TEXT NOT NULL REFERENCES exams (id),
    amount INTEGER NOT NULL,
    authority TEXT,
    ref_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at DATETIME,
    verified_at DATETIME
);

CREATE TABLE IF NOT EXISTS exam_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users (id),
    exam_id TEXT NOT NULL REFERENCES exams (id),
    payment_id TEXT REFERENCES payments (id),
    created_at DATETIME,
    started_at DATETIME,
    expires_at DATETIME,
    ended_at DATETIME,
    current_q_idx INTEGER NOT NULL DEFAULT 0,
    score REAL,
    status TEXT NOT NULL DEFAULT 'pending',
    tab_switches INTEGER NOT NULL DEFAULT 0,
    fraud_count INTEGER NOT NULL DEFAULT 0,
    fraud_events TEXT
);

CREATE TABLE IF NOT EXISTS answers (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES exam_sessions (id),
    question_id TEXT NOT NULL REFERENCES questions (id),
    chosen_opt TEXT NOT NULL,
    answered_at DATETIME,
    CONSTRAINT uq_session_question UNIQUE (session_id, question_id)
);

CREATE TABLE IF NOT EXISTS admin_audit_log (
    id TEXT PRIMARY KEY,
    admin_id TEXT NOT NULL REFERENCES users (id),
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT,
    detail TEXT,
    created_at DATETIME
);

CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '',
    updated_at DATETIME
);

CREATE TABLE IF NOT EXISTS exam_access (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users (id),
    exam_id TEXT NOT NULL REFERENCES exams (id),
    granted_by TEXT REFERENCES users (id),
    created_at DATETIME,
    CONSTRAINT uq_user_exam_access UNIQUE (user_id, exam_id)
);
