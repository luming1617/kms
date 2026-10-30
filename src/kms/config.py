"""集中配置：全部来自环境变量，启动时做 fail-fast 校验。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import quote

DEFAULT_LANGUAGES = ("zh-CN", "en-US", "ja-JP")

SECURITY_LEVEL_READ_SETS: dict[str, tuple[str, ...]] = {
    "TopSecret": ("TopSecret", "Confidential", "Secret", "Public"),
    "Confidential": ("Confidential", "Secret", "Public"),
    "Secret": ("Secret", "Public"),
    "Public": ("Public",),
}


class ConfigurationError(RuntimeError):
    """启动配置不满足方案约束。"""


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class PostgresSettings:
    host: str
    port: int
    user: str
    password: str
    database: str
    max_connections: int = 20
    ssl_mode: str | None = None
    hnsw_ef_search: int = 40
    hnsw_iterative_scan: str = "off"
    hnsw_max_scan_tuples: int = 20000
    hnsw_scan_mem_multiplier: float = 1.0

    def dsn(self, database: str | None = None) -> str:
        user = quote(self.user, safe="")
        password = quote(self.password, safe="")
        db = database or self.database
        return f"postgresql://{user}:{password}@{self.host}:{self.port}/{db}"

    def lightrag_env(self) -> dict[str, str]:
        """LightRAG 的 ClientManager 只认 POSTGRES_* 环境变量。"""
        server_settings = [
            item for item in (os.environ.get("POSTGRES_SERVER_SETTINGS") or "").split("&") if item
        ]
        server_settings.extend(
            [
                f"hnsw.ef_search={self.hnsw_ef_search}",
                f"hnsw.iterative_scan={self.hnsw_iterative_scan}",
                f"hnsw.max_scan_tuples={self.hnsw_max_scan_tuples}",
                f"hnsw.scan_mem_multiplier={self.hnsw_scan_mem_multiplier}",
            ]
        )
        env = {
            "POSTGRES_HOST": self.host,
            "POSTGRES_PORT": str(self.port),
            "POSTGRES_USER": self.user,
            "POSTGRES_PASSWORD": self.password,
            "POSTGRES_DATABASE": self.database,
            "POSTGRES_MAX_CONNECTIONS": str(self.max_connections),
            "POSTGRES_SERVER_SETTINGS": "&".join(server_settings),
        }
        if self.ssl_mode:
            env["POSTGRES_SSL_MODE"] = self.ssl_mode
        return env


@dataclass(frozen=True, slots=True)
class RetrievalSettings:
    top_k: int = 40
    chunk_top_k: int = 20
    max_total_tokens: int = 12000
    max_entity_tokens: int = 6000
    max_relation_tokens: int = 8000
    min_rerank_score: float = 0.5
    rerank_model: str = "default"
    enable_rerank: bool = False
    answer_min_chunks: int = 2
    l2_batch_discard_ratio: float = 0.5
    relax_order: tuple[str, ...] = ("asset", "region")
    max_param_retry: int = 1
    framework_retry_attempts: int = 2


@dataclass(frozen=True, slots=True)
class LlmSettings:
    model_id: str = "local-llm"
    prompt_version: str = "v1"
    base_url: str | None = None
    api_key: str | None = None
    timeout_seconds: float = 60.0


@dataclass(frozen=True, slots=True)
class Settings:
    postgres: PostgresSettings
    retrieval: RetrievalSettings = field(default_factory=RetrievalSettings)
    llm: LlmSettings = field(default_factory=LlmSettings)
    default_embedding_dim: int = 1024
    enable_answer_cache: bool = False
    log_level: str = "INFO"
    session_secret: str = ""
    session_max_age_seconds: int = 300
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    embedding_timeout_seconds: float = 30.0
    nlu_base_url: str | None = None
    nlu_model: str | None = None
    nlu_api_key: str | None = None
    otel_endpoint: str | None = None
    langfuse_enabled: bool = False
    langfuse_host: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    pushgateway_url: str | None = None
    publish_lock_timeout_seconds: float = 30.0
    admin_token: str | None = None
    api_host: str = "0.0.0.0"  # noqa: S104  # nosec B104 - 容器内监听
    api_port: int = 8000

    @classmethod
    def from_env(cls) -> Settings:
        postgres = PostgresSettings(
            host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
            port=_env_int("POSTGRES_PORT", 5434),
            user=os.environ.get("POSTGRES_USER", "ics"),
            password=os.environ.get("POSTGRES_PASSWORD", "ics"),
            database=os.environ.get("POSTGRES_DATABASE", "ics"),
            max_connections=_env_int("POSTGRES_MAX_CONNECTIONS", 20),
            ssl_mode=os.environ.get("POSTGRES_SSL_MODE") or None,
            hnsw_ef_search=_env_int("POSTGRES_HNSW_EF_SEARCH", 40),
            hnsw_iterative_scan=os.environ.get("POSTGRES_HNSW_ITERATIVE_SCAN", "off"),
            hnsw_max_scan_tuples=_env_int("POSTGRES_HNSW_MAX_SCAN_TUPLES", 20000),
            hnsw_scan_mem_multiplier=_env_float("POSTGRES_HNSW_SCAN_MEM_MULTIPLIER", 1.0),
        )
        retrieval = RetrievalSettings(
            top_k=_env_int("ICS_TOP_K", 40),
            chunk_top_k=_env_int("ICS_CHUNK_TOP_K", 20),
            max_total_tokens=_env_int("ICS_MAX_TOTAL_TOKENS", 12000),
            max_entity_tokens=_env_int("ICS_MAX_ENTITY_TOKENS", 6000),
            max_relation_tokens=_env_int("ICS_MAX_RELATION_TOKENS", 8000),
            min_rerank_score=_env_float("ICS_MIN_RERANK_SCORE", 0.5),
            rerank_model=os.environ.get("ICS_RERANK_MODEL", "default"),
            enable_rerank=_env_bool("ICS_ENABLE_RERANK", False),
            answer_min_chunks=_env_int("ICS_ANSWER_MIN_CHUNKS", 2),
            l2_batch_discard_ratio=_env_float("ICS_L2_BATCH_DISCARD_RATIO", 0.5),
            framework_retry_attempts=_env_int("ICS_FRAMEWORK_RETRY_ATTEMPTS", 2),
        )
        llm = LlmSettings(
            model_id=os.environ.get("ICS_LLM_MODEL_ID", "local-llm"),
            prompt_version=os.environ.get("ICS_PROMPT_VERSION", "v1"),
            base_url=os.environ.get("ICS_LLM_BASE_URL") or None,
            api_key=os.environ.get("ICS_LLM_API_KEY") or None,
            timeout_seconds=_env_float("ICS_LLM_TIMEOUT_SECONDS", 60.0),
        )
        return cls(
            postgres=postgres,
            retrieval=retrieval,
            llm=llm,
            default_embedding_dim=_env_int("ICS_EMBEDDING_DIM", 1024),
            enable_answer_cache=_env_bool("ICS_ENABLE_ANSWER_CACHE", False),
            log_level=os.environ.get("ICS_LOG_LEVEL", "INFO"),
            session_secret=os.environ.get("ICS_SESSION_SECRET", ""),
            session_max_age_seconds=_env_int("ICS_SESSION_MAX_AGE_SECONDS", 300),
            embedding_base_url=os.environ.get("ICS_EMBEDDING_BASE_URL") or None,
            embedding_api_key=os.environ.get("ICS_EMBEDDING_API_KEY") or None,
            embedding_timeout_seconds=_env_float("ICS_EMBEDDING_TIMEOUT_SECONDS", 30.0),
            nlu_base_url=os.environ.get("ICS_NLU_BASE_URL") or None,
            nlu_model=os.environ.get("ICS_NLU_MODEL") or None,
            nlu_api_key=os.environ.get("ICS_NLU_API_KEY") or None,
            otel_endpoint=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or None,
            langfuse_enabled=_env_bool("LANGFUSE_ENABLED", False),
            langfuse_host=os.environ.get("LANGFUSE_HOST") or None,
            langfuse_public_key=os.environ.get("LANGFUSE_PUBLIC_KEY") or None,
            langfuse_secret_key=os.environ.get("LANGFUSE_SECRET_KEY") or None,
            pushgateway_url=os.environ.get("PUSHGATEWAY_URL") or None,
            publish_lock_timeout_seconds=_env_float("ICS_PUBLISH_LOCK_TIMEOUT_SECONDS", 30.0),
            admin_token=os.environ.get("ICS_ADMIN_TOKEN") or None,
            api_host=os.environ.get("ICS_API_HOST", "0.0.0.0"),  # noqa: S104  # nosec B104
            api_port=_env_int("ICS_API_PORT", 8000),
        )

    def validate_startup(self) -> None:
        """§13：POSTGRES_WORKSPACE 必须为空；非空会静默覆盖全部 workspace。"""
        workspace_override = os.environ.get("POSTGRES_WORKSPACE")
        if workspace_override:
            raise ConfigurationError(
                "POSTGRES_WORKSPACE 必须为空：非空会覆盖全部 workspace 路由（§3.1/§13）"
            )
        if self.retrieval.answer_min_chunks < 1:
            raise ConfigurationError("ICS_ANSWER_MIN_CHUNKS 不得低于 1（§6.2）")
        if not 0.0 <= self.retrieval.l2_batch_discard_ratio <= 1.0:
            raise ConfigurationError("ICS_L2_BATCH_DISCARD_RATIO 必须位于 [0, 1]")
        if self.retrieval.framework_retry_attempts < 1:
            raise ConfigurationError("ICS_FRAMEWORK_RETRY_ATTEMPTS 不得低于 1")
        if self.postgres.hnsw_iterative_scan not in {"off", "relaxed_order", "strict_order"}:
            raise ConfigurationError(
                "POSTGRES_HNSW_ITERATIVE_SCAN 必须是 off/relaxed_order/strict_order"
            )

    def require_session_secret(self) -> str:
        if len(self.session_secret) < 32:
            raise ConfigurationError("ICS_SESSION_SECRET 至少 32 字符；请求体不得承载授权维度")
        return self.session_secret


def load_settings() -> Settings:
    settings = Settings.from_env()
    settings.validate_startup()
    return settings
