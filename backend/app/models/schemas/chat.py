from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import MessageRole


class ChatRequest(BaseModel):
    """
    Purpose:
        Schema for RAG chat query requests.

    Attributes:
        conversation_id (UUID | None): ID of existing conversation to continue.
        message (str): The user's natural language query.
        document_ids (list[UUID] | None): Optional filter to limit search to specific documents.
        model (str | None): Override for the LLM model.
        num_ctx (int | None): Override for the context window size.
        num_predict (int | None): Override for the max prediction tokens.
    """
    conversation_id: UUID | None = None
    message: str = Field(min_length=1, max_length=4000)
    document_ids: list[UUID] | None = None
    model: str | None = None
    num_ctx: int | None = None
    num_predict: int | None = None


class FeedbackRequest(BaseModel):
    """
    Purpose:
        Schema for user feedback on assistant responses.

    Attributes:
        rating (str): "up" or "down" rating.
        comment (str | None): Optional text comment explaining the rating.
    """
    rating: str = Field(pattern=r"^(up|down)$")
    comment: str | None = Field(default=None, max_length=1000)



class ConversationRead(BaseModel):
    """
    Purpose:
        Sanitized conversation metadata for API responses.

    Attributes:
        id (UUID): Unique conversation identifier.
        project_id (UUID): ID of the parent project.
        title (str | None): Conversation title.
        created_at (datetime): Creation timestamp.
        updated_at (datetime): Last update timestamp.
    """
    id: UUID
    project_id: UUID | None
    user_id: UUID
    title: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MessageRead(BaseModel):
    """
    Purpose:
        Sanitized message representation for API responses.

    Attributes:
        id (UUID): Unique message identifier.
        conversation_id (UUID): Parent conversation ID.
        role (MessageRole): Role of the message (USER, ASSISTANT, SYSTEM).
        content (str): The actual text content.
        metadata_ (dict): Store for citations, sources, and feedback.
        created_at (datetime): Timestamp of creation.
    """
    id: UUID
    conversation_id: UUID
    role: MessageRole
    content: str
    metadata_: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationDetail(ConversationRead):
    """
    Purpose:
        Full conversation record including the message history.

    Attributes:
        messages (list[MessageRead]): Ordered list of messages in the conversation.
    """
    messages: list[MessageRead]


class PaginatedConversations(BaseModel):
    """
    Purpose:
        Schema for paginated lists of conversations.

    Attributes:
        items (list[ConversationRead]): The slice of conversations for the current page.
        total (int): Total number of conversations matching the query.
    """
    items: list[ConversationRead]
    total: int
