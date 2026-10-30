"""发布体渲染：content JSON → 可投影正文；确定性、无随机顺序。"""

from __future__ import annotations

from typing import Any


def render_content(content: dict[str, Any]) -> str:
    title = str(content.get("title", "")).strip()
    sections = content.get("sections") or []
    parts: list[str] = [title] if title else []
    for section in sections:
        heading = str(section.get("heading", "")).strip()
        body = str(section.get("body", "")).strip()
        parts.append(f"## {heading}" if heading else "##")
        if body:
            parts.append(body)
    return "\n\n".join(parts)
