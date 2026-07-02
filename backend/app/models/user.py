from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Enum,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    ChatScopeMode,
    HomeTab,
    LLMProvider,
    ThemePreference,
)
from app.utils.database import Base

if TYPE_CHECKING:
    from app.models.conversation import Conversation
    from app.models.document import Document, DocumentTemplate


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(
        String(320),
        unique=True,
        index=True,
        nullable=False,
    )

    hashed_password: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    display_name: Mapped[str | None] = mapped_column(String(120))

    theme_preference: Mapped[ThemePreference] = mapped_column(
        Enum(ThemePreference, name="theme_preference_enum"),
        default=ThemePreference.SYSTEM,
        nullable=False,
    )

    default_home_tab: Mapped[HomeTab] = mapped_column(
        Enum(HomeTab, name="home_tab_enum"),
        default=HomeTab.CHAT,
        nullable=False,
    )

    sidebar_collapsed: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    new_chat_scope_mode: Mapped[ChatScopeMode] = mapped_column(
        Enum(ChatScopeMode, name="chat_scope_mode_enum"),
        default=ChatScopeMode.CLEAR,
        nullable=False,
    )

    projects: Mapped[list[Project]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    memories: Mapped[list[UserMemory]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    llm_config: Mapped[UserLLMConfig | None] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )


class UserMemory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_memories"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    user: Mapped[User] = relationship(
        back_populates="memories",
    )


class UserLLMConfig(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_llm_configs"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    provider: Mapped[LLMProvider] = mapped_column(
        Enum(LLMProvider, name="llm_provider_enum"),
        nullable=False,
    )

    api_key_encrypted: Mapped[bytes] = mapped_column(
        LargeBinary,
        nullable=False,
    )

    model: Mapped[str | None] = mapped_column(String(120))

    temperature: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.7,
    )

    max_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=2048,
    )

    max_retries: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
    )

    base_delay_s: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=1.0,
    )

    timeout_s: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=60,
    )

    extra_params: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSONB),
        default=dict,
        nullable=False,
    )

    user: Mapped[User] = relationship(
        back_populates="llm_config",
    )


# Project is here rather than in document.py because it has a direct
# user_id FK and belongs primarily to the user domain. It also has
# relationships to Conversation, Document, and DocumentTemplate via
# forward references resolved lazily by SQLAlchemy.


class Project(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "projects"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    system_prompt: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User] = relationship(back_populates="projects")

    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )

    documents: Mapped[list[Document]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )

    templates: Mapped[list[DocumentTemplate]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "name",
            name="uq_projects_user_name",
        ),
    )



