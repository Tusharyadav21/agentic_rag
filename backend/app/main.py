from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.config import get_settings
from app.routes import (
    agents,
    analytics,
    auth,
    chat,
    conversations,
    individual_chat,
    documents,
    extract,
    health,
    llm_config,
    platform,
    projects,
    templates,
)
from app.utils.logging import configure_logging
from app.utils.middleware import RequestTracingMiddleware
from app.utils.rate_limit import limiter


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Purpose:
        Manages application startup and shutdown events.

    Responsibilities:
        - Initialize global settings
        - Configure system logging

    Args:
        app (FastAPI):
            The FastAPI application instance.

    Returns:
        None

    Side Effects:
        - Sets up logging for the process
    """
    settings = get_settings()
    configure_logging(settings.log_level)
    
    from app.utils.minio_client import ensure_bucket_exists
    from app.utils.qdrant_client import ensure_collection_exists
    ensure_bucket_exists()
    await ensure_collection_exists("artha_docs")
    
    yield


app = FastAPI(title="Artha API", version="0.1.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

settings = get_settings()
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(RequestTracingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_origin_regex=settings.cors_allow_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(documents.router)
app.include_router(conversations.router)
app.include_router(conversations.user_router)
app.include_router(individual_chat.router)
app.include_router(chat.router)
app.include_router(analytics.router)
app.include_router(extract.router)
app.include_router(llm_config.router)
app.include_router(templates.router)
app.include_router(agents.router)
app.include_router(platform.router)

from dishka.integrations.fastapi import setup_dishka
from app.utils.di import setup_di

container = setup_di()
setup_dishka(container, app)
