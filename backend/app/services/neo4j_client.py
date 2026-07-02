"""
Neo4j async client — singleton wrapping neo4j AsyncDriver.

Follows the same singleton + factory pattern as
``backend/app/utils/redis_client.py``.
"""

import logging

from neo4j import AsyncGraphDatabase

from app.config import get_settings

logger = logging.getLogger(__name__)


class Neo4jClient:
    """Async Neo4j driver wrapper with connection health-check."""

    def __init__(self, uri: str, user: str, password: str) -> None:
        self._driver = AsyncGraphDatabase.driver(uri, auth=(user, password))

    async def close(self) -> None:
        """Release the driver and all open connections."""
        await self._driver.close()

    async def check_connection(self) -> bool:
        """Run RETURN 1 to verify the connection is alive."""
        try:
            async with self._driver.session() as session:
                result = await session.run("RETURN 1")
                await result.consume()
                return True
        except Exception as exc:
            logger.warning("Neo4j connection check failed: %s", exc)
            return False

    def get_driver(self) -> AsyncGraphDatabase.driver:
        """Return the underlying async driver instance."""
        return self._driver


neo4j_client: Neo4jClient | None = None


def get_neo4j_client() -> Neo4jClient | None:
    """Return the singleton Neo4jClient, creating it if necessary.

    Returns None (with a logged warning) when Neo4j is unavailable so
    callers can degrade gracefully.
    """
    global neo4j_client
    if neo4j_client is None:
        settings = get_settings()
        try:
            neo4j_client = Neo4jClient(
                uri=settings.neo4j_uri,
                user=settings.neo4j_user,
                password=settings.neo4j_password,
            )
        except Exception as exc:
            logger.warning("Failed to initialise Neo4j client: %s", exc)
            return None
    return neo4j_client
