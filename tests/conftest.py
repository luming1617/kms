"""pytest 公共配置：src/scripts 导入路径与 PG 可用性探针。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT / "scripts", ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

# 测试同时使用 `src.kms.*` 与生产代码的 `kms.*`；必须折叠成同一组模块，
# 否则 contextvars / Prometheus 注册表会出现两份实例。
import importlib  # noqa: E402
import pkgutil  # noqa: E402

import kms  # noqa: E402

sys.modules.setdefault("src.kms", kms)
for module_info in pkgutil.walk_packages(kms.__path__, prefix="kms."):
    try:
        module = importlib.import_module(module_info.name)
    except ImportError:
        continue
    sys.modules.setdefault("src." + module_info.name, module)

from src.kms.config import Settings  # noqa: E402


@pytest.fixture(scope="session")
def project_root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings.from_env()


@pytest.fixture(scope="session")
def pg_available(settings: Settings) -> bool:
    import asyncio

    import asyncpg

    async def _check() -> bool:
        try:
            conn = await asyncpg.connect(settings.postgres.dsn())
        except Exception:
            return False
        await conn.close()
        return True

    return asyncio.run(_check())
