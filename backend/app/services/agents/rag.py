import json
import logging
from collections.abc import AsyncGenerator
from uuid import UUID

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.user import Project, UserMemory
from app.services.agents.memory_manager import MemoryManager
from app.services.llm_client import BaseLLMClient, LiteLLMClient
from app.services.rag_engine import rag_engine
from app.services.repositories.conversations import ConversationRepository
from app.services.repositories.documents import DocumentRepository
from app.services.reranker import Reranker

logger = logging.getLogger(__name__)

settings = get_settings()

_COMPLEX_KEYWORDS = frozenset({
    "compare", "contrast", "analyze", "analyse", "summarize", "summarise",
    "explain", "list", "all", "difference", "between", "versus", "vs",
    "how", "why", "relationship", "impact", "effect", "overview",
})


class Source(BaseModel):
    document_id: str = ""
    filename: str = ""
    content: str = ""
    score: float = 0.0
    parent_content: str | None = None
    image_path: str | None = None
    image_caption: str | None = None


class RagState(BaseModel):
    project_id: UUID = Field(default_factory=UUID)
    question: str = ""
    sources: list[Source] = Field(default_factory=list)
    prompt: str = ""
    history: list[dict] = Field(default_factory=list)
    search_query: str = ""
    document_ids: list[UUID] | None = None
    system_prompt: str | None = None
    chunk_limit: int = 5
    low_confidence: bool = False
    model_name: str | None = None
    num_ctx: int | None = None
    num_predict: int | None = None
    hyde_text: str | None = None
    graph_context: str | None = None
    query_entities: list[str] | None = None
    needs_hyde: bool | None = None
    hyde_run: bool | None = None
    needs_retrieval: bool = True

    # Telemetry fields (populated during graph execution)
    pre_rerank_count: int = 0
    post_rerank_count: int = 0
    top_reranker_score: float | None = None
    quality_gate_dropped: bool = False
    cosine_threshold_dropped: int = 0


DEFAULT_SYSTEM_PROMPT = (
    "You are a careful local-first RAG assistant. Answer using the supplied "
    "project context when it is relevant. Cite sources inline using [1], [2], "
    "etc. If the context is insufficient, say what is missing and answer from "
    "general reasoning only when useful."
)


def _format_history(history: list[dict[str, str]], capitalize_roles: bool = False) -> str:
    if capitalize_roles:
        return "\n".join(
            f"{message['role'].capitalize()}: {message['content']}" for message in history
        )
    return "\n".join(f"{message['role']}: {message['content']}" for message in history)


def _classify_query_complexity(query: str) -> int:
    words = query.lower().split()
    if len(words) <= 8 and not _COMPLEX_KEYWORDS.intersection(words):
        return 3
    if len(words) > 20 or _COMPLEX_KEYWORDS.intersection(words):
        return 6
    return 5


def _estimate_tokens(text: str) -> int:
    return len(text) // 4


async def _summarize_history(messages: list[dict], llm_client: BaseLLMClient) -> str:
    history_str = _format_history(messages, capitalize_roles=True)
    prompt = (
        "Summarize the following conversation in 2-3 sentences, capturing the key topics "
        "and context. Be concise.\n\n"
        f"{history_str}\n\nSummary:"
    )
    try:
        return (await llm_client.generate(prompt)).strip()
    except Exception as exc:
        logger.warning("History summarization failed: %s, using recent messages fallback", exc)
        return history_str[-500:]


