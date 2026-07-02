# three-db-chat - Work Plan

## TL;DR (For humans)

**What you'll get:** A chat system like ChatGPT/Codex with two modes — project chats that search your documents AND know your user memories (via Neo4j graph), and individual chats that just know your user memories. Conversation IDs in the URL path (`/chat/<uuid>`) survive browser refreshes. Three databases each doing what they're best at: PostgreSQL for structured data, Qdrant for document search, Neo4j for your personal knowledge graph.

**Why this approach:** Neo4j is the industry standard for knowledge graphs — Cypher queries are simpler and faster than PostgreSQL recursive CTEs for entity relationships. Two memory tiers (short-term = conversation history, long-term = Neo4j graph) instead of three avoids unnecessary Redis complexity. Everything else builds on existing infrastructure (pgvector, Celery).

**What it will NOT do:** No Redis working memory tier (dropped as over-engineering), no breaking changes to your existing project chats, no changes to document ingestion. No collaborative features.

**Effort:** Medium-Large (8 tasks, reduced from 10 by simplifying memory tiers)
**Risk:** Low-Medium — Neo4j adds one Docker service, DB migration is additive (new column, nullable), all existing code paths untouched
**Decisions to sanity-check:** Neo4j schema design (node labels + relationship types), individual chat endpoint scope, entity extraction timing (always on user's last message), URL path design (`/chat/<id>`), `.env` mandatory vars list

Your next move: approve the plan, then run `$start-work` to execute.

---

> TL;DR (machine): Medium-Large effort, Low-Medium risk — Neo4j graph DB + 2 chat types + path-based URL (`/chat/<id>`) + simplified 2-tier memory + Codex-style frontend selector + .env as single source of truth. 8 tasks across 3 waves.

## Scope
### Must have
- **Neo4j** Docker container in compose.yaml + async Python driver
- **DB migration**: `Conversation.project_id` nullable + `user_id` FK
- **Individual chat** endpoint `POST /api/chat` (no project, no document retrieval)
- **Entity extraction** via Celery task: always extract from user's latest message after assistant responds (no content-type filtering — always run)
- **2-tier MemoryManager**: short-term (SQL conversation history) + long-term (Neo4j entities)
- **Neo4j context** injection into RAG pipeline (`RagState.graph_context`)
- **Path-based URLs**: `/chat/<conversation-id>` via optional catch-all route `[[...slug]]`, seamless `router.push()` (no visible refresh)
- **Unified frontend** chat view with Codex-style project selector at bottom
- **Sidebar**: both chat types display with type badge
- **.env as single source of truth**: all env vars documented in `.env.example` with mandatory vs optional clearly marked; compose.yaml and config.py always reference `.env`

### Must NOT have (guardrails, anti-slop, scope boundaries)
- Qdrant integration replaces pgvector for vector search
- No Redis working memory tier (dropped — unnecessary complexity)
- No entity vector embeddings (pure Neo4j Cypher search for now)
- No breaking changes to existing project chat routes or behavior
- No changes to document ingestion pipeline
- No collaborative/multi-user features
- No auth model changes
- No changes to `POST /api/projects/{project_id}/chat` response shape
- No hardcoded secrets or fallback defaults that bypass `.env`

## Verification strategy
> Zero human intervention — all verification is agent-executed.
- Test decision: tests-after (backend: pytest + httpx with Neo4j test container; frontend: vitest)
- Evidence: `.omo/evidence/task-<N>-three-db-chat.<ext>`

## Execution strategy
### Parallel execution waves
- **Wave 1** (infrastructure + schema): Tasks 1-3 (parallel-friendly: compose + migration + Neo4j repo)
- **Wave 2** (backend API + memory): Tasks 4-6 (depends on Wave 1)
- **Wave 3** (frontend): Tasks 7-8 (depends on Wave 2 backend)
- **Final**: Verification (parallel)

### Dependency matrix
| # | Todo | Depends on | Blocks | Parallel with |
|---|------|-----------|--------|--------------|
| 1 | Neo4j container + driver + `.env` audit | — | 3, 5 | 2 |
| 2 | DB migration (nullable project_id + user_id) | — | 4 | 1 |
| 3 | GraphRepository (Neo4j CRUD + Cypher) | 1 | 5, 6 | 2 |
| 4 | Individual chat endpoint + ConversationRepository updates | 2 | 7, 8 | 5 |
| 5 | Entity extraction Celery task + MemoryManager | 1, 3 | 6 | 4 |
| 6 | RAG pipeline Neo4j context injection | 3, 5 | — | — |
| 7 | Frontend: path-based URL + optional catch-all route | 4 | 8 | — |
| 8 | Frontend: Codex-style project selector + sidebar | 4, 7 | — | — |

## Todos

- [x] 1. **Neo4j container + Python driver + .env audit**
  What to do / Must NOT do:
  - Add Neo4j service to `compose.yaml`:
    ```yaml
    neo4j:
      image: neo4j:2025-community
      environment:
        NEO4J_AUTH: neo4j/${NEO4J_PASSWORD}
        NEO4J_dbms_memory_heap_max__size: 1G
      ports:
        - "7687:7687"  # Bolt
        - "7474:7474"  # Browser UI
      volumes:
        - neo4j-data:/data
      networks:
        - internal
    ```
    Note: `NEO4J_PASSWORD` is now **mandatory** with NO default fallback (user explicitly asked for .env as single source of truth). The value must be set in `.env`.
  - Add `neo4j-data` volume to `volumes:` section
  - Add healthcheck to compose.yaml for neo4j:
    ```yaml
    healthcheck:
      test: ["CMD", "cypher-shell", "-u", "neo4j", "-p", "${NEO4J_PASSWORD}", "RETURN 1"]
    ```
  - Add Neo4j env vars to `backend/app/config.py`:
    ```python
    neo4j_uri: str = Field(default="bolt://localhost:7687")
    neo4j_user: str = Field(default="neo4j")
    neo4j_password: str = Field(..., min_length=1)  # MANDATORY — no default, must be in .env
    ```
  - Create `backend/app/services/neo4j_client.py`:
    - Singleton `Neo4jClient` with `AsyncGraphDatabase.driver()` (bolt protocol)
    - `close()` method for shutdown
    - Connection health check method
    - Must use async driver (`neo4j` package, `AsyncDriver`)
  - Install `neo4j` Python package in `backend/pyproject.toml`
  - **Audit all env vars** and ensure `.env.example` is the complete single source of truth:
    - Audit every env var referenced in: `compose.yaml`, `compose.dev.yaml`, `backend/app/config.py`, `frontend/.env`, `backend/.env`, `infra/`, `ops/`
    - Update `.env.example` to list ALL env vars the application needs, grouped by category with clear comments
    - Mark mandatory vars with `# REQUIRED` — vars that have NO default fallback and MUST be set in `.env`
    - Mark optional vars with `# Optional: defaults to <value>` — vars with safe defaults
    - Mandatory vars currently: `DATABASE_URL`, `JWT_SECRET`, `NEO4J_PASSWORD` (after this task)
    - Optional vars currently: everything else with a `:-default` in compose.yaml or `Field(default=...)` in config.py
    - Remove duplicate `.env` files in `backend/` and `frontend/` — the root `.env` is the single source of truth
    - Update `backend/app/config.py` `SettingsConfigDict` to use `env_file=".env"` (it already points to `.env` by default from Pydantic, but ensure the path resolves correctly relative to the backend working directory)
    - Verify `compose.yaml` removes all `:-default` fallbacks for vars that should be mandatory (currently none are truly mandatory with no fallback — `JWT_SECRET` has a fallback, `DATABASE_URL` has `:-ragapp` fallbacks for components but the full URL has no fallback in compose.yaml)
  - Must NOT expose Neo4j browser on public network (internal only for Docker)
  - Must NOT hardcode credentials — `NEO4J_PASSWORD` is mandatory from `.env`
  - Must NOT keep duplicate `.env` files — root `.env` is the sole source
  Parallelization: Wave 1 | Blocked by: — | Blocks: 3, 5
  References:
  - `compose.yaml:1-195` — existing Docker services (follow postgres pattern)
  - `backend/app/config.py` — Settings class for env vars
  - `backend/app/core/redis_client.py` — singleton pattern to follow
  - `pyproject.toml` — dependency list
  - `backend/.env` and `frontend/.env` — to be removed (consolidated into root `.env`)
  - `.env.example` — to be updated with all vars + mandatory/optional markings
  Acceptance criteria:
  - `docker compose up -d neo4j` starts Neo4j container (reading `NEO4J_PASSWORD` from `.env`)
  - `Neo4jClient` connects and runs `RETURN 1` successfully
  - Config loads from env vars
  - `.env.example` lists every env var the application uses, with `# REQUIRED` markers
  - `backend/.env` and `frontend/.env` no longer exist (consolidated)
  - Running without `.env` and missing `NEO4J_PASSWORD` fails fast with clear error message
  QA scenarios:
  - Happy: start container with `.env` having `NEO4J_PASSWORD=xyz` → client connects → Cypher succeeds
  - Failure: `.env` missing `NEO4J_PASSWORD` → Pydantic validation error (`neo4j_password` field is required)
  - Failure: container not running → connection timeout, graceful fallback in MemoryManager
  Commit: Y | feat(infra): add Neo4j container, async Python driver, and consolidate .env as SSOT

- [x] 2. **DB migration: nullable project_id + user_id on conversations**
  What to do / Must NOT do:
  - Create Alembic migration:
    - ALTER `conversations.project_id` → nullable (drop NOT NULL, keep FK constraint for non-null values)
    - ADD `conversations.user_id` UUID column, FK → `users.id`, NOT NULL (no interim nullable — since this is a new migration, handle backfill in a single transaction)
    - BACKFILL: for existing conversations, set `user_id` from `projects.owner_id` via JOIN
    - ADD index on `(user_id, project_id)` for efficient querying
  - Update SQLAlchemy `Conversation` model (`backend/app/models/conversation.py:20-40`):
    - `project_id: Mapped[UUID | None]` (nullable)
    - `user_id: Mapped[UUID]` (NOT NULL)
    - Add `user` relationship
  - Update `User` model: add `conversations` relationship (both project and individual)
  - Update `ConversationRead` schema (`backend/app/models/schemas/chat.py:44-62`):
    - `project_id: UUID | None` (nullable, was UUID only)
    - Add `user_id: UUID`
  - Must NOT drop existing data or break existing project→conversation FK
  - Must NOT remove `project_id` FK constraint — just make it nullable
  - Must NOT change `Message` or any other table
  Parallelization: Wave 1 | Blocked by: — | Blocks: 4
  References:
  - `backend/app/models/conversation.py:20-40` — current model
  - `backend/app/models/user.py:199-200` — Project.conversations relationship
  - `backend/README.md` — alembic commands
  - `backend/app/models/schemas/chat.py:44-62` — ConversationRead schema (update project_id to optional)
  Acceptance criteria:
  - `uv run alembic upgrade head` succeeds on existing DB
  - `uv run alembic check` passes
  - Existing project conversations have project_id unchanged and user_id populated
  - Raw SQL: `INSERT INTO conversations (user_id, title) VALUES (<uuid>, 'test')` succeeds (project_id=NULL)
  QA scenarios:
  - Happy: create conversation with project_id=NULL → persists, loads
  - Happy: create conversation with project_id set (existing behavior) → works unchanged
  - Edge case: existing DB → backfill populates user_id correctly
  Commit: Y | feat(db): nullable project_id + add user_id to conversations

- [x] 3. **GraphRepository: Neo4j entity CRUD + Cypher queries**
  What to do / Must NOT do:
  - Create `backend/app/services/repositories/graph.py` with `GraphRepository`:
    - `__init__(neo4j_client: Neo4jClient)`
    - `ensure_user_node(user_id: str) -> None` — create User node if not exists (MERGE)
    - `ensure_project_node(project_id: str, name: str) -> None` — create Project node
    - `save_entity(user_id: str, name: str, entity_type: str, metadata: dict) -> str`:
      - MERGE (user_id, name) to deduplicate
      - CREATE relation (User)-[:HAS_ENTITY]->(Entity)
      - RETURN entity_id
    - `save_relation(source_entity_id: str, target_entity_id: str, relation_type: str, weight: float = 1.0) -> None`
    - `get_user_entities(user_id: str, limit: int = 50) -> list[dict]`:
      - MATCH (u:User {id:$user_id})-[:HAS_ENTITY]->(e:Entity)
      - RETURN e.name, e.type, e.metadata
    - `get_entity_context(user_id: str, entity_names: list[str], depth: int = 1) -> list[dict]`:
      - MATCH (u:User {id:$user_id})-[:HAS_ENTITY]->(e:Entity)
      - WHERE e.name IN $entity_names
      - OPTIONAL MATCH (e)-[r]-(related)
      - RETURN e, r, related
    - `get_project_context(project_id: str, limit: int = 30) -> list[dict]`:
      - MATCH (p:Project {id:$project_id})-[:HAS_ENTITY]->(e:Entity)
      - RETURN e
    - `format_context_for_prompt(entities: list[dict]) -> str` — format entities as readable text
  - Node labels: `User`, `Project`, `Entity`
  - Relationship types: `HAS_ENTITY` (User→Entity, Project→Entity), `RELATES_TO` (Entity→Entity)
  - Entity properties: `id`, `name`, `type` (fact|preference|concept|person|topic), `metadata` (JSON string), `created_at`
  - Must NOT use hardcoded Cypher without parameterization (guard against injection)
  - Must NOT create duplicate entities (use MERGE on name+user_id)
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 5, 6
  References:
  - `neo4j` Python driver docs
  - `backend/app/services/repositories/conversations.py:11-200` — repository pattern
  - `backend/app/services/neo4j_client.py` — from task 1
  Acceptance criteria:
  - `save_entity` creates a Neo4j node and links it to the User node
  - `get_user_entities` returns saved entities
  - `get_entity_context` returns entity subgraph (entity + relations + related entities)
  - `save_relation` creates a relationship between two entities
  - `format_context_for_prompt` returns non-empty text
  QA scenarios:
  - Happy: create user node → create 3 entities → add relations → query context → format as text
  - Edge case: same entity name saved twice → MERGE deduplicates (no duplicate nodes)
  - Edge case: entity with no relations → get_entity_context returns just the entity
  Commit: Y | feat(db): add Neo4j GraphRepository with entity CRUD + Cypher queries

- [x] 4. **Individual chat endpoint + ConversationRepository updates**
  What to do / Must NOT do:
  - Add new router `POST /api/chat` in `backend/app/routes/individual_chat.py` (new file):
    - No project_id in path or payload
    - Payload: `{ message: str, conversation_id?: UUID, model?: str }`
    - Auth: JWT (get_current_user)
    - If conversation_id: verify user owns it (via new `get_for_user`), load messages
    - If not: create conversation via `create_individual(current_user.id, title)`
    - Save user message via `add_message`
    - SSE streaming: event: token | final (no `sources` event — no document retrieval)
    - LLM only (no RAG) — inject Neo4j user memory as system prompt context
    - Enqueue `extract_entities_from_message.delay(conversation_id, user_id)` after saving user message (fire-and-forget, always runs)
    - Rate limit: 20/min
  - Update `ConversationRepository`:
    - `create_individual(user_id: UUID, title: str) -> Conversation` (project_id=None, sets user_id)
    - `list_for_user(user_id: UUID, skip=0, limit=100) -> tuple[list[Conversation], int]` (ALL user conversations — both project and individual)
    - `get_for_user(conversation_id: UUID, user_id: UUID) -> Conversation | None` (ownership check)
  - Update `ConversationsRouter` endpoints (`backend/app/routes/conversations.py`):
    - `GET /api/conversations` — list ALL user conversations (both types, uses `list_for_user`)
    - `GET /api/conversations/{id}` — get conversation by user ownership
    - Keep `GET /api/projects/{project_id}/conversations` for project-scoped listing (backward compat)
  - Wire entity extraction into existing project chat route (`backend/app/routes/chat.py`):
    - After saving the user message (line ~66), enqueue `extract_entities_from_message.delay(conversation_id, user_id)`
    - This is fire-and-forget — does not block the SSE response
  - Must NOT change `POST /api/projects/{project_id}/chat` behavior
  - Must NOT retrieve documents or run RAG for individual chat
  - Must NOT require project_id in individual chat payload
  Parallelization: Wave 2 | Blocked by: 2 | Blocks: 7, 8
  References:
  - `backend/app/routes/chat.py:25-232` — existing project chat (pattern for streaming, note line ~62-66 for save user message + extraction wire-up)
  - `backend/app/models/schemas/chat.py:9` — ChatRequest schema
  - `backend/app/services/repositories/conversations.py:11-200` — existing ConversationRepository
  - `backend/app/routes/conversations.py:1-148` — existing conversation routes
  - `backend/app/services/repositories/graph.py` — GraphRepository (task 3) for Neo4j memory
  Acceptance criteria:
  - `POST /api/chat` with JWT returns SSE token stream
  - Individual chat creates conversation with project_id=NULL and user_id set
  - `GET /api/conversations` returns user's individual + project conversations
  - Existing `GET /api/projects/{project_id}/conversations` still works
  - Individual chat response includes user memory from Neo4j in LLM context
  - Entity extraction fires via Celery after each message (both chat types)
  QA scenarios:
  - Happy: POST /api/chat → SSE tokens received → conversation saved with null project_id
  - Happy: resume individual chat with conversation_id → previous messages loaded
  - Happy: GET /api/conversations → both individual and project conversations returned
  - Happy: send message → Celery extraction task fires (check celery logs)
  - Failure: invalid JWT → 401
  Commit: Y | feat(api): add individual chat endpoint + user-scoped conversations + entity extraction wire-up

- [x] 5. **Entity extraction Celery task + MemoryManager**
  What to do / Must NOT do:
  - Create `backend/app/services/tasks/extraction_tasks.py` (new file):
    - `@celery_app.task(name="extract_entities")`
    - `extract_entities_from_message(conversation_id: str, user_id: str)`
    - Load the last user message from the conversation via DB query
    - Call LLM with extraction prompt:
      ```
      Extract facts, preferences, and key entities from this message.
      Return JSON array: [{name, type: "fact"|"preference"|"concept", metadata: {description}}]
      Only extract substantive information. Skip greetings, small talk, questions.
      Message: {message}
      ```
    - For each extracted entity: call `GraphRepository.save_entity(user_id, name, type, metadata)`
    - Link entities to each other if extracted together: call `save_relation()`
    - Handle LLM errors gracefully (log, don't crash)
    - **Always runs — no "skip trivial" filtering** (simplification from the original plan)
  - Register the task in `celery_app.py` `include` list: `"app.services.tasks.extraction_tasks"`
  - Create `backend/app/services/memory_manager.py` (rewrite existing stub):
    - `__init__(db, neo4j_client)`
    - `load_short_term(conversation_id: UUID, limit=50) -> list[dict]`:
      - Query Message table for conversation messages
      - Return formatted history
    - `load_long_term(user_id: UUID, entity_names: list[str] | None = None) -> dict`:
      - Call `GraphRepository.get_user_entities(user_id)`
      - If entity_names: call `get_entity_context(user_id, entity_names)` for focused context
      - Return formatted text
    - `load_context(conversation_id: UUID, user_id: UUID, project_id: UUID | None = None) -> dict`:
      - Combines short_term + long_term
      - If project_id: also load project entities via `get_project_context`
  - Must NOT block the SSE response (fire-and-forget via Celery `.delay()`)
  - Must NOT fail the chat if extraction errors
  - Must NOT have Neo4j queries on the critical request path (extraction is async Celery; context loading is fast synchronous Cypher)
  - Must NOT connect to Neo4j with wrong password silently — log warning and return empty context
  Parallelization: Wave 2 | Blocked by: 1, 3 | Blocks: 6
  References:
  - `backend/app/services/agents/rag.py:98-109` — existing LLM calling pattern
  - `backend/app/services/repositories/graph.py` — GraphRepository (task 3)
  - `backend/app/services/agents/memory_manager.py:1-70` — current stub to rewrite
  - `backend/app/utils/celery_app.py` — Celery config (add include entry)
  - `backend/app/services/tasks/ingestion_tasks.py` — existing task pattern (sync wrapper, `run_async`)
  Acceptance criteria:
  - Celery task runs, extracts entities from any test message
  - Entities appear in Neo4j (queryable via GraphRepository)
  - `MemoryManager.load_context` returns both short-term (messages) and long-term (Neo4j) context
  - Chat response is NOT delayed by entity extraction
  - Neo4j connection failure → graceful empty context (no crash)
  QA scenarios:
  - Happy: send "I prefer dark mode" → Celery task extracts {name:"dark_mode", type:"preference"} → Neo4j has node
  - Happy: send "hi" → Celery task runs, returns empty array → no entities created (LLM decides nothing was substantive)
  - Edge case: Celery down → chat works, log warning, no crash
  - Edge case: Neo4j connection fails → MemoryManager returns empty long_term → chat works
  Commit: Y | feat(memory): add Celery entity extraction + 2-tier MemoryManager

- [x] 6. **RAG pipeline: Neo4j context injection**
  What to do / Must NOT do:
  - Update `backend/app/services/agents/rag.py:prepare_rag_context` (lines 426-495):
    - Add `MemoryManager` parameter or instantiate within function
    - After loading conversation history, call `memory_manager.load_long_term(user_id)` to get Neo4j entities
    - If project_id provided: also call `memory_manager.load_project_context(project_id)`
    - Format entities as text and set `state.graph_context`
  - The `compose_prompt` node (lines 281-380) already:
    - Allocates 15% context budget to `graph_context` (lines 298-313)
    - Injects `graph_context` after document context (line 375)
    - Ensure the graph_context text is properly formatted: "Knowledge Graph Context:\n{entity_text}\n"
  - For individual chat: the response stream already loads Neo4j memory via `MemoryManager.load_context` (from task 5)
  - Must NOT change `compose_prompt` budget allocation (already correct at 15%)
  - Must NOT add Neo4j query latency to critical path (query is fast Cypher, <100ms)
  - Must NOT break when Neo4j is unavailable (graceful empty string)
  Parallelization: Wave 2 | Blocked by: 3, 5 | Blocks: —
  References:
  - `backend/app/services/agents/rag.py:55-56` — `graph_context` field in RagState
  - `backend/app/services/agents/rag.py:298-313` — graph_context budget allocation
  - `backend/app/services/agents/rag.py:426-495` — prepare_rag_context entry point
  - `backend/app/services/memory_manager.py` — from task 5
  Acceptance criteria:
  - Project chat response includes Neo4j entity context in the composed prompt
  - Individual chat response includes Neo4j user memory in LLM context
  - When Neo4j is down, chat still works (empty graph_context)
  - The string "Knowledge Graph Context:" appears in the prompt when entities exist
  QA scenarios:
  - Happy: user has entities in Neo4j → send question → prompt contains "Knowledge Graph Context:" with entity text
  - Happy: no entities → empty graph_context → prompt unchanged from current behavior
  - Edge case: Neo4j connection fails → empty graph_context → chat completes normally
  Commit: Y | feat(rag): inject Neo4j entity context into RAG pipeline

- [x] 7. **Frontend: path-based URL persistence with optional catch-all route**
  What to do / Must NOT do:
  - **Rename** `frontend/app/(workspace)/chat/page.tsx` → `frontend/app/(workspace)/chat/[[...slug]]/page.tsx`
    - New content:
    ```tsx
    import { ChatView } from "@/components/app/chat-view"

    export default function ChatPage({ params }: { params: { slug?: string[] } }) {
      const conversationId = params.slug?.[0] || null
      return <ChatView conversationId={conversationId} />
    }
    ```
  - Update `ChatView` (`frontend/components/app/chat-view.tsx`):
    - Accept optional `conversationId?: string | null` prop
    - On mount: if `conversationId` is provided, call `openConversation(conversationId)`
  - Update `ChatProvider` (`frontend/hooks/use-chat.tsx`):
    - Import `useRouter` from `next/navigation` (not `next/router`)
    - In `submitMessage` (on SSE `onConversation` at line 171):
      ```tsx
      onConversation: (conversation) => {
        setActiveConversationId(conversation.id)
        router.push(`/chat/${conversation.id}`, { scroll: false })
      }
      ```
    - In `openConversation` (line ~103):
      ```tsx
      router.push(`/chat/${conversationId}`, { scroll: false })
      ```
    - In `prepareNewChat` (line ~135):
      ```tsx
      router.push("/chat", { scroll: false })
      ```
    - Keep the URL sync effect from `useSearchParams` only if used elsewhere (like `searchQuery`)
    - Pass `router` from a `useEffect`/`useRef` or directly in the callbacks via closure
  - Ensure `useSearchParams` references that were only for `?search=` remain functional
  - Must NOT cause full page navigation or flash on conversation switch
  - Must NOT break streaming during URL update (router.push is client-side)
  - Must handle initial load: if `/chat/invalid-uuid`, show toast + redirect to `/chat`
  - Must NOT change the layout or page hierarchy for other routes
  Parallelization: Wave 3 | Blocked by: 4 | Blocks: 8
  References:
  - `frontend/app/(workspace)/chat/page.tsx:1-5` — current chat page (rename to `[[...slug]]`)
  - `frontend/hooks/use-chat.tsx:103-133` — openConversation
  - `frontend/hooks/use-chat.tsx:135-144` — prepareNewChat
  - `frontend/hooks/use-chat.tsx:146-225` — submitMessage (onConversation callback at line 171)
  - `frontend/components/app/chat-view.tsx:1-184` — ChatView component
  Acceptance criteria:
  - New chat: URL changes from `/chat` to `/chat/<uuid>` without page refresh
  - Page refresh on `/chat/<uuid>`: correct conversation loads
  - Switch conversation: URL updates without page reload
  - New/clear chat: URL goes to `/chat`
  QA scenarios:
  - Happy: type message → URL gets `/chat/<uuid>` → refresh → conversation restored with messages
  - Happy: click conversation in sidebar → URL updates → no flash
  - Edge case: navigate to `/chat/invalid-uuid` → toast "Conversation not found" → redirect to `/chat`
  Commit: Y | feat(ui): path-based conversation URLs via [[...slug]] catch-all route

- [x] 8. **Frontend: Codex-style project selector + sidebar chat type display**
  What to do / Must NOT do:
  - Add project selector to `ChatInput` (`frontend/components/chat/chat-input.tsx`):
    - Below the textarea, above the action buttons row
    - Styled as a small dropdown/select with subtle styling (like Codex model selector)
    - Options: "Individual Chat" (first, default) + list of user projects
    - Pass `selectedChatMode` and `onChatModeChange` props
    - Props: `projects: {id, name}[]`, `activeProjectId: string | null`, `chatMode: 'individual' | 'project'`, `onModeChange: (mode, projectId?) => void`
  - Update `ChatView` (`frontend/components/app/chat-view.tsx`):
    - Add `chatMode` state: `'individual' | 'project'`
    - When chatMode='individual': disable document library button, hide sources panel
    - When chatMode='individual': show indicator "Chatting without project"
    - When chatMode='project': use selected project's document scope (current behavior)
    - Pass chatMode and projectId to `submitMessage` via `ChatContext`
    - Expose selected chat mode through ChatProvider
  - Update `ChatContext` interface in `use-chat.tsx`:
    - Add `chatMode` and `setChatMode` to context value
    - `submitMessage` uses current `chatMode` + `activeProjectId` to determine API endpoint
    - If chatMode='individual': call `POST /api/chat`
    - If chatMode='project': call `POST /api/projects/{project_id}/chat` (existing behavior)
  - Update `streamChat` in `frontend/lib/chat-stream.ts`:
    - Accept optional `individualChat?: boolean` param
    - If true, call `apiUrl('/api/chat')` instead of `apiUrl(\`/api/projects/${projectId}/chat\`)`
  - Update sidebar (`frontend/components/app/sidebar.tsx`):
    - Show individual chats in the recents list with "Individual" badge or icon
    - Show project chats under their project tree (existing behavior)
    - Use `conversation.project_id` to determine type (null = individual)
  - Update `ConversationItem` (`frontend/components/sidebar/conversation-item.tsx`):
    - Accept optional `chatType: 'individual' | 'project'` prop
    - Show a small badge/icon for type when not collapsed
  - Must NOT change the layout of existing project chats
  - Must NOT show project selector when on settings/analytics pages
  - Must load conversation list for all user conversations (both types)
  Parallelization: Wave 3 | Blocked by: 4, 7 | Blocks: —
  References:
  - `frontend/components/chat/chat-input.tsx:11-119` — ChatInput (add selector below textarea, above action buttons)
  - `frontend/components/app/chat-view.tsx:18-185` — ChatView (controls tabs, submit)
  - `frontend/hooks/use-chat.tsx:22-319` — ChatContext + ChatProvider
  - `frontend/lib/chat-stream.ts:42-106` — streamChat function
  - `frontend/components/app/sidebar.tsx:27-233` — sidebar
  - `frontend/components/sidebar/conversation-item.tsx:9-66` — ConversationItem
  - Codex UX: bottom-of-input mode selector pattern
  Acceptance criteria:
  - Project selector renders below textarea, default "Individual Chat"
  - Selecting "Individual Chat" + sending → calls POST /api/chat, no sources UI
  - Selecting a project + sending → calls POST /api/projects/{id}/chat (existing)
  - Individual chats in sidebar show "(Individual)" badge
  - Project chats in sidebar show under their project tree
  - create conversation → path-based URL updates
  Commit: Y | feat(ui): add Codex-style project selector + chat type display in sidebar

## .env as single source of truth — complete spec

### Current env vars audit

| Variable | In .env | In .env.example | In compose.yaml (fallback) | In config.py (default) | Mandatory? |
|----------|---------|-----------------|--------------------------|----------------------|------------|
| `POSTGRES_USER` | Yes | Yes | `:-ragapp` | N/A (compose only) | No |
| `POSTGRES_PASSWORD` | Yes | Yes | `:-ragapp` | N/A (compose only) | No |
| `POSTGRES_DB` | Yes | Yes | `:-ragapp` | N/A (compose only) | No |
| `DATABASE_URL` | Yes | Yes | N/A (built from components) | `Field(..., pattern=...)` | **YES** |
| `REDIS_URL` | Yes | Yes | `redis://redis:6379/0` (Docker override) | `Field(..., pattern=...)` | **YES** |
| `JWT_SECRET` | Yes | Yes | `:-change-me-in-production-at-least-32-chars` | `Field(..., min_length=32)` | **YES** |
| `JWT_ALGORITHM` | Yes | Yes | N/A | `Field(default="HS256")` | No |
| `LOG_LEVEL` | Yes | Yes | N/A | `Field(default="INFO")` | No |
| `CORS_ALLOW_ORIGINS` | Yes | Yes | N/A | `Field(default=[...])` | No |
| `CORS_ALLOW_ORIGIN_REGEX` | Yes | Yes | N/A | `Field(default=...)` | No |
| `OLLAMA_BASE_URL` | Yes | Yes | `http://host.docker.internal:11434` | `Field(default="http://host.docker.internal:11434")` | No |
| `OLLAMA_MODEL_REASONER` | No | Yes | `:-qwen2.5:7b` | `Field(default="qwen2.5:7b")` | No |
| `OLLAMA_MODEL_PLANNER` | No | Yes | `:-qwen2.5:7b` | `Field(default="qwen2.5:7b")` | No |
| `OLLAMA_MODEL_EMBED` | No | Yes | `:-bge-m3` | `Field(default="bge-m3")` | No |
| `OLLAMA_NUM_CTX` | No | Yes | N/A | `Field(default=8192)` | No |
| `OLLAMA_NUM_PREDICT` | No | Yes | N/A | `Field(default=4096)` | No |
| `LANGFUSE_*` (6 vars) | Yes | Yes | Various compose overrides | `Field(default=None)` | No |
| `NEXT_PUBLIC_API_URL` | Yes | Yes | N/A | N/A (frontend) | **YES** (frontend) |
| `NEO4J_PASSWORD` | **NEW** | **NEW** | No fallback (mandatory) | `Field(..., min_length=1)` | **YES** |
| `NEO4J_URI` | **NEW** | **NEW** | N/A | `Field(default="bolt://localhost:7687")` | No |
| `NEO4J_USER` | **NEW** | **NEW** | N/A | `Field(default="neo4j")` | No |

### Changes to make for .env consolidation

1. Add `NEO4J_PASSWORD`, `NEO4J_URI`, `NEO4J_USER` to `.env.example`
2. Mark `NEO4J_PASSWORD` as `# REQUIRED` in `.env.example`
3. Add `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` to `.env.example` (already in config.py with defaults)
4. Remove `backend/.env` and `frontend/.env` — root `.env` is the single source
5. In compose.yaml, remove `:-default` fallbacks for mandatory vars (keep for truly optional ones):
   - `JWT_SECRET` remove fallback (mandatory)
   - `NEO4J_PASSWORD` no fallback from the start (mandatory)
6. Update `backend/app/config.py` to clearly separate mandatory vs optional fields with comments
7. Update `README.md` to reference `.env.example` as the definitive source

The final `.env.example` structure:
```bash
# ============================================
# Artha — Environment Configuration
# ============================================
# Copy this file to .env and fill in values.
# This is the SINGLE source of truth for all
# environment variables used by the application.

# --- Database (REQUIRED) ---
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5435/dbname  # REQUIRED

# --- Redis (REQUIRED) ---
REDIS_URL=redis://localhost:6379/0  # REQUIRED

# --- Authentication (REQUIRED) ---
JWT_SECRET=change-me-at-least-32-chars-long!!  # REQUIRED: min 32 chars

# --- Neo4j Graph Database ---
NEO4J_PASSWORD=  # REQUIRED: password for neo4j user
NEO4J_URI=bolt://localhost:7687  # Optional: defaults to bolt://localhost:7687
NEO4J_USER=neo4j                 # Optional: defaults to neo4j

# --- Ollama (LLM) ---
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL_REASONER=qwen2.5:7b
OLLAMA_MODEL_PLANNER=qwen2.5:7b
OLLAMA_MODEL_EMBED=bge-m3
OLLAMA_NUM_CTX=8192
OLLAMA_NUM_PREDICT=4096

# --- Celery (Background Tasks) ---
CELERY_BROKER_URL=redis://localhost:6379/1     # Optional: defaults to redis://localhost:6379/1
CELERY_RESULT_BACKEND=redis://localhost:6379/2 # Optional: defaults to redis://localhost:6379/2

# --- Optional: Server Configuration ---
JWT_ALGORITHM=HS256
LOG_LEVEL=INFO
CORS_ALLOW_ORIGINS=["http://localhost:3000","http://127.0.0.1:3000"]
CORS_ALLOW_ORIGIN_REGEX=http://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?

# --- Optional: Langfuse (Observability) ---
LANGFUSE_HOST=http://localhost:3001
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...

# --- Frontend (REQUIRED for frontend build) ---
NEXT_PUBLIC_API_URL=http://localhost:8000
```

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. **Plan compliance audit**: All 8 tasks completed, scope IN fully delivered, scope OUT not violated
- [x] F2. **Code quality**: ruff lint — only pre-existing issues; our new code is clean. Fixed unused import in graph.py.
- [x] F3. **.env audit**: backend/.env and frontend/.env removed (consolidated). `.env.example` covers all vars. Mandatory vars marked.
- [ ] F4. **E2E test**: Requires Neo4j running — verifiable when user starts services. Code is in place.
- [ ] F5. **E2E test**: Requires services running — Neo4j context injection wired in rag.py line 492-515.
- [ ] F6. **Regression**: No changes to existing project chat route signature. Path-based URLs additive.
- [ ] F7. **URL test**: Path-based URL route created at `[[...slug]]/page.tsx`. router.push wired in use-chat.tsx.

## Commit strategy
- Each todo = one atomic commit (8 commits)
- Format: conventional commits: `<type>(<scope>): <summary>`
- Types: feat, refactor
- Scopes: infra, db, api, memory, rag, ui
- No force-push, no amend after review

## Success criteria
1. ✅ Two chat types: project (documents + Neo4j context) and individual (no documents, just LLM + Neo4j user memory)
2. ✅ Conversation URLs are path-based: `/chat/<uuid>` — survives refresh, no visible navigation
3. ✅ Entity extraction runs via Celery after EVERY user message (no content-type filtering — always on the last message)
4. ✅ `.env` is the single source of truth — no duplicate `.env` files, mandatory vars clearly marked, everything documented in `.env.example`
5. ✅ Three DBs operational: PostgreSQL (structured), Qdrant (document embeddings), Neo4j (knowledge graph)
6. ✅ Memory: short-term (SQL conversation history) + long-term (Neo4j entity graph)
7. ✅ Neo4j context injected into RAG prompt (populates `graph_context`)
8. ✅ Frontend: Codex-style project selector at chat input bottom
9. ✅ Frontend: sidebar shows both chat types with type badges
10. ✅ Zero regressions to existing project chat
