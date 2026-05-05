# 🧭 Pulse AI — Developer Skills Roadmap

> A progressive learning path covering every skill needed to build the Pulse AI retail analytics chatbot from scratch.  
> **Total estimated time: ~29 weeks** (part-time, ~10 hrs/week)

---

## 📌 What is Pulse AI?

Pulse AI is a **SQL-grounded, multi-agent conversational analytics bot** that:
- Accepts natural language questions from authenticated users
- Translates them into SQL via an LLM agent pipeline
- Executes queries against a SQL Server data warehouse
- Streams answers back in plain English with charts, citations and verification
- Persists per-user conversation threads in MongoDB
- Tracks every LLM call and user feedback in Langfuse

```
User (React + MSAL)
       │  HTTPS + Bearer JWT
       ▼
FastAPI Backend
   ├── Auth (Azure AD JWT validation)
   ├── Orchestrator (per-user instance)
   │    ├── Classifier → Domain Guard → Clarifier
   │    ├── SQL Writer → SQL Guard → SQL Server
   │    ├── Summariser (streaming) → Verifier → Citation Builder
   │    ├── Visualisation → Chart.js JSON
   │    └── KPI Router → Stored Procedures
   ├── MongoDB (conversation threads)
   ├── SQLite (SQL cache + entity index)
   └── Langfuse (traces + user feedback scores)
```

---

## 🗺️ How to use this roadmap

Each level builds on the previous one. Complete the exercises, then move on.  
You don't need to master everything in a level before moving forward — 80% is enough.

| Level | Topic | Weeks | Milestone |
|-------|-------|-------|-----------|
| 1 | Foundation | 1–4 | Write a Python script that calls OpenAI |
| 2 | Backend Core | 5–8 | Build a FastAPI REST + SSE server |
| 3 | Databases | 9–11 | Query SQL Server & MongoDB from Python |
| 4 | LLM Engineering | 12–16 | Build a multi-turn chatbot with structured output |
| 5 | Multi-Agent Orchestration | 17–20 | Build a SQL-generating agent with memory & caching |
| 6 | Auth & Security | 21–23 | Add Microsoft login with JWT validation |
| 7 | Frontend | 24–27 | Build a streaming React chat UI |
| 8 | Observability | 28 | Add Langfuse tracing and user feedback |
| 9 | DevOps | 29 | Containerise services with Docker Compose |

---

## 🟢 Level 1 — Foundation `Weeks 1–4`

> **By the end:** You can write a Python script that calls OpenAI, parses the response, and stores results to a file. You can write basic TypeScript.

### Python 3.10+
- [ ] Type hints: `str | None`, `list[dict]`, `TypeVar`, generics
- [ ] Dataclasses: `@dataclass(frozen=True)` for immutable config
- [ ] Generators: `yield`, `yield from`, generator functions
- [ ] Context managers: `with`, `__enter__`/`__exit__`, `contextlib`
- [ ] `contextvars.ContextVar` — per-thread/task state without global variables
- [ ] `functools.lru_cache` — memoisation / singleton pattern
- [ ] Virtual environments: `python -m venv`, `pip install -r requirements.txt`
- [ ] `python-dotenv`: loading `.env` files, `os.getenv()`

### TypeScript
- [ ] Interfaces and type aliases
- [ ] Union types (`string | null`), optional fields (`field?`)
- [ ] Generics (`Array<T>`, `Promise<T>`)
- [ ] `async/await`, `try/catch`
- [ ] `import`/`export` (ES modules)
- [ ] `npm`/`node` basics

### Git & Project Hygiene
- [ ] `git init`, `add`, `commit`, `branch`, `push`
- [ ] `.gitignore` — never commit `.env`, `__pycache__`, `node_modules`
- [ ] `.env` vs `.env.example` pattern — commit examples, not secrets

