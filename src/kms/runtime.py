"""生产装配：按语言建 RAG / QueryService / PublishPipeline。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from lightrag import LightRAG
from lightrag.utils import EmbeddingFunc

from kms.authority.projection import (
    apply_candidate_projection_schema,
    apply_chunk_projection_schema,
)
from kms.config import Settings
from kms.db import Database
from kms.publish.pipeline import PublishPipeline, ensure_authority_schema
from kms.query.cache import AnswerCache
from kms.query.nlu import KeywordProvider
from kms.query.pipeline import QueryService
from kms.query.router import MultiWorkspaceQueryService
from kms.storage.backends import build_lightrag
from kms.workspace import WorkspaceRecord, WorkspaceRegistry


def make_embedding_func(record: WorkspaceRecord, provider: Any) -> EmbeddingFunc:
    async def _embed(texts: list[str], **_kwargs: Any) -> np.ndarray:
        vectors = await provider.embed(texts, model=record.embedding_model)
        return np.asarray(vectors, dtype=np.float32)

    return EmbeddingFunc(
        embedding_dim=record.embedding_dim,
        func=_embed,
        model_name=record.embedding_model,
    )


@dataclass
class Runtime:
    settings: Settings
    database: Database
    registry: WorkspaceRegistry
    rags: dict[str, LightRAG]
    query_services: dict[str, QueryService]
    publish_pipelines: dict[str, PublishPipeline]
    observers: list[Any]

    @property
    def query_service(self) -> MultiWorkspaceQueryService:
        return MultiWorkspaceQueryService(self.query_services)

    async def aclose(self) -> None:
        for rag in self.rags.values():
            await rag.finalize_storages()
        await self.database.close()


async def build_runtime(
    settings: Settings,
    *,
    embedding_provider: Any,
    llm: Any,
    cache: AnswerCache | None = None,
    observer: Any | None = None,
    keywords: KeywordProvider | None = None,
) -> Runtime:
    database = Database(settings)
    await database.connect()
    registry = WorkspaceRegistry(database)
    await ensure_authority_schema(database)
    rags: dict[str, LightRAG] = {}
    services: dict[str, QueryService] = {}
    pipelines: dict[str, PublishPipeline] = {}
    observers: list[Any] = []
    try:
        for record in await registry.list_all():
            if not record.enabled:
                continue
            embedding_func = make_embedding_func(record, embedding_provider)
            rag = build_lightrag(
                settings,
                workspace=record.lang,
                embedding_func=embedding_func,
                llm_model_func=llm,
            )
            await rag.initialize_storages()
            async with database.transaction() as connection:
                await apply_chunk_projection_schema(
                    connection,
                    model_name=record.embedding_model,
                    embedding_dim=record.embedding_dim,
                )
                await apply_candidate_projection_schema(
                    connection,
                    model_name=record.embedding_model,
                    embedding_dim=record.embedding_dim,
                )
            rags[record.lang] = rag
            services[record.lang] = QueryService(
                settings=settings,
                database=database,
                registry=registry,
                rag=rag,
                llm=llm,
                cache=cache,
                observer=observer,
                keywords=keywords,
            )
            pipelines[record.lang] = PublishPipeline(
                settings=settings,
                database=database,
                registry=registry,
                rag=rag,
                workspace=record,
            )
            if observer is not None:
                observers.append(observer)
        return Runtime(
            settings=settings,
            database=database,
            registry=registry,
            rags=rags,
            query_services=services,
            publish_pipelines=pipelines,
            observers=observers,
        )
    except Exception:
        for rag in rags.values():
            await rag.finalize_storages()
        await database.close()
        raise
