"""2-tier MemoryManager: short-term (SQL history) + long-term (Neo4j entities).

This replaces the previous stub with real Neo4j-backed long-term memory.
"""

import logging
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, Message
from app.services.neo4j_client import get_neo4j_client
from app.services.repositories.graph import GraphRepository

logger = logging.getLogger(__name__)

_MEMORY_LIMIT = 50


class MemoryManager:
    """Loads short-term conversation history and long-term Neo4j entity memory."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def load_short_term(
        self,
        conversation_id: UUID,
        limit: int = _MEMORY_LIMIT,
    ) -> list[Message]:
        """Load recent conversation messages from SQL."""
        result = await self.db.execute(
            sa.select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        # Return in chronological order
        return list(reversed(result.scalars().all()))

    async def load_long_term(
        self,
        user_id: UUID,
        entity_names: list[str] | None = None,
    ) -> str:
        """Load Neo4j entity context for a user.

        Returns formatted text for prompt injection, or empty string if
        Neo4j is unavailable or no entities exist.
        """
        client = get_neo4j_client()
        if client is None:
            return ""

        try:
            repo = GraphRepository(client)
            if entity_names:
                entities = await repo.get_entity_context(str(user_id), entity_names)
            else:
                entities = await repo.get_user_entities(str(user_id))

            return GraphRepository.format_context_for_prompt(entities)
        except Exception as exc:
            logger.warning("Failed to load Neo4j long-term memory: %s", exc)
            return ""

    async def load_context(
        self,
        conversation_id: UUID,
        user_id: UUID,
        project_id: UUID | None = None,
    ) -> dict:
        """Combine short-term and long-term memory into a single context dict.

        Returns:
            ``{"short_term": [...], "long_term": "...", "project_entities": "..."}``
        """
        short_term = await self.load_short_term(conversation_id)
        long_term = await self.load_long_term(user_id)

        result: dict = {
            "short_term": [m for m in short_term],
            "long_term": long_term,
        }

        if project_id is not None:
            client = get_neo4j_client()
            if client is not None:
                try:
                    repo = GraphRepository(client)
                    project_entities = await repo.get_project_context(str(project_id))
                    result["project_entities"] = (
                        GraphRepository.format_context_for_prompt(project_entities)
                    )
                except Exception as exc:
                    logger.warning("Failed to load project entities: %s", exc)
                    result["project_entities"] = ""
            else:
                result["project_entities"] = ""

        return result
