# 智能客服知识管理服务（ics-knowledge）

按 `docs/智能客服知识管理-技术方案.md` 的 M0–M8 实施计划落地。技术栈固定为：

- LightRAG 1.5.7（框架原生 API，`aquery_data` + `mix`）
- PostgreSQL 18.6 + pgvector 0.8.7（HNSW）
- Python 3.12，src layout

## 当前状态

| 阶段 | 内容 | 证据 |
|---|---|---|
| M0 | 探测与基线（表名 / 签名 / chunk_id / 索引代价 / 压测） | `reports/m0/final` |
| M1 | workspace={lang} 路由、四后端重指向、启动自检 | `reports/m1/M1-验收报告.md` |
| M2 | 权威层、字典、草稿校验、冲突队列、归组签字 | `reports/m2` |
| M3 | 两 chunk 表加列、正文闸 ①②④、**G1** | `reports/m3` |
| M4 | 候选闸 ③a/③b/⑤⑥⑦⑧⑨、degree 同谓词 | `reports/m4/M4-验收报告.md` |
| M5 | 发布/改版/撤回、图 scope 反聚合、对账、**G2** | `reports/m5` |
| M6 | 八步查询链路、L2 三通道、L3 阶梯、生成与引用 | `reports/m6/M6-验收报告.md` |
| M7 | 指标/Trace、nightly、越权 CI、压测、**G3** | `reports/m7` |
| M7a | 锁定集 132 条、确定性指标、生命周期 | `reports/m7a` |
| M7b | OTel/Collector/Prometheus/Langfuse、告警演练 | `reports/m7b` |
| M8 | 单表 vs 按 lang 分区决策记录 | `reports/m8` |

全量测试：104 passed；覆盖率以 `pytest --cov=kms --cov-branch --cov-fail-under=85` 为准；
ruff/mypy 全绿；bandit medium=0。

## 会话契约（P0）

`POST /v1/answer` 的请求体只有 `question`；语言与六维授权必须来自网关注入的
`X-ICS-Session` 签名头（HMAC-SHA256，密钥 `ICS_SESSION_SECRET`，≥32 字符）。
管理面另用 `Authorization: Bearer $ICS_ADMIN_TOKEN`，未配置则不注册路由：

```json
{"question": "请说明 BTC 现货的交易规则"}
```

签名载荷包含 `language` / `product` / `year` / `security_level` / `asset` / `region` / `exp`，
服务端只验签、不接受请求体自报授权维度；签发示例见 `kms.api.session.SessionSigner`。

## 快速开始

```bash
# 1. 启动 PG18 + pgvector
docker compose -f docker/docker-compose.yml up -d

# 2. 隔离环境（SMB 挂载上不要做 pip 可编辑安装）
uv venv -p 3.12 /tmp/ics_venv
uv pip install --python /tmp/ics_venv/bin/python \
  lightrag-hku==1.5.7 asyncpg pgvector pydantic fastapi uvicorn prometheus-client httpx numpy \
  pytest pytest-asyncio pytest-cov ruff mypy bandit

# 3. 运行（全部命令用 PYTHONPATH，不依赖已安装包）
export PYTHONPATH=src:scripts
/tmp/ics_venv/bin/python -m pytest tests -q -p no:cacheprovider

# 4. 确定性评测 / 模型轨（可选；需要同一套 LLM/embedding 端点）
PYTHONPATH=src:scripts /tmp/ics_venv/bin/python scripts/run_eval.py
PYTHONPATH=src:scripts /tmp/ics_venv/bin/python scripts/run_model_track.py

# 5. 启动服务（需要会话密钥、LLM 与 embedding 的 OpenAI-compatible 端点）
export ICS_SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(40))')"
export ICS_LLM_BASE_URL=http://127.0.0.1:11434/v1
export ICS_EMBEDDING_BASE_URL=http://127.0.0.1:11434/v1
PYTHONPATH=src /tmp/ics_venv/bin/python -m kms.cli

# M0 探测（会重建 public schema）
/tmp/ics_venv/bin/python scripts/run_m0_probes.py

# 门禁
/tmp/ics_venv/bin/python scripts/g1_content_gates.py
/tmp/ics_venv/bin/python scripts/g2_reconcile_gate.py
/tmp/ics_venv/bin/python scripts/g3_release_gate.py

# 夜间对账 / M8 决策
/tmp/ics_venv/bin/python scripts/nightly_reconcile.py
/tmp/ics_venv/bin/python scripts/m8_evaluate.py

# 观测栈（独立 compose；应用仍由 kms.cli 启动）
docker compose -f docker/observability-compose.yml up -d
```

## 目录

- `src/kms/scope.py`：FilterAST、唯一谓词渲染、contextvars、写路径护栏
- `src/kms/storage`：三个存储子类，类名保持 `PGKVStorage` / `PGVectorStorage` /
  `PGTableGraphStorage`，只重指向 `lightrag.kg.STORAGES`
- `src/kms/authority`：A.1–A.5 DDL、投影加列、校验与冲突
- `src/kms/publish`：发布流水线、chunk_filter 版本行、图/候选 scope、对账
- `src/kms/query`：八步链路、L2、L3、生成、缓存、NLU 关键词
- `src/kms/observability`：指标、OTel、Langfuse 适配
- `src/kms/api/session.py`：HMAC 会话验签（请求体不承载授权维度）
- `src/kms/api/admin.py`：只读管理面（只查 `knowledge` / `knowledge_version` / `chunk_filter`）
- `src/kms/query/nlu.py`：`LlmKeywordProvider`，未配置时回退词切分
- `src/kms/runtime.py` / `src/kms/cli.py`：多 workspace 装配与服务入口
- `scripts/run_eval.py` / `scripts/run_model_track.py`：确定性评测 / LLM-as-judge 模型轨
- `eval`：离线评测（黄金集、指标、门禁、生命周期）
- `ci/ci.sh`：固定门禁链；`.github/workflows/ci.yml` 为 PR CI

## 关键约束（实现口径）

- 写路径（upsert/delete/drop/flush）入口断言 `scope_var is None`；查询期 scope 只在 `contextvars` 中。
- `chunk_filter` 以 `(chunk_id, kid_version)` 为主键保留历史；发布事务写版本行 + 两表回填 + 代次推进。
- L2 只裁决授权四维 + 有效期；偏好维只打点，不进 prompt。
- 外部 rerank 默认关闭（`ICS_ENABLE_RERANK=false`）；白名单确认前不得开启。
- `POSTGRES_WORKSPACE` 必须为空，启动自检会拒绝非空值。

## 与方案不一致项（M0 实测）

1. 本机 1.5.7 构建把 `process_options` 放在 `apipeline_enqueue_documents`；
   `apipeline_process_enqueue_documents()` 不接受该参数。
2. 图存储删边方法是 `remove_edges`（不是 `delete_edge`），写路径护栏按实际方法名覆盖。
3. 附录 B 行号基于另一构建；本机行号见 `reports/m0/final/02-signatures.json`。
