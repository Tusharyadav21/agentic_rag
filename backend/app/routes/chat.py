import logging
import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import MessageRole
from app.models.schemas.chat import ChatRequest, FeedbackRequest
from app.models.user import User
from app.services.agents.runtime import AgentRuntime
from app.services.langfuse_client import get_langfuse
from app.services.llm_factory import get_llm_for_user
from app.services.repositories.conversations import ConversationRepository
from app.services.repositories.messages import MessageRepository
from app.services.repositories.projects import ProjectRepository
from app.utils.database import get_db
from app.utils.dependencies import get_current_user
from app.utils.langfuse_utils import safe_end, safe_generation, safe_span, safe_trace_update
from app.utils.rate_limit import limiter
from app.utils.sse import sse_event

router = APIRouter(prefix="/api/projects/{project_id}/chat", tags=["chat"])
logger = logging.getLogger(__name__)


@router.post("")
@limiter.limit("20/minute")
async def chat(
    request: Request,
    project_id: UUID,
    payload: ChatRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    target_model = payload.model
    llm_client = await get_llm_for_user(current_user.id, db)
    lf = get_langfuse()

    try:
        project = await ProjectRepository(db).get_for_user(project_id, current_user.id)
        if project is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

        conversation_repository = ConversationRepository(db)
        if payload.conversation_id:
            conversation = await conversation_repository.get_for_project(
                payload.conversation_id,
                project_id,
            )
            if conversation is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Conversation not found",
                )
        else:
            title = payload.message.strip()[:80] or "New conversation"
            conversation = await conversation_repository.create(project_id, title, current_user.id)

        await conversation_repository.add_message(
            conversation.id,
            MessageRole.USER,
            payload.message,
        )

        # Fire-and-forget entity extraction (always runs after user message)
        try:
            from app.utils.celery_app import celery_app

            celery_app.send_task(
                "extract_entities",
                args=[str(conversation.id), str(current_user.id)],
            )
        except Exception as exc:
            logger.warning("Failed to enqueue entity extraction: %s", exc)

        trace = None
        if lf is not None:
            try:
                trace = lf.trace(
                    name="rag-chat",
                    session_id=str(conversation.id),
                    user_id=str(current_user.id),
                    input=payload.message,
                    metadata={
                        "project_id": str(project_id),
                        "project_name": project.name,
                        "model": target_model,
                        "num_ctx": payload.num_ctx,
                        "num_predict": payload.num_predict,
                    },
                    tags=["rag", "chat"],
                )
            except Exception as lf_exc:
                logger.warning("Langfuse trace creation failed: %s", lf_exc)

        async def stream():
            assistant_content: list[str] = []
            rag_span = None
            generation_span = None
            t_rag_start = time.perf_counter()

            try:
                yield sse_event(
                    "conversation",
                    {
                        "id": str(conversation.id),
                        "project_id": str(project_id),
                        "title": conversation.title,
                    },
                )

                rag_span = safe_span(trace, name="rag-retrieval", input=payload.message)
                node_span = None

                rag_state = {}
                runtime = AgentRuntime(db, llm_client)
                runtime_error: str | None = None
                async for chunk in runtime.execute(
                    user_input=payload.message,
                    conversation_id=conversation.id,
                    workspace_id=project_id,
                    project_id=project_id,
                    trace_id=trace.id if trace else None,
                    document_ids=payload.document_ids,
                    model_name=target_model,
                    num_ctx=payload.num_ctx,
                    num_predict=payload.num_predict,
                ):
                    node_name = list(chunk.keys())[0]

                    # Detect runtime errors (e.g. no active agent) and short-circuit
                    if node_name == "error":
                        runtime_error = chunk[node_name]
                        safe_end(node_span)
                        break

                    # End previous node span before starting the next
                    safe_end(node_span)
                    node_span = safe_span(trace, name=f"rag-node-{node_name}")

                    yield sse_event("node", node_name)
                    if isinstance(chunk[node_name], dict):
                        rag_state.update(chunk[node_name])

                safe_end(node_span)
                if runtime_error:
                    yield sse_event("error", {"detail": runtime_error})
                    return

                rag_sources = rag_state.get("sources", [])
                sources_dict = [s.model_dump() for s in rag_sources] if rag_sources else []
                if sources_dict:
                    yield sse_event("sources", sources_dict)

                safe_end(
                    rag_span,
                    output={
                        "sources_count": len(rag_sources),
                        "sources": [
                            {"filename": s.filename, "score": s.score} for s in rag_sources
                        ],
                    },
                    metadata={
                        "latency_s": round(time.perf_counter() - t_rag_start, 3),
                        "pre_rerank_count": rag_state.get("pre_rerank_count", 0),
                        "post_rerank_count": rag_state.get("post_rerank_count", 0),
                        "top_reranker_score": rag_state.get("top_reranker_score"),
                        "quality_gate_dropped": rag_state.get("quality_gate_dropped", False),
                        "hyde_triggered": rag_state.get("hyde_run", False),
                    },
                )

                rag_prompt = rag_state.get("prompt", "")
                t_gen_start = time.perf_counter()
                generation_span = safe_generation(
                    trace,
                    name="ollama-stream",
                    model=target_model or "unknown",
                    input=rag_prompt,
                    model_parameters={
                        "num_ctx": payload.num_ctx,
                        "num_predict": payload.num_predict,
                    },
                )

                async for token in llm_client.stream_generate(
                    rag_prompt,
                    model_name=target_model,
                    num_ctx=payload.num_ctx,
                    num_predict=payload.num_predict,
                ):
                    assistant_content.append(token)
                    yield sse_event("token", token)

                content = "".join(assistant_content).strip()
                message = await conversation_repository.add_message(
                    conversation.id,
                    MessageRole.ASSISTANT,
                    content or "I could not generate a response.",
                    {"sources": [s.model_dump() for s in rag_sources], "model": target_model},
                )
                final_data = {"message_id": str(message.id), "content": message.content}
                yield sse_event("final", final_data)

                safe_end(
                    generation_span,
                    output=content,
                    usage={"output": len(assistant_content)},
                    metadata={"latency_s": round(time.perf_counter() - t_gen_start, 3)},
                )
                safe_trace_update(trace, lf, output=content)

            except Exception as exc:
                await db.rollback()
                logger.exception("Stream error after %d tokens", len(assistant_content))
                if assistant_content:
                    partial = "".join(assistant_content).strip()
                    try:
                        await conversation_repository.add_message(
                            conversation.id,
                            MessageRole.ASSISTANT,
                            partial + "\n\n*[Response interrupted due to an error]*",
                            {"sources": [], "truncated": True},
                        )
                    except Exception:
                        pass

                for span in (generation_span, rag_span):
                    safe_end(span, metadata={"error": str(exc)})
                safe_trace_update(trace, lf, output=f"ERROR: {exc}", metadata={"error": True})

                yield sse_event("error", {"detail": str(exc)})
            finally:
                await llm_client.close()

        return StreamingResponse(stream(), media_type="text/event-stream")
    except Exception:
        await llm_client.close()
        raise


@router.post("/messages/{message_id}/feedback")
async def post_message_feedback(
    project_id: UUID,
    message_id: UUID,
    payload: FeedbackRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, str]:
    project = await ProjectRepository(db).get_for_user(project_id, current_user.id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    repository = MessageRepository(db)
    message = await repository.get_for_project(message_id, project_id)
    if message is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")

    await repository.update_feedback(message, payload.rating, payload.comment)

    lf = get_langfuse()
    if lf is not None:
        try:
            lf.score(
                name="user-feedback",
                value=1.0 if payload.rating == "up" else 0.0,
                comment=payload.comment,
                trace_id=None,
            )
        except Exception:
            pass

    return {"status": "success"}
