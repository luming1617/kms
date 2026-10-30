"""生命周期用例：发布→可检索、改版→V2、撤回→不可检索、迟到写不覆盖。"""

from __future__ import annotations

from dataclasses import dataclass

from src.kms.authority.models import CardDraft
from src.kms.publish.pipeline import PublishFailed, PublishPipeline
from src.kms.query.pipeline import QueryService
from src.kms.scope import SessionScope


@dataclass(frozen=True, slots=True)
class LifecycleOutcome:
    current_marker: str | None
    old_marker_retrievable: bool
    withdrawn_retrievable: bool
    late_write_overrode: bool


async def run_lifecycle_case(
    *,
    pipeline: PublishPipeline,
    service: QueryService,
    draft_v1: CardDraft,
    draft_v2: CardDraft,
    session: SessionScope,
    question: str,
) -> LifecycleOutcome:
    await pipeline.publish(draft_v1, expected_version=0)
    first = await service.answer(session, question)
    assert first.answered

    await pipeline.publish(draft_v2, expected_version=1)
    second = await service.answer(session, question)
    current = "CANARY-V2" if "CANARY-V2" in second.answer else None
    old_retrievable = "CANARY-V1" in second.answer

    await pipeline.withdraw(draft_v2.kid, language=draft_v2.language)
    third = await service.answer(session, question)
    withdrawn_retrievable = third.answered

    # 迟到写：V1 在 V2 后到达，expected_version=1 与 current=2 不符必须拒绝
    late_overrode = False
    try:
        await pipeline.publish(draft_v1, expected_version=1)
        fourth = await service.answer(session, question)
        late_overrode = "CANARY-V1" in fourth.answer
    except PublishFailed:
        late_overrode = False
    return LifecycleOutcome(
        current_marker=current,
        old_marker_retrievable=old_retrievable,
        withdrawn_retrievable=withdrawn_retrievable,
        late_write_overrode=late_overrode,
    )
