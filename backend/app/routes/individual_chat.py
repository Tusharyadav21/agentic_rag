"""Individual chat endpoint — no project, no document retrieval, just LLM + Neo4j user memory."""

import logging
import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import MessageRole
from app.models.schemas.chat import ChatRequest
from app.models.user import User
from app.services.agents.memory_manager import MemoryManager
from app.services.llm_factory import get_llm_for_user
from app.services.repositories.conversations import ConversationRepository
from app.utils.database import get_db
from app.utils.dependencies import get_current_user
from app.utils.rate_limit import limiter
from app.utils.sse import sse_event

router = APIRouter(prefix="/api/chat", tags=["individual_chat"])
logger = logging.getLogger(__name__)


@router.post("")
@limiter.limit("20/minute")
async def chat(
    request: Request,
    payload: ChatRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    target_model = payload.model
    llm_client = await get_llm_for_user(current_user.id, db)

    conversation_repo = ConversationRepository(db)
    memory_mgr = MemoryManager(db)

    if payload.conversation_id:
        conversation = await conversation_repo.get_for_user(
            payload.conversation_id,
            current_user.id,
        )
        if conversation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found",
            )
    else:
        title = payload.message.strip()[:80] or "New conversation"
        conversation = await conversation_repo.create_individual(current_user.id, title)

    await conversation_repo.add_message(
        conversation.id,
        MessageRole.USER,
        payload.message,
    )

    # Fire-and-forget entity extraction (always runs after user message)
    try:
        from app.utils.celery_app import celery_app
        from app.config import get_settings

        get_settings()  # ensure settings are loaded
        celery_app.send_task(
            "extract_entities",
            args=[str(conversation.id), str(current_user.id)],
        )
    except Exception as exc:
        logger.warning("Failed to enqueue entity extraction: %s", exc)

    async def stream():
        assistant_content: list[str] = []
        try:
            yield sse_event(
                "conversation",
                {
                    "id": str(conversation.id),
                    "user_id": str(current_user.id),
                    "title": conversation.title,
                },
            )

            # Load Neo4j user memory
            memory_context = await memory_mgr.load_context(
                conversation_id=conversation.id,
                user_id=current_user.id,
            )
            long_term = memory_context.get("long_term", "")
            short_term = memory_context.get("short_term", [])

            # Build prompt with memory context
            system_parts = [
                "You are a helpful assistant. Answer the user's questions based on your knowledge.",
            ]
            if long_term:
                system_parts.append(f"\n\nUser Context:\n{long_term}")

            conversation_history = ""
            if short_term:
                history_lines = [
                    f"{'User' if m.role == MessageRole.USER else 'Assistant'}: {m.content}"
                    for m in short_term[-10:]
                ]
                conversation_history = "\n".join(history_lines)

            prompt = f"{' '.join(system_parts)}\n\n"
            if conversation_history:
                prompt += f"Previous conversation:\n{conversation_history}\n\n"
            prompt += f"User: {payload.message}\nAssistant:"

            t_gen_start = time.perf_counter()
            async for token in llm_client.stream_generate(
                prompt,
                model_name=target_model,
                num_ctx=payload.num_ctx,
                num_predict=payload.num_predict,
            ):
                assistant_content.append(token)
                yield sse_event("token", token)

            content = "".join(assistant_content).strip()
            message = await conversation_repo.add_message(
                conversation.id,
                MessageRole.ASSISTANT,
                content or "I could not generate a response.",
                {"model": target_model, "chat_type": "individual"},
            )
            final_data = {"message_id": str(message.id), "content": message.content}
            yield sse_event("final", final_data)

            logger.info(
                "Individual chat %s: %d tokens in %.2fs",
                conversation.id,
                len(assistant_content),
                time.perf_counter() - t_gen_start,
            )

        except Exception as exc:
            await db.rollback()
            logger.exception("Stream error in individual chat %s", conversation.id)
            yield sse_event("error", {"detail": str(exc)})
        finally:
            await llm_client.close()

    return StreamingResponse(stream(), media_type="text/event-stream")
