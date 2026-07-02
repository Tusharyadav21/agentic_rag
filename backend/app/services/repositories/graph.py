"""
Neo4j GraphRepository — Entity CRUD and Cypher queries for the user memory
knowledge graph.

Node labels: ``User``, ``Project``, ``Entity``
Relationship types: ``HAS_ENTITY``, ``RELATES_TO``
"""

import json
import logging
from uuid import uuid4

from app.services.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)


class GraphRepository:
    """Persistence layer for Neo4j entity graph operations."""

    def __init__(self, client: Neo4jClient) -> None:
        self._driver = client.get_driver()

    # ------------------------------------------------------------------
    # Node creation
    # ------------------------------------------------------------------

    async def ensure_user_node(self, user_id: str) -> None:
        """Create a ``User`` node if it does not already exist (MERGE)."""
        async with self._driver.session() as session:
            await session.run(
                "MERGE (u:User {id: $user_id})",
                user_id=user_id,
            )

    async def ensure_project_node(self, project_id: str, name: str) -> None:
        """Create a ``Project`` node linked to the owning user."""
        async with self._driver.session() as session:
            await session.run(
                "MERGE (p:Project {id: $project_id}) "
                "ON CREATE SET p.name = $name",
                project_id=project_id,
                name=name,
            )

    async def save_entity(
        self,
        user_id: str,
        name: str,
        entity_type: str,
        metadata: dict | None = None,
    ) -> str:
        """Create or merge an entity node and link it to the user.

        Returns the entity ID (UUID string).
        """
        entity_id = str(uuid4())
        metadata_json = json.dumps(metadata or {})
        async with self._driver.session() as session:
            await session.run(
                """
                MATCH (u:User {id: $user_id})
                MERGE (e:Entity {name: $name, user_id: $user_id})
                ON CREATE SET
                    e.id = $entity_id,
                    e.type = $entity_type,
                    e.metadata = $metadata_json,
                    e.created_at = timestamp()
                ON MATCH SET
                    e.metadata = $metadata_json,
                    e.type = $entity_type
                MERGE (u)-[:HAS_ENTITY]->(e)
                """,
                user_id=user_id,
                entity_id=entity_id,
                name=name,
                entity_type=entity_type,
                metadata_json=metadata_json,
            )
        return entity_id

    async def save_relation(
        self,
        source_entity_id: str,
        target_entity_id: str,
        relation_type: str,
        weight: float = 1.0,
    ) -> None:
        """Create a ``RELATES_TO`` relationship between two entities."""
        async with self._driver.session() as session:
            await session.run(
                """
                MATCH (a:Entity {id: $source_id})
                MATCH (b:Entity {id: $target_id})
                MERGE (a)-[r:RELATES_TO {type: $relation_type}]->(b)
                ON CREATE SET r.weight = $weight
                ON MATCH SET r.weight = $weight
                """,
                source_id=source_entity_id,
                target_id=target_entity_id,
                relation_type=relation_type,
                weight=weight,
            )

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    async def get_user_entities(
        self,
        user_id: str,
        limit: int = 50,
    ) -> list[dict]:
        """Return all entities linked to a user."""
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (u:User {id: $user_id})-[:HAS_ENTITY]->(e:Entity)
                RETURN e.id AS id,
                       e.name AS name,
                       e.type AS type,
                       e.metadata AS metadata,
                       e.created_at AS created_at
                LIMIT $limit
                """,
                user_id=user_id,
                limit=limit,
            )
            rows = await result.data()
        for row in rows:
            if isinstance(row.get("metadata"), str):
                try:
                    row["metadata"] = json.loads(row["metadata"])
                except (json.JSONDecodeError, TypeError):
                    row["metadata"] = {}
        return rows

    async def get_entity_context(
        self,
        user_id: str,
        entity_names: list[str],
        depth: int = 1,
    ) -> list[dict]:
        """Return an entity subgraph: the named entities plus their related
        nodes up to *depth* hops away."""
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (u:User {id: $user_id})-[:HAS_ENTITY]->(e:Entity)
                WHERE e.name IN $entity_names
                OPTIONAL MATCH (e)-[r]-(related)
                RETURN e AS entity,
                       labels(e) AS entity_labels,
                       r AS rel,
                       related,
                       labels(related) AS related_labels
                LIMIT 200
                """,
                user_id=user_id,
                entity_names=entity_names,
            )
            rows = await result.data()
        return self._format_context_rows(rows)

    async def get_project_context(
        self,
        project_id: str,
        limit: int = 30,
    ) -> list[dict]:
        """Return entities linked to a project."""
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (p:Project {id: $project_id})-[:HAS_ENTITY]->(e:Entity)
                RETURN e.id AS id,
                       e.name AS name,
                       e.type AS type,
                       e.metadata AS metadata
                LIMIT $limit
                """,
                project_id=project_id,
                limit=limit,
            )
            return await result.data()

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def format_context_for_prompt(entities: list[dict]) -> str:
        """Format entity context as human-readable text for LLM prompts.

        Returns an empty string when there are no entities.
        """
        if not entities:
            return ""

        lines = ["Knowledge Graph Context:"]
        for ent in entities:
            name = ent.get("name", "unknown")
            etype = ent.get("type", "unknown")
            meta = ent.get("metadata", {})
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except (json.JSONDecodeError, TypeError):
                    meta = {}
            meta_str = json.dumps(meta) if meta else ""
            lines.append(f"  - {name} ({etype}){': ' + meta_str if meta_str else ''}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_context_rows(rows: list[dict]) -> list[dict]:
        """Flatten and deduplicate the raw context query result."""
        seen: set[str] = set()
        result: list[dict] = []
        for row in rows:
            entity = row.get("entity")
            if entity and entity.get("id") not in seen:
                result.append(
                    {
                        "id": entity.get("id"),
                        "name": entity.get("name"),
                        "type": entity.get("type"),
                        "metadata": entity.get("metadata"),
                    }
                )
                if entity.get("id"):
                    seen.add(entity.get("id"))
        return result
