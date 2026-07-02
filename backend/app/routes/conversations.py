from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.schemas.chat import ConversationDetail, ConversationRead, PaginatedConversations
from app.models.user import User
from app.services.repositories.conversations import ConversationRepository
from app.services.repositories.projects import ProjectRepository
from app.utils.database import get_db
from app.utils.dependencies import get_current_user

router = APIRouter(prefix="/api/projects/{project_id}/conversations", tags=["conversations"])
user_router = APIRouter(prefix="/api/conversations", tags=["conversations"])


async def _ensure_project(db: AsyncSession, project_id: UUID, user_id: UUID) -> None:
    """
    Purpose:
        Verifies that a project exists and belongs to the requesting user.

    Args:
        db (AsyncSession):
            Database session dependency.

        project_id (UUID):
            The ID of the project to verify.

        user_id (UUID):
            The ID of the user requesting access.

    Returns:
        None

    Raises:
        HTTPException:
            404 Not Found if the project does not exist or is not owned by the user.

    Flow:
        1. Query the ProjectRepository for the specified project and user.
        2. Raise 404 if no matching project is found.
    """
    if await ProjectRepository(db).get_for_user(project_id, user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")


@router.get("", response_model=PaginatedConversations)
async def list_conversations(
    project_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = 0,
    limit: int = 100,
) -> PaginatedConversations:
    """
    Purpose:
        Lists all conversations associated with a specific project.

    Responsibilities:
        - Verify user has access to the project
        - Fetch paginated conversation list from repository

    Args:
        project_id (UUID):
            The ID of the project.

        current_user (Annotated[User, Depends(get_current_user)]):
            The authenticated user.

        db (Annotated[AsyncSession, Depends(get_db)]):
            Database session dependency.

        skip (int, optional):
            Number of records to skip for pagination. Defaults to 0.

        limit (int, optional):
            Maximum number of records to return. Defaults to 100.

    Returns:
        PaginatedConversations:
            A paginated list of conversations and the total count.

    Flow:
        1. Validate project ownership using _ensure_project.
        2. Call ConversationRepository to retrieve a slice of conversations.
        3. Map database models to ConversationRead schemas.
        4. Return paginated response.
    """
    await _ensure_project(db, project_id, current_user.id)
    items, total = await ConversationRepository(db).list_for_project(
        project_id,
        skip=skip,
        limit=limit,
    )
    return PaginatedConversations(
        items=[ConversationRead.model_validate(item) for item in items],
        total=total,
    )


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    project_id: UUID,
    conversation_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Conversation:
    """
    Purpose:
        Retrieves detailed information for a specific conversation.

    Responsibilities:
        - Verify user has access to the project
        - Fetch conversation by ID and project scope

    Args:
        project_id (UUID):
            The ID of the parent project.

        conversation_id (UUID):
            The ID of the conversation to retrieve.

        current_user (Annotated[User, Depends(get_current_user)]):
            The authenticated user.

        db (Annotated[AsyncSession, Depends(get_db)]):
            Database session dependency.

    Returns:
        ConversationDetail:
            The detailed conversation record.

    Raises:
        HTTPException:
            404 Not Found if project not found or conversation doesn't belong to project.

    Flow:
        1. Validate project ownership using _ensure_project.
        2. Retrieve conversation via ConversationRepository scoped by project.
        3. Raise 404 if conversation is not found.
        4. Return conversation details.
    """
    await _ensure_project(db, project_id, current_user.id)
    conversation = await ConversationRepository(db).get_for_project(conversation_id, project_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return conversation


@user_router.get("", response_model=PaginatedConversations)
async def list_user_conversations(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = 0,
    limit: int = 100,
) -> PaginatedConversations:
    """List all conversations (both project and individual) for the authenticated user."""
    items, total = await ConversationRepository(db).list_for_user(
        current_user.id,
        skip=skip,
        limit=limit,
    )
    return PaginatedConversations(
        items=[ConversationRead.model_validate(item) for item in items],
        total=total,
    )


@user_router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_user_conversation(
    conversation_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Conversation:
    """Get a specific conversation by ownership (user must own it)."""
    conversation = await ConversationRepository(db).get_for_user(conversation_id, current_user.id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return conversation