def build_rag_graph(db: AsyncSession, llm_client: BaseLLMClient):
    async def analyze_query(state: RagState) -> RagState:
        history_str = _format_history(state.history or [])

        prompt = (
            "You are an expert search query analyzer. "
            "Analyze the user question and conversation history. "
            "Return a JSON object containing:\n"
            "1. 'standalone_query': If the question refers to past context "
            "(e.g. using 'it', 'they', 'the file'), "
            "rewrite it to be a standalone search query. Otherwise, return the original question.\n"
            "2. 'entities': A list of up to 5 key entities "
            "(nouns, systems, acronyms) mentioned in the question "
            "for Graph search. If none, return [].\n"
            "3. 'needs_hyde': Set to true only if the query is conceptual, "
            "comparison-based, or requires an abstract explanation.\n"
            "4. 'needs_retrieval': Set to false if the message is a casual "
            "greeting (hi, hello, hey, thanks), small talk, or a question that "
            "can be answered without any project documents. Set to true if it "
            "asks about project content, documents, or domain knowledge.\n\n"
            f"History:\n{history_str}\n\n"
            f"User Question:\n{state.question}\n\n"
            "Format: JSON object with keys 'standalone_query' (str), "
            "'entities' (list of str), 'needs_hyde' (bool), "
            "'needs_retrieval' (bool)."
        )

        try:
            res = await llm_client.generate(
                prompt,
                model_name=state.model_name,
                num_ctx=state.num_ctx,
                format="json",
            )
            data = json.loads(res)
            search_query = data.get("standalone_query") or state.question
            entities = data.get("entities") or []
            needs_hyde = data.get("needs_hyde", False)
            needs_retrieval = data.get("needs_retrieval", True)
        except Exception as exc:
            logger.warning("Consolidated query analysis failed: %s", exc)
            search_query = state.question
            entities = []
            needs_hyde = False
            needs_retrieval = True

        chunk_limit = _classify_query_complexity(search_query)

        return state.model_copy(update={
            "search_query": search_query,
            "query_entities": entities,
            "needs_hyde": needs_hyde,
            "needs_retrieval": needs_retrieval,
            "chunk_limit": chunk_limit,
        })

    async def hyde_expand(state: RagState) -> RagState:
        if not settings.hyde_enabled:
            return state.model_copy(update={"hyde_text": None, "hyde_run": True})

        n = settings.hyde_variants
        prompt = (
            f"Write {n} concise, plausible 2-3 sentence answers to the question below. "
            "Each answer should take a different perspective or focus on different aspects. "
            "They will be used only as retrieval cues, so prioritise topical keywords. "
            "Do not include preambles.\n\n"
            f"Question: {state.search_query}\n\n"
            f"Return a JSON array of {n} strings, each string is one hypothetical answer."
        )
        try:
            answer = await llm_client.generate(
                prompt,
                model_name=state.model_name,
                num_ctx=state.num_ctx,
                num_predict=512,
                format="json",
            )
            data = json.loads(answer)
            if isinstance(data, list) and len(data) > 0:
                hyde_text = "\n\n".join(data)
            else:
                hyde_text = str(data[0]) if data else None
        except Exception as exc:
            logger.warning("HyDE expansion failed: %s", exc)
            hyde_text = None

        return state.model_copy(update={"hyde_text": hyde_text, "hyde_run": True})

    async def retrieve(state: RagState) -> RagState:
        chunk_limit = state.chunk_limit
        embed_text = state.search_query
        if hyde_text := state.hyde_text:
            embed_text = f"{state.search_query}\n\n{hyde_text}"

        doc_ids_str = [str(did) for did in state.document_ids] if state.document_ids else None
        rows = await rag_engine.retrieve(
            embed_text, 
            project_id=str(state.project_id), 
            document_ids=doc_ids_str,
            limit=chunk_limit * 3
        )
        seen_parents: set[str] = set()
        sources: list[Source] = []
        for doc in rows:
            metadata = doc.get("metadata", {})
            content = doc.get("content", "")
            score = doc.get("score", 0.0)
            parent_content = metadata.get("parent_content")
            
            dedup_key = parent_content if parent_content else content
            if dedup_key in seen_parents:
                continue
            seen_parents.add(dedup_key)

            sources.append(Source(
                document_id=metadata.get("document_id", "unknown"),
                filename=metadata.get("filename", "Document"),
                content=content,
                parent_content=parent_content,
                score=score,
            ))
        return state.model_copy(update={
            "sources": sources,
            "pre_rerank_count": len(sources),
        })

    async def rerank(state: RagState) -> RagState:
        if not state.sources:
            return state

        reranker = Reranker()
        query = state.search_query
        top_k = state.chunk_limit

        contents = [s.content for s in state.sources]
        results = await reranker.rerank(query, contents, top_k=top_k)

        best_score = float(results[0][1]) if results else 0.0
        new_sources: list[Source] = []
        for index, score in results:
            float_score = float(score)
            if float_score < best_score * 0.8:
                continue
            source = state.sources[index].model_copy(update={"score": float_score})
            new_sources.append(source)

        return state.model_copy(update={
            "sources": new_sources,
            "post_rerank_count": len(new_sources),
            "top_reranker_score": best_score,
        })

    async def quality_gate(state: RagState) -> RagState:
        if not state.sources:
            return state.model_copy(update={"low_confidence": True, "quality_gate_dropped": True})
            
        if state.document_ids:
            return state.model_copy(update={"low_confidence": False, "quality_gate_dropped": False})
            
        best_score = state.sources[0].score
        dropped = best_score < settings.relevance_threshold
        return state.model_copy(
            update={
                "low_confidence": dropped,
                "quality_gate_dropped": dropped,
            }
        )

    async def compress(state: RagState) -> RagState:
        if not state.sources or state.low_confidence:
            return state

        compressed: list[Source] = []
        for source in state.sources:
            full_text = source.parent_content or source.content
            compressed.append(source.model_copy(update={"content": full_text}))

        return state.model_copy(update={"sources": compressed})

    def compose_prompt(state: RagState) -> RagState:
        total_ctx = state.num_ctx or 8192
        predict_reserved = state.num_predict or 1024
        prompt_budget = total_ctx - predict_reserved

        # Context layer weights from config — controls budget allocation
        w_chunks = settings.context_weight_chunks    # 0.60
        w_history = settings.context_weight_history   # 0.25
        w_memory = settings.context_weight_memory     # 0.15
        # Reserve 200-token slack for framing tokens
        slack = 200

        system_preamble = state.system_prompt or DEFAULT_SYSTEM_PROMPT
        if _estimate_tokens(system_preamble) > 500:
            system_preamble = system_preamble[:2000]
        system_tokens = _estimate_tokens(system_preamble)

        # --- Graph/memory context (15% budget) ---
        graph_context = ""
        raw_graph = state.graph_context
        graph_budget = int((prompt_budget - system_tokens - slack) * w_memory)
        if raw_graph and graph_budget > 0:
            lines = raw_graph.split("\n")
            budget_graph = []
            running_tokens = 0
            for line in lines:
                line_toks = _estimate_tokens(line)
                if running_tokens + line_toks > graph_budget:
                    break
                budget_graph.append(line)
                running_tokens += line_toks
            if budget_graph:
                graph_context = "\nKnowledge Graph Context:\n" + "\n".join(budget_graph) + "\n"

        # --- History context (25% budget) ---
        history_context = ""
        raw_history = state.history or []
        history_budget = int((prompt_budget - system_tokens - slack) * w_history)
        if raw_history and history_budget > 0:
            budget_history = []
            running_tokens = 0
            for msg in reversed(raw_history):
                msg_str = f"{msg['role'].capitalize()}: {msg['content']}"
                msg_toks = _estimate_tokens(msg_str)
                if running_tokens + msg_toks > history_budget:
                    break
                budget_history.insert(0, msg_str)
                running_tokens += msg_toks
            if budget_history:
                history_context = (
                    "Recent conversation history:\n"
                    + "\n".join(budget_history)
                    + "\n\n"
                )

        used_tokens = (
            system_tokens
            + _estimate_tokens(graph_context)
            + _estimate_tokens(history_context)
            + _estimate_tokens(state.question)
        )
        # — Chunk context (60% of what remains after system/framing) —
        weight_sum = w_chunks + w_history + w_memory
        chunk_budget = max(
            int((prompt_budget - used_tokens - slack) * w_chunks / weight_sum), 600
        )

        low_confidence = state.low_confidence
        if not state.needs_retrieval:
            context = "No project documents were retrieved because the question did not require it. Answer the user's question directly from general knowledge."
        elif low_confidence or not state.sources:
            context = (
                "No sufficiently relevant content found in project documents. "
                "Answer from general knowledge if helpful, or clearly state what's missing."
            )
        else:
            budget_sources = []
            running_tokens = 0
            for index, source in enumerate(state.sources, start=1):
                img_hint = ""
                if source.image_caption:
                    img_hint = f"\n[Image: {source.image_caption}]"
                chunk_str = f"[{index}] {source.filename}{img_hint}\n{source.content}"
                chunk_toks = _estimate_tokens(chunk_str)
                if running_tokens + chunk_toks > chunk_budget:
                    break
                budget_sources.append(chunk_str)
                running_tokens += chunk_toks
            context = (
                "\n\n".join(budget_sources) if budget_sources
                else "No context loaded under budget."
            )

        prompt = (
            f"{system_preamble}\n\n"
            f"Project context:\n{context}\n\n"
            f"{graph_context}\n"
            f"{history_context}"
            f"User question:\n{state.question}\n\n"
            "Assistant answer:"
        )
        return state.model_copy(update={"prompt": prompt})

    def route_after_gate(state: RagState) -> str:
        if state.low_confidence and state.needs_hyde and not state.hyde_run:
            logger.info("RAG Quality Gate failed. Routing to conditional HyDE correction pass.")
            return "hyde_expand"
        return "compress"

    graph = StateGraph(RagState)

    graph.add_node("analyze_query", analyze_query)
    graph.add_node("retrieve", retrieve)
    graph.add_node("rerank", rerank)
    graph.add_node("quality_gate", quality_gate)
    graph.add_node("hyde_expand", hyde_expand)
    graph.add_node("compress", compress)
    graph.add_node("compose_prompt", compose_prompt)

    def route_after_analysis(state: RagState) -> str:
        if not state.needs_retrieval:
            logger.info("Skipping retrieval — casual/non-document query detected.")
            return "compose_prompt"
        return "retrieve"

    graph.set_entry_point("analyze_query")
    graph.add_conditional_edges(
        "analyze_query",
        route_after_analysis,
        {"retrieve": "retrieve", "compose_prompt": "compose_prompt"},
    )
    graph.add_edge("retrieve", "rerank")
    graph.add_edge("rerank", "quality_gate")

    graph.add_conditional_edges(
        "quality_gate",
        route_after_gate,
        {"hyde_expand": "hyde_expand", "compress": "compress"},
    )

    graph.add_edge("hyde_expand", "retrieve")
    graph.add_edge("compress", "compose_prompt")
    graph.add_edge("compose_prompt", END)

    return graph.compile()


