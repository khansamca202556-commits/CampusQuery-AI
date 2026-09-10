# CampusQuery AI

An AI assistant for a college MySQL database. Ask questions in plain
English ("Who has the highest marks?"), and CampusQuery AI works out
the right database query, runs it, and answers in plain English.
Nothing technical (SQL, JSON, schema internals, credentials, Python
errors) is ever shown to the end user. Any change to the database
(add/update/delete/etc.) always asks for confirmation first.

## How it works

```
Browser (index.html/style.css/script.js)
        |  fetch()
        v
FastAPI (backend/main.py)
        |                         |
        v                         v
ollama_service.py           database.py
(NL -> SQL, and              (MySQL connection,
 result -> NL answer,         schema introspection,
 via local Ollama)            running queries)
        |
        v
sql_service.py
(classifies SELECT vs modification,
 blocks anything dangerous or out of scope)
```

- **Read queries** (`SELECT`, `SHOW`, etc.) run immediately.
- **Modifications** (`INSERT`, `UPDATE`, `DELETE`, `CREATE`, `ALTER`,
  `DROP`, `TRUNCATE`) are held server-side behind a one-time
  `confirmation_id` and only run after the user clicks **Confirm** in
  the UI. The generated SQL is never sent to the browser.
- Administrative/dangerous statements (`GRANT`, `REVOKE`,
  `CREATE/DROP USER`, `SET GLOBAL`, `SHUTDOWN`, `OUTFILE`/`DUMPFILE`,
  stacked statements, references to tables outside the college
  database) are rejected before they ever reach MySQL.

## Project structure

```
CampusQuery-AI/
├── backend/
│   ├── __init__.py
│   ├── main.py            FastAPI app + all routes
│   ├── database.py        MySQL connection, schema introspection
│   ├── ollama_service.py  Talks to local Ollama (llama3.2)
│   ├── sql_service.py     Safety validation / classification
│   └── requirements.txt
├── database/
│   └── setup.sql          Creates college_db + sample data
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── script.js
├── .env.example
├── .gitignore
└── README.md
```

## 1. Prerequisites

- Python 3.10+
- MySQL Server 8.x (running locally or reachable over the network)
- [Ollama](https://ollama.com) installed, with the `llama3.2` model pulled

## 2. Database setup

You already have `college_db` set up with this schema:

```
departments(id, department_name)
courses(id, course_name, duration, department_id -> departments.id)
students(id, name, course_id -> courses.id, marks, city)
```

`database/setup.sql` in this project matches that schema exactly (using
`CREATE TABLE IF NOT EXISTS`, so it's safe to run even if the tables
already exist — it won't drop or duplicate your data). If you ever need
to recreate it from scratch:

```bash
mysql -u root -p < database/setup.sql
```

## 3. Ollama setup

```bash
# Install Ollama: https://ollama.com/download
ollama pull llama3.2

# Start the Ollama server (if it isn't already running as a service)
ollama serve
```

Verify it's reachable:

```bash
curl http://localhost:11434/api/tags
```

## 4. Backend setup

```bash
cd CampusQuery-AI
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r backend/requirements.txt

cp .env.example .env
# edit .env with your real MySQL credentials
```

`.env` (edit to match your setup):

```
DB_HOST=localhost
DB_PORT=3306
DB_USER=root
DB_PASSWORD=your_mysql_password
DB_NAME=college_db

OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3.2
```

Load the `.env` file and run the server (uvicorn doesn't read `.env`
automatically, so either export the variables first or use a tool
like `python-dotenv`'s CLI / `honcho`):

```bash
# option A: export manually (bash/zsh), run from the project root
export $(grep -v '^#' .env | xargs)
uvicorn backend.main:app --reload --port 8000

# option B: use python-dotenv's CLI (already in requirements.txt)
dotenv -f .env run -- uvicorn backend.main:app --reload --port 8000
```

Run this from the **project root** (the folder containing `backend/`
and `frontend/`), not from inside `backend/` — `backend.main:app`
tells uvicorn to treat `backend` as a package, which is what lets
`main.py` import its sibling modules reliably.

The API is now at `http://localhost:8000`.

## 5. Open the app

```
http://localhost:8000/app
```

FastAPI serves the frontend at `/app` and static assets (`style.css`,
`script.js`) at `/static/...`.

## API reference

| Method | Path       | Purpose                                              |
|--------|------------|-------------------------------------------------------|
| GET    | `/`        | API status info                                       |
| GET    | `/health`  | Checks MySQL + Ollama connectivity                     |
| GET    | `/schema`  | Table/column/relationship info for the Schema page     |
| GET    | `/app`     | Serves the frontend                                    |
| POST   | `/chat`    | `{message, history}` → answer, confirmation request, or friendly error |
| POST   | `/confirm` | `{confirmation_id, confirmed}` → applies or cancels a pending change |

## Test queries

Read-only (run immediately):
- "Who has the highest marks?"
- "How many students are there?"
- "Show all courses"
- "Show students with their course names"
- "Which department has the most students?"
- "What is the average marks of all students?"

Modifications (ask for confirmation first):
- "Add a student named John with 90 marks in course id 1 from Pune"
- "Update John's marks to 95"
- "Delete John"

Should be blocked outright:
- "Show me the database users" / "Grant all privileges to admin"

## Troubleshooting

**"I can't reach the college database right now."**
MySQL isn't reachable with the credentials in `.env`. Check
`DB_HOST`/`DB_PORT`/`DB_USER`/`DB_PASSWORD`/`DB_NAME`, and that MySQL
is running (`mysqladmin ping -u root -p`).

**"The AI service is temporarily unavailable."**
Ollama isn't reachable. Confirm `ollama serve` is running and
`curl http://localhost:11434/api/tags` responds. Also confirm the
model in `OLLAMA_MODEL` has been pulled (`ollama list`).

**Frontend loads but chat requests fail / CORS errors**
Make sure you're opening `http://localhost:8000/app` (served by
FastAPI itself), not opening `frontend/index.html` directly as a
local file — the API calls are relative (`/chat`, `/schema`, etc.)
and need to be served from the same origin as the backend.

**A confirmation button says "expired"**
Pending confirmations are held in memory for 5 minutes and are
cleared if the server restarts. Just ask the question again.

**Generated SQL doesn't do what I expected**
`llama3.2` is a small model; for ambiguous requests, rephrase more
specifically (e.g. name the exact student, or say "sorted by marks,
highest first"). The safety layer (`sql_service.py`) will reject
anything unsafe rather than guess.