### 📚 Resources
- [Python Official Tutorial](https://docs.python.org/3/tutorial/)
- [TypeScript Handbook](https://www.typescriptlang.org/docs/handbook/)
- [Pro Git Book](https://git-scm.com/book)

---

## 🟡 Level 2 — Backend Core `Weeks 5–8`

> **By the end:** You can build a FastAPI server with typed routes, dependency injection, CORS, and real-time Server-Sent Events streaming.

### FastAPI
- [ ] Route definitions: `@app.get`, `@app.post`, path/query parameters
- [ ] **Pydantic request/response models** — `BaseModel`, field validation
- [ ] **Dependency injection** — `Depends()` for shared logic (auth, DB sessions)
- [ ] **Middleware** — `CORSMiddleware`, `allow_origins`, `allow_headers`
- [ ] **Lifespan** — `@asynccontextmanager async def lifespan(app)` for startup/shutdown
- [ ] **`StreamingResponse`** — streaming HTTP responses
- [ ] **Server-Sent Events (SSE)** — `text/event-stream`, `data: {...}\n\n` format
- [ ] `HTTPException` for structured error responses
- [ ] `dataclasses.asdict` — serialize dataclasses to dicts

### Pydantic v2
- [ ] `BaseModel`, `Field(default=...)`
- [ ] `model_validate(dict)`, `model_dump()`
- [ ] `model_validate_json(string)` — parse JSON directly
- [ ] `ValidationError` handling
- [ ] Frozen `@dataclass` as settings object

### Configuration Pattern
```python
@dataclass(frozen=True)
class Settings:
    llm_model: str
    mongo_uri: str | None = None

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv()
    return Settings(llm_model=os.getenv("LLM_MODEL", "gpt-4o-mini"))
```

### 📚 Resources
- [FastAPI Official Docs](https://fastapi.tiangolo.com/)
- [Pydantic v2 Docs](https://docs.pydantic.dev/latest/)
- [SSE Explained](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events)

---

## 🟡 Level 3 — Databases `Weeks 9–11`

> **By the end:** You can read from SQL Server with pyodbc, store/retrieve documents in MongoDB, and use SQLite as a lightweight local cache.

### SQL Server via pyodbc
- [ ] ODBC connection strings (`Driver`, `Server`, `Database`, `UID`, `PWD`)
- [ ] `cursor.execute(sql)`, `cursor.fetchmany(n)`, `cursor.description`
- [ ] Parameterised queries — `cursor.execute(sql, [param1, param2])`
- [ ] **Stored procedures**: `EXEC ProcName @param=?`
- [ ] `contextlib.closing` — auto-close connections
- [ ] SQL read-only guard — block `INSERT`/`UPDATE`/`DROP` from user input
- [ ] `Decimal` → `float` conversion for JSON serialisation

### MongoDB via pymongo
- [ ] `MongoClient(uri)`, `client[db][collection]`
- [ ] Document model design — nested arrays (threads → turns)
- [ ] `create_index([...], unique=True)` — compound unique indexes
- [ ] `update_one(filter, {"$set": ..., "$setOnInsert": ...}, upsert=True)`
- [ ] `find(filter)` — cursor iteration
- [ ] Multi-tenancy pattern: `{app_id, user_id, thread_id}` as document key
- [ ] MongoDB Atlas SRV connection strings

### SQLite (caching)
- [ ] `sqlite3.connect(path)`, `cursor.execute`, `connection.commit`
- [ ] Schema creation with `CREATE TABLE IF NOT EXISTS`
- [ ] TTL-based invalidation: store `created_at`, delete expired rows
- [ ] Use cases: SQL query cache, entity search index

### 📚 Resources
- [pyodbc Wiki](https://github.com/mkleehammer/pyodbc/wiki)
- [pymongo Tutorial](https://pymongo.readthedocs.io/en/stable/tutorial.html)
- [SQLite Python Docs](https://docs.python.org/3/library/sqlite3.html)

---

## 🔵 Level 4 — LLM Engineering `Weeks 12–16`

> **By the end:** You can build a multi-turn chatbot that classifies questions, generates structured JSON output, and embeds text for semantic search.

### LLM Fundamentals
- [ ] **Prompt structure**: `system` role (instructions) + `user` role (input)
- [ ] `temperature=0` for deterministic, reproducible outputs
- [ ] **Structured output**: asking the LLM to return JSON, then parsing it
- [ ] **Streaming**: `stream=True`, iterating delta chunks
- [ ] **Embeddings**: `litellm.embedding()`, cosine similarity, similarity threshold
- [ ] Context windows: token limits, conversation history truncation
- [ ] **Prompt engineering patterns**:
  - Few-shot examples in the system prompt
  - Chain-of-thought reasoning
  - Output format specification with examples
  - Negative examples ("do NOT do X")

### LiteLLM
- [ ] `litellm.completion(model, messages, temperature, timeout)`
- [ ] Model format: `"openai/gpt-4.1-mini"`, `"anthropic/claude-3-5-sonnet-latest"`
- [ ] `litellm.embedding(model, input=[text])`
- [ ] `litellm.success_callback = ["langfuse"]` — observability hook
- [ ] `metadata={"trace_id": ..., "generation_name": ...}` — trace propagation
- [ ] Error types: `APIConnectionError`, `Timeout`, SSL errors

### Agent Patterns (as used in Pulse AI)

| Agent | Input | Output | Pattern |
|-------|-------|--------|---------|
| Classifier | question + history | `{label: "business_question"}` | JSON structured output |
| Domain guard | question | `{in_scope: bool, rejection_message}` | JSON structured output |
| SQL writer | question + schema | SQL string | Text output |
| SQL explainer | SQL string | plain English | Text output |
| Summariser | question + rows | narrative answer | **Streaming** text |
| Verifier | answer + rows | `{verified, issues[]}` | JSON structured output |
| Citation builder | answer + rows | `[{claim, source_column, ...}]` | JSON structured output |
| Clarifier | question | `{needs_clarification, question}` | JSON structured output |
| KPI router | question + KPIs | `{kpi, params}` or null | JSON structured output |

### 📚 Resources
- [LiteLLM Docs](https://docs.litellm.ai/)
- [OpenAI Prompt Engineering Guide](https://platform.openai.com/docs/guides/prompt-engineering)
- [Embeddings Explainer](https://platform.openai.com/docs/guides/embeddings)

---

## 🔵 Level 5 — Multi-Agent Orchestration `Weeks 17–20`

> **By the end:** You can build a full SQL-generating agent pipeline with streaming, error recovery, conversation memory, semantic caching, and chart output.

### Orchestrator Pattern
```
ChatOrchestrator (one per user)
  ├── classify → domain_guard → clarify
  ├── resolve_sql (cache lookup → LLM write → execute → retry on error)
  ├── stream_summarise (streaming tokens → SSE)
  ├── parallel: [visualise, explain_sql]
  ├── parallel: [verify, build_citations]
  └── persist to memory → yield metadata SSE event
```

- [ ] Central router class with per-user singleton
- [ ] `ThreadPoolExecutor` for parallel background tasks
- [ ] `Future.result()` with timeout + fallback
- [ ] SSE event protocol: `start` → `token` × N → `metadata` → `[DONE]`
- [ ] Collecting streamed chunks into full text

### Error Recovery (QueryResolver)
- [ ] 6 retry strategies: syntax fix, column fix, broaden, simplify, safe rewrite, full rewrite
- [ ] Each strategy re-calls the LLM with a different prompt and the error message
- [ ] Exponential backoff concept
- [ ] Graceful fallback: if all retries fail → return helpful error message

### Conversation Memory
- [ ] `deque(maxlen=N)` — sliding window of last N turns
- [ ] Thread abstraction: create, switch, list
- [ ] Rich turn metadata: SQL, citations, chart data, verification
- [ ] Dual backend: MongoDB (production) / JSON file (dev)
- [ ] Building context string for LLM: `Turn 1 | User: ... | Assistant: ...`

### Semantic SQL Cache
- [ ] Question normalisation: lowercase, remove stop words, strip whitespace
- [ ] Schema fingerprint: `hash(schema_string)` — invalidate when schema changes
- [ ] Embedding lookup: embed the question → find nearest cached question
- [ ] Similarity threshold (e.g. 0.88): only use cache hit above threshold
- [ ] Exact match first, semantic match second

### Entity Resolution
- [ ] `difflib.SequenceMatcher` — fuzzy string similarity
- [ ] Multiple resolvers for different entity types (city, state, store, etc.)
- [ ] Embedding-based fallback when fuzzy match is weak
- [ ] Confidence score + source tag in result
- [ ] SQLite index with TTL for resolver results

### Visualisation Pipeline
- [ ] Detect column roles: first non-numeric = label, rest = metrics
- [ ] Keyword matching: question contains "trend"/"over time" → line chart
- [ ] Smart date format: ≤62 days → `"07 Apr"`, ≤2 years → `"Apr '26"`
- [ ] Chart title from question: strip filler prefixes, title-case remainder
- [ ] Chart.js JSON schema: `{chart_type, title, labels, datasets}`

### 📚 Resources
- [Python concurrent.futures](https://docs.python.org/3/library/concurrent.futures.html)
- [Python deque](https://docs.python.org/3/library/collections.html#collections.deque)
- [difflib SequenceMatcher](https://docs.python.org/3/library/difflib.html)

---

## 🟠 Level 6 — Authentication & Security `Weeks 21–23`

> **By the end:** You can add Microsoft login to a web app, validate JWT tokens on the backend, and isolate data per authenticated user.

### OAuth 2.0 / OpenID Connect Concepts
- [ ] **Authorization Code flow with PKCE** — the secure SPA flow
- [ ] **ID token** vs **access token** — what each is for
- [ ] **JWT structure**: `header.payload.signature` (Base64url encoded)
- [ ] Key claims: `aud` (audience), `iss` (issuer), `exp` (expiry), `preferred_username`
- [ ] **JWKS** (JSON Web Key Set) — Microsoft's public keys for signature verification
- [ ] **RS256** — RSA signature with SHA-256

### Azure Active Directory
- [ ] App Registration: Client ID, Tenant ID
- [ ] **Authentication → Single-page application** platform, redirect URIs
- [ ] Implicit grant OFF (MSAL v3 uses PKCE, not implicit)
- [ ] Token configuration: add `email` optional claim on ID token
- [ ] API permissions: `openid`, `profile`, `email`

### MSAL Browser (Frontend)
```typescript
const msalInstance = new PublicClientApplication(msalConfig);
await msalInstance.initialize();           // required in v3+
instance.loginRedirect(loginRequest);      // redirect to Microsoft
await instance.acquireTokenSilent({...})   // silent renewal
resp.idToken                               // use ID token as Bearer
```

### Backend JWT Validation
```python
jwks = httpx.get(f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys").json()
claims = jwt.decode(token, jwks, algorithms=["RS256"],
                    audience=client_id,
                    issuer=f"https://login.microsoftonline.com/{tenant}/v2.0")
email = claims["preferred_username"]
```
- [ ] JWKS in-memory cache (1-hour TTL) — avoid fetching on every request
- [ ] `python-jose[cryptography]` library
- [ ] FastAPI `OAuth2PasswordBearer` security scheme
- [ ] Graceful dev bypass when `AZURE_TENANT_ID` is unset

### Per-User Isolation
- [ ] Per-user orchestrator dict + `threading.Lock` double-checked locking
- [ ] Per-user MongoDB `user_id` namespace
- [ ] Email normalisation: `email.lower().strip()`

### 📚 Resources
- [Microsoft Identity Platform Docs](https://docs.microsoft.com/en-us/azure/active-directory/develop/)
- [JWT.io Debugger](https://jwt.io/)
- [python-jose Docs](https://python-jose.readthedocs.io/)
- [MSAL Browser Docs](https://github.com/AzureAD/microsoft-authentication-library-for-js)

---

## 🟣 Level 7 — Frontend `Weeks 24–27`

> **By the end:** You can build a production React chat UI with streaming responses, charts, Microsoft login, and a conversation thread sidebar.

### React 19
- [ ] `useState`, `useEffect`, `useRef`, `useCallback` — core hooks
- [ ] Lifting state up, prop drilling vs. context
- [ ] Async effects: fetch data on mount, clean up on unmount
- [ ] Controlled inputs (`value` + `onChange`)
- [ ] List rendering with stable `key` props

### Streaming UI Pattern
```typescript
for await (const event of streamMessage(text)) {
  if (event.type === 'token') {
    fullText += event.content;
    setMessages(prev => prev.map(m =>
      m.id === botId ? { ...m, text: fullText } : m
    ));
  }
}
```
- [ ] Async generator for SSE stream parsing
- [ ] Append tokens to message in place (immutable state update)
- [ ] Streaming cursor animation with CSS

### MSAL React
```tsx
<MsalProvider instance={msalInstance}>
  <AuthenticatedTemplate><ChatApp /></AuthenticatedTemplate>
  <UnauthenticatedTemplate><LoginPage /></UnauthenticatedTemplate>
</MsalProvider>
```
- [ ] `MsalProvider` — context wrapper
- [ ] `AuthenticatedTemplate` / `UnauthenticatedTemplate`
- [ ] `useMsal()`, `useIsAuthenticated()` hooks
- [ ] `acquireTokenSilent()` → `resp.idToken`
- [ ] **v3+ initialisation**: call `msalInstance.initialize()` before `ReactDOM.createRoot()`

### SSE Client (fetch-based)
```typescript
const response = await fetch('/chat/stream', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
  body: JSON.stringify({ message }),
});
const reader = response.body.getReader();
// parse data: {...}\n\n lines
```

### Axios with Auth Interceptor
```typescript
api.interceptors.request.use(async (config) => {
  config.headers.Authorization = `Bearer ${await getToken()}`;
  return config;
});
```

### Chart.js
- [ ] `new Chart(canvas, { type, data: { labels, datasets }, options })`
- [ ] Line, bar, pie configurations
- [ ] Dynamic update: `chart.data.labels = [...]; chart.update()`
- [ ] Responsive sizing, legend, tooltips

### Vite
- [ ] `vite.config.ts` — server proxy for backend API
- [ ] `.env` with `VITE_*` prefix for injected vars
- [ ] `import.meta.env.VITE_AZURE_CLIENT_ID`
- [ ] `npm run dev` vs `npm run build`

### 📚 Resources
- [React Docs (react.dev)](https://react.dev/)
- [Chart.js Docs](https://www.chartjs.org/docs/)
- [Vite Guide](https://vitejs.dev/guide/)

---

## ⚫ Level 8 — Observability & Feedback `Week 28`

> **By the end:** You can trace every LLM call to a Langfuse dashboard, see per-user cost breakdowns, and persist user 👍/👎 feedback linked to specific traces.

### Langfuse v2 Concepts
- [ ] **Trace** — one user request = one trace (input question, output answer, user email)
- [ ] **Span / Generation** — one LLM call within a trace (tokens, cost, latency, prompt)
- [ ] **Score** — user feedback attached to a trace (`value: 1` = 👍, `value: 0` = 👎)

### LiteLLM Auto-Logging
```python
litellm.success_callback = ["langfuse"]   # one line — logs all calls
# Pass metadata to link generation to correct trace:
litellm.completion(..., metadata={
    "trace_id": current_trace_id(),
    "trace_user_id": "john@company.com",
    "generation_name": "sql_writer",
})
```

### ContextVar Pattern (zero-intrusion tracing)
```python
# Set once per request:
_trace_id_var.set(trace.id)

# Read anywhere in the call stack without passing parameters:
def _lf_meta(name): return {"trace_id": _trace_id_var.get(), ...}
```

### Feedback API
```python
langfuse.score(trace_id=req.trace_id, name="user-feedback",
               value=req.score, data_type="BOOLEAN")
```

### Self-Hosting with Docker
- [ ] Langfuse v2 Docker Compose (Postgres + Langfuse server)
- [ ] Health check, depends_on with condition
- [ ] Image pinning (`:2` not `:latest` to avoid v3 breaking changes)
- [ ] Dashboard at `http://localhost:3001`

### 📚 Resources
- [Langfuse Docs](https://langfuse.com/docs)
- [LiteLLM Langfuse Integration](https://docs.litellm.ai/docs/observability/langfuse_integration)

---

## ⚙️ Level 9 — DevOps & Infrastructure `Week 29`

> **By the end:** You can containerise a multi-service app, manage secrets safely, and handle corporate proxy/TLS issues.

### Docker & Docker Compose
- [ ] `Dockerfile` basics: `FROM`, `COPY`, `RUN`, `CMD`
- [ ] `docker compose up -d`, `docker compose down`, `docker compose logs`
- [ ] Service dependencies: `depends_on` with `condition: service_healthy`
- [ ] `healthcheck`: `test`, `interval`, `retries`
- [ ] Named volumes for data persistence
- [ ] Port mapping: `"3001:3000"` (host:container)
- [ ] Environment injection: `environment:` vs `env_file:`
- [ ] Image pinning: always pin to a specific major version

### Environment & Secret Management
- [ ] `.env` — local secrets (never commit)
- [ ] `.env.example` — committed template with placeholder values
- [ ] `SSL_CERT_FILE` — corporate root CA for TLS inspection
- [ ] Docker proxy config for corporate networks

### Running the Full Stack Locally
```bash
# Start Langfuse
docker compose -f docker-compose.langfuse.yml up -d

# Start MongoDB
net start MongoDB

# Start backend
uvicorn api:app --reload --port 8000

# Start frontend
cd frontend && npm run dev
```

### 📚 Resources
- [Docker Getting Started](https://docs.docker.com/get-started/)
- [Docker Compose Reference](https://docs.docker.com/compose/compose-file/)

---

## 🎓 Capstone Project

Build a mini version of Pulse AI in 4 sprints:

| Sprint | Deliverable | Skills practised |
|--------|-------------|------------------|
| 1 (1 week) | FastAPI + SQLite + one LLM agent (question → SQL → answer) | Levels 2, 3, 4 |
| 2 (1 week) | Add streaming SSE + React chat UI | Levels 2, 7 |
| 3 (1 week) | Add conversation memory + Langfuse tracing | Levels 5, 8 |
| 4 (1 week) | Add Microsoft login + per-user isolation | Level 6 |

---

## 📦 Quick-Reference: Full Library Cheat Sheet

### Python (Backend)
| Library | Version | Purpose |
|---------|---------|---------|
| `fastapi` | latest | REST API framework |
| `pydantic` | ≥2.7 | Data validation & serialisation |
| `uvicorn` | latest | ASGI server |
| `litellm` | ≥1.74 | Multi-provider LLM abstraction |
| `openai` | ≥1.0 | OpenAI SDK (used by LiteLLM) |
| `langfuse` | ≥2,<3 | LLM observability & feedback |
| `pymongo[srv]` | ≥4.7 | MongoDB driver |
| `pyodbc` | ≥5.1 | SQL Server (ODBC) |
| `python-jose[cryptography]` | ≥3.3 | JWT validation |
| `httpx` | ≥0.27 | Async HTTP client (JWKS fetch) |
| `python-dotenv` | ≥1.0 | `.env` file loading |

### JavaScript/TypeScript (Frontend)
| Package | Version | Purpose |
|---------|---------|---------|
| `react` | ^19 | UI framework |
| `@azure/msal-browser` | ^5 | Azure AD authentication |
| `@azure/msal-react` | ^5 | React wrapper for MSAL |
| `axios` | ^1.15 | HTTP client with interceptors |
| `chart.js` | ^4.5 | Data visualisation |
| `react-markdown` | ^10 | Markdown rendering |
| `remark-gfm` | ^4 | GitHub Flavored Markdown |
| `uuid` | ^13 | Unique ID generation |
| `vite` | ^8 | Build tool & dev server |
| `typescript` | ~6 | Static typing |
| `tailwindcss` | ^4 | Utility-first CSS |

### Infrastructure
| Tool | Version | Purpose |
|------|---------|---------|
| MongoDB Community | 7+ | Conversation thread storage |
| SQL Server / Fabric DW | — | Business data warehouse |
| Langfuse | v2 | LLM observability dashboard |
| Docker Desktop | latest | Container runtime |
| PostgreSQL | 16 | Langfuse backend database |

---

*Generated for Pulse AI — Arvind Fashions Retail Analytics Assistant*
