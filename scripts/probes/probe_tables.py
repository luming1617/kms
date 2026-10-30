"""M0-1：真实向量表名与 63 字符上限核算（§4.4 / §15.3）。"""

from __future__ import annotations

import types

from lightrag.base import BaseVectorStorage
from lightrag.kg.postgres_impl import PG_MAX_IDENTIFIER_LENGTH, namespace_to_table_name
from lightrag.namespace import NameSpace
from probes.common import make_embedding_func, print_summary, write_report

MODEL_CASES = [
    ("probe-embed", 32),
    ("text-embedding-3-large", 3072),
    ("bge-m3", 1024),
    ("text-embedding-3-small", 1536),
    ("extremely-long-embedding-model-name-used-for-limit-check", 4096),
]


def suffix_for(model_name: str, dim: int) -> str | None:
    stub = types.SimpleNamespace(embedding_func=make_embedding_func(dim=dim, model_name=model_name))
    return BaseVectorStorage._generate_collection_suffix(stub)  # type: ignore[arg-type]


def collect() -> dict:
    namespaces = {
        "chunks": NameSpace.VECTOR_STORE_CHUNKS,
        "entities": NameSpace.VECTOR_STORE_ENTITIES,
        "relationships": NameSpace.VECTOR_STORE_RELATIONSHIPS,
    }
    base_tables = {label: namespace_to_table_name(ns) for label, ns in namespaces.items()}
    cases = []
    all_ok = True
    for model_name, dim in MODEL_CASES:
        suffix = suffix_for(model_name, dim)
        tables = {label: f"{base}_{suffix}" for label, base in base_tables.items()}
        lengths = {label: len(name) for label, name in tables.items()}
        within_limit = suffix is not None and max(lengths.values()) <= PG_MAX_IDENTIFIER_LENGTH
        if model_name != "extremely-long-embedding-model-name-used-for-limit-check":
            all_ok = all_ok and within_limit
        cases.append(
            {
                "model_name": model_name,
                "embedding_dim": dim,
                "suffix": suffix,
                "tables": tables,
                "lengths": lengths,
                "within_limit": within_limit,
            }
        )

    no_suffix = suffix_for("", 1024)
    payload = {
        "probe": "tables",
        "pg_max_identifier_length": PG_MAX_IDENTIFIER_LENGTH,
        "base_tables": base_tables,
        "cases": cases,
        "model_name_missing_suffix": no_suffix,
        "notes": [
            "真实表名 = namespace_to_table_name(namespace) + '_' + _generate_collection_suffix()",
            "最长基表名 LIGHTRAG_VDB_RELATION = 21 字符，后缀规范化后必须 <= 41",
            "model_name 为空时后缀为 None，框架回落到裸基表并发 warning（§13 P2）",
        ],
        "limit_violation_detected": not cases[-1]["within_limit"],
        "passed": all_ok and no_suffix is None and not cases[-1]["within_limit"],
    }
    return payload


def main() -> None:
    payload = collect()
    write_report("01-tables", payload)
    print_summary(payload)


if __name__ == "__main__":
    main()
