from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.conversation import Conversation, Message
from app.models.enums import MessageRole


class ConversationRepository:
    """
    Purpose:
        Handles persistence and retrieval of Conversation and Message entities.

    Responsibilities:
        - Manage conversation lifecycles within a project.
        - Handle paginated retrieval of conversation history.
        - Orchestrate message insertion into conversations.

    Dependencies:
        - SQLAlchemy AsyncSession for database access

    Architectural constraints:
        - All access must be scoped by project_id.
        - Messages are fetched via selectinload to avoid N+1 queries during retrieval.
    """
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_for_project(
        self,
        project_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[list[Conversation], int]:
        """
        Purpose:
            Retrieve a paginated list of conversations for a project.

        Responsibilities:
            - Filter conversations by project_id.
            - Order by last update descending.
            - Provide total count for pagination.

        Inputs:
            project_id (UUID): ID of the project scoping the request.
            skip (int): Number of records to skip.
            limit (int): Maximum number of records to return.

        Outputs:
            tuple[list[Conversation], int]: A tuple containing the slice of conversations and
                the total matching count.

        Exceptions:
            None explicitly raised.

        Side effects:
            Read-only operation.

        Execution flow:
            1. Count total conversations matching project_id using func.count().
            2. Execute paginated select query with offset and limit.
            3. Return result scalars and total count.
        """
        from sqlalchemy import func

        total = await self.db.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.project_id == project_id)
        )
        result = await self.db.execute(
            select(Conversation)
            .where(Conversation.project_id == project_id)
            .order_by(Conversation.updated_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return list(result.scalars().all()), total or 0

    async def get_for_project(self, conversation_id: UUID, project_id: UUID) -> Conversation | None:
        """
        Purpose:
            Retrieve a single conversation and its associated messages.

        Responsibilities:
            - Verify conversation belongs to the specified project.
            - Eagerly load messages to prevent secondary queries.

        Inputs:
            conversation_id (UUID): ID of the conversation.
            project_id (UUID): ID of the project for access control.

        Outputs:
            Conversation | None: The conversation entity with loaded messages, or None if not
                found/authorized.

        Exceptions:
            None explicitly raised.

        Side effects:
            Read-only operation.

        Execution flow:
            1. Construct select query with selectinload(Conversation.messages).
            2. Filter by conversation_id and project_id.
            3. Return the scalar result.
        """
        result = await self.db.execute(
            select(Conversation)
            .options(selectinload(Conversation.messages))
            .where(Conversation.id == conversation_id, Conversation.project_id == project_id)
        )
        return result.scalar_one_or_none()

    async def create(self, project_id: UUID, title: str | None = None, user_id: UUID | None = None) -> Conversation:
        """
        Purpose:
            Initialize a new conversation within a project.

        Responsibilities:
            - Persist a new Conversation entity.
            - Ensure a default title is set if none provided.

        Inputs:
            project_id (UUID): ID of the parent project.
            title (str | None): Optional conversation title.
            user_id (UUID | None): ID of the owning user (required after user_id FK migration).

        Outputs:
            Conversation: The created conversation entity.

        Exceptions:
            SQLAlchemy errors during commit.

        Side effects:
            Inserts a new row into the conversations table.

        Execution flow:
            1. Instantiate Conversation with project_id, user_id, and title.
            2. Add to session and flush to generate ID.
            3. Apply default "New conversation" title if title is missing.
            4. Commit and refresh.
            5. Return conversation.
        """
        conversation = Conversation(project_id=project_id, user_id=user_id, title=title)
        self.db.add(conversation)
        await self.db.flush()
        if not conversation.title:
            conversation.title = "New conversation"
        await self.db.commit()
        await self.db.refresh(conversation)
        return conversation

    async def create_individual(self, user_id: UUID, title: str | None = None) -> Conversation:
        """Create a new individual (non-project) conversation."""
        conversation = Conversation(user_id=user_id, title=title)
        self.db.add(conversation)
        await self.db.flush()
        if not conversation.title:
            conversation.title = "New conversation"
        await self.db.commit()
        await self.db.refresh(conversation)
        return conversation

    async def list_for_user(
        self,
        user_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[list[Conversation], int]:
        """List ALL conversations for a user (both project and individual)."""
        from sqlalchemy import func

        total = await self.db.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.user_id == user_id)
        )
        result = await self.db.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return list(result.scalars().all()), total or 0

    async def get_for_user(self, conversation_id: UUID, user_id: UUID) -> Conversation | None:
        """Get a conversation by ownership (regardless of project scope)."""
        result = await self.db.execute(
            select(Conversation)
            .options(selectinload(Conversation.messages))
            .where(Conversation.id == conversation_id, Conversation.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def add_message(
        self,
        conversation_id: UUID,
        role: MessageRole,
        content: str,
        metadata: dict | None = None,
    ) -> Message:
        """
        Purpose:
            Append a message to an existing conversation.

        Responsibilities:
            - Persist a new Message entity linked to the conversation.

        Inputs:
            conversation_id (UUID): ID of the target conversation.
            role (MessageRole): Role of the message sender (USER, ASSISTANT, SYSTEM).
            content (str): The message text.
            metadata (dict | None): Optional structured metadata.

        Outputs:
            Message: The created message entity.

        Exceptions:
            SQLAlchemy errors during commit.

        Side effects:
            Inserts a new row into the messages table.

        Execution flow:
            1. Create Message instance.
            2. Add to session.
            3. Commit and refresh.
            4. Return message.
        """
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            metadata_=metadata or {},
        )
        self.db.add(message)
        await self.db.commit()
        await self.db.refresh(message)
        return message
