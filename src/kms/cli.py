"""服务入口：会话验签 + 多 workspace 装配 + uvicorn。"""

from __future__ import annotations

import asyncio
import logging
import sys

import uvicorn

from kms.api.admin import AuthorityAdminStore
from kms.api.app import create_app
from kms.api.session import SignedSessionResolver
from kms.config import load_settings
from kms.observability import tracing
from kms.observability.langfuse import LangfuseObserver
from kms.providers import OpenAICompatibleEmbeddingProvider, OpenAICompatibleLLM
from kms.query.nlu import LlmKeywordProvider
from kms.runtime import build_runtime


async def _serve() -> None:
    settings = load_settings()
    logging.basicConfig(level=settings.log_level.upper())
    secret = settings.require_session_secret()
    if not settings.llm.base_url:
        raise SystemExit("ICS_LLM_BASE_URL 未配置：服务无法生成回答")
    embedding_base = settings.embedding_base_url or settings.llm.base_url
    if not embedding_base:
        raise SystemExit("ICS_EMBEDDING_BASE_URL 未配置：服务无法检索")
    tracing.configure_tracing(endpoint=settings.otel_endpoint)
    observer = (
        LangfuseObserver(
            enabled=True,
            host=settings.langfuse_host,
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
        )
        if settings.langfuse_enabled
        else None
    )
    embedding = OpenAICompatibleEmbeddingProvider(
        base_url=embedding_base,
        api_key=settings.embedding_api_key or settings.llm.api_key,
        timeout_seconds=settings.embedding_timeout_seconds,
    )
    llm = OpenAICompatibleLLM(
        base_url=settings.llm.base_url,
        model=settings.llm.model_id,
        api_key=settings.llm.api_key,
        timeout_seconds=settings.llm.timeout_seconds,
    )
    keywords = None
    if settings.nlu_model and (settings.nlu_base_url or settings.llm.base_url):
        nlu_llm = OpenAICompatibleLLM(
            base_url=settings.nlu_base_url or settings.llm.base_url,
            model=settings.nlu_model,
            api_key=settings.nlu_api_key or settings.llm.api_key,
            timeout_seconds=settings.llm.timeout_seconds,
        )
        keywords = LlmKeywordProvider(nlu_llm)
    runtime = await build_runtime(
        settings,
        embedding_provider=embedding,
        llm=llm,
        observer=observer,
        keywords=keywords,
    )
    admin_store = (
        AuthorityAdminStore(runtime.database)
        if settings.admin_token and getattr(runtime, "database", None) is not None
        else None
    )
    app = create_app(
        runtime.query_service,
        SignedSessionResolver(secret, max_age_seconds=settings.session_max_age_seconds),
        admin_store=admin_store,
        admin_token=settings.admin_token,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=settings.api_host,
            port=settings.api_port,
            log_level=settings.log_level.lower(),
        )
    )
    try:
        await server.serve()
    finally:
        await runtime.aclose()


def main() -> int:
    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
