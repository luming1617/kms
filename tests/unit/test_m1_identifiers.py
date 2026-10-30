"""M1：doc_id ≡ kid，不做任何拼接。"""

from __future__ import annotations

import pytest

from src.kms.publish.ids import chunk_id_for, doc_id_for_kid


def test_doc_id_is_pure_kid() -> None:
    assert doc_id_for_kid("K-2024-0001") == "K-2024-0001"
    assert chunk_id_for("K-2024-0001", 7) == "K-2024-0001-chunk-007"


@pytest.mark.parametrize("kid", ["", " K1", "K1\n", "K 1 "])
def test_invalid_kid_rejected(kid: str) -> None:
    with pytest.raises(ValueError):
        doc_id_for_kid(kid)