async def prepare_rag_context(
    db: AsyncSession,
    project_id: UUID,
    conversation_id: UUID,
    question: str,
    document_ids: list[UUID] | None = None,
    model_name: str | None = None,
    num_ctx: int | None = None,
    num_predict: int | None = None,
    llm_client: BaseLLMClient | None = None,
) -> AsyncGenerator[dict, None]:
    if llm_client is None:
        llm_client = LiteLLMClient()

    project = await db.get(Project, project_id)
    system_prompt = project.system_prompt if project else None

    conversation = await ConversationRepository(db).get_for_project(conversation_id, project_id)
    history: list[dict] = []

    user_id = project.user_id if project else None
    if user_id:
        result = await db.execute(select(UserMemory).where(UserMemory.user_id == user_id))
        memories = result.scalars().all()
        if memories:
            memories_list = [m.content for m in memories]
            try:
                reranker = Reranker()
                scored = await reranker.rerank(question, memories_list, top_k=3)
                relevant_memories = [memories_list[idx] for idx, _ in scored]
            except Exception as e:
                logger.warning("Reranking user memories failed: %s", e)
                relevant_memories = memories_list[:5]

            if relevant_memories:
                memory_str = "\n".join([f"- {m}" for m in relevant_memories])
                content = f"Core User Memories/Preferences:\n{memory_str}"
                history.append({"role": "system", "content": content})

    if conversation and conversation.messages:
        messages = [
            {"role": m.role, "content": m.content}
            for m in conversation.messages[:-1]
        ]
        if len(messages) > settings.history_summarize_at:
            older = messages[:-settings.history_keep_recent]
            recent = messages[-settings.history_keep_recent:]
            summary = await _summarize_history(older, llm_client)
            history.append({"role": "system", "content": f"Summary: {summary}"})
            history.extend(recent)
        else:
            history.extend(messages)

    # Load Neo4j entity context for the prompt
    graph_context: str | None = None
    if user_id:
        try:
            memory_mgr = MemoryManager(db)
            long_term = await memory_mgr.load_long_term(user_id)
            if long_term:
                graph_context = long_term

            # Also load project entities if project context is available
            if project_id:
                project_entities = await memory_mgr.load_context(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    project_id=project_id,
                )
                proj_ctx = project_entities.get("project_entities", "")
                if proj_ctx:
                    graph_context = (
                        f"{graph_context}\n\n{proj_ctx}"
                        if graph_context else proj_ctx
                    )
        except Exception as exc:
            logger.warning("Failed to load Neo4j entity context: %s", exc)

    graph = build_rag_graph(db, llm_client)
    async for chunk in graph.astream(
        RagState(
            project_id=project_id,
            question=question,
            history=history,
            search_query=question,
            graph_context=graph_context,
            document_ids=document_ids,
            system_prompt=system_prompt,
            model_name=model_name,
            num_ctx=num_ctx,
            num_predict=num_predict,
        ),
        config={},
        stream_mode="updates",
    ):
        yield chunk
