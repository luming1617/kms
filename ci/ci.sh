#!/usr/bin/env bash
# 固定门禁链：ruff → format → mypy → pytest+cov → bandit → pip-audit
# B608 为 SQL 表名/列名拼接的静态误报：表名均来自常量或正则校验，取值一律走 $n 参数。
set -euo pipefail
export PYTHONPATH="${PYTHONPATH:-}:src:scripts"
ruff check src tests scripts eval
ruff format --check src tests scripts eval
mypy src
pytest tests -q -p no:cacheprovider \
  --cov=kms --cov-branch --cov-fail-under=85
bandit -r src -ll -s B608
pip-audit
