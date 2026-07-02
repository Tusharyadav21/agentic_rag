"""Celery tasks for entity extraction from user messages into Neo4j."""

import json
import logging
from uuid import UUID

from sqlalchemy import select

from app.config import get_settings
from app.models.conversation import Conversation, MessageRole
from app.services.llm_client import LiteLLMClient
from app.services.neo4j_client import get_neo4j_client
from app.services.repositories.graph import GraphRepository
from app.utils.celery_app import celery_app
from app.utils.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Helper to run async code inside a sync Celery task."""
    import asyncio

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


async def _extract_async(conversation_id: str, user_id: str) -> None:
    """Extract entities from the last user message and persist to Neo4j."""
    db = None
    try:
        db = AsyncSessionLocal()
        result = await db.execute(
            select(Conversation)
            .where(Conversation.id == UUID(conversation_id))
        )
        conversation = result.scalar_one_or_none()
        if not conversation or not conversation.messages:
            return

        # Get the last user message
        user_messages = [m for m in conversation.messages if m.role == MessageRole.USER]
        if not user_messages:
            return
        last_msg = user_messages[-1]
        message_text = last_msg.content.strip()
        if not message_text:
            return

        # Call LLM to extract entities
        extract_prompt = (
            "Extract facts, preferences, and key entities from this message.\n"
            "Return JSON array: [{name, type: \"fact\"|\"preference\"|\"concept\"|\"person\"|\"topic\", metadata: {description}}]\n"
            "Only extract substantive information. Skip greetings, small talk, questions.\n"
            f"Message: {message_text}"
        )

        llm = LiteLLMClient(model=f"ollama/{get_settings().ollama_model_planner}")
        response = await llm.generate(extract_prompt, format="json")
        if not response:
            return

        entities = json.loads(response)
        if not isinstance(entities, list) or not entities:
            return

        # Connect to Neo4j and persist
        client = get_neo4j_client()
        if client is None:
            logger.warning("Neo4j unavailable, skipping entity storage")
            return

        repo = GraphRepository(client)
        await repo.ensure_user_node(user_id)

        previous_id = None
        for ent in entities:
            name = ent.get("name", "")
            etype = ent.get("type", "fact")
            meta = ent.get("metadata", {})
            if not name:
                continue

            entity_id = await repo.save_entity(
                user_id=user_id,
                name=name,
                entity_type=etype,
                metadata=meta,
            )

            # Link entities extracted together
            if previous_id:
                await repo.save_relation(
                    source_entity_id=previous_id,
                    target_entity_id=entity_id,
                    relation_type="co_occurrence",
                )
            previous_id = entity_id

        logger.info(
            "Extracted %d entities from conversation %s for user %s",
            len(entities), conversation_id, user_id,
        )

    except json.JSONDecodeError:
        logger.warning("Failed to parse entity extraction JSON for conversation %s", conversation_id)
    except Exception as exc:
        logger.error("Entity extraction failed for conversation %s: %s", conversation_id, exc)
    finally:
        if db:
            await db.close()


@celery_app.task(name="extract_entities", bind=True, max_retries=1, default_retry_delay=30)
def extract_entities_from_message(
    self,
    conversation_id: str,
    user_id: str,
) -> None:
    """Celery task: extract entities from the last user message."""
    try:
        _run_async(_extract_async(conversation_id, user_id))
    except Exception as exc:
        logger.error("extract_entities task failed: %s", exc)
        raise self.retry(exc=exc)
