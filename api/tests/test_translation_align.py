from app.worker.translation_align import (
    idxs_needing_retranslate,
    target_missing_expected_script,
    translation_expanded_from_short_source,
    translation_too_long_for_slot,
    translation_too_short_for_source,
)


def test_korean_target_without_hangul_is_untranslated() -> None:
    assert target_missing_expected_script("Trong khu rung xanh", "ko")
    assert not target_missing_expected_script("어른 찾아라", "ko")


def test_long_source_stub_target_is_misassigned() -> None:
    source = "Trong khu rừng xanh có một ngôi nhà hình cây nấm. Ở đó sống chú thỏ đội mũ."
    assert translation_too_short_for_source(source, "어른 찾아라")
    assert not translation_too_short_for_source("Tìm người lớn", "어른 찾아라")


def test_paragraph_on_short_slot_is_document_dump() -> None:
    paragraph = "푸른 숲속에 버섯모양의 집이 있어요. 그 곳엔 토끼 모자가 살고 있었죠."
    assert translation_too_long_for_slot(paragraph, "ko", 1.2)
    assert translation_expanded_from_short_source("Tìm người lớn", paragraph)
    assert not translation_too_long_for_slot("어른 찾아라", "ko", 1.2)


def test_idxs_needing_retranslate_catches_untranslated_and_stub() -> None:
    items = [
        (0, "Trong khu rừng xanh có một ngôi nhà hình cây nấm.", 4.0),
        (1, "Tìm người lớn", 1.0),
    ]
    translated = {
        0: "어른 찾아라",
        1: "Trong khu rung xanh co mot ngoi nha",
    }
    assert idxs_needing_retranslate(items, translated, "vi", "ko") == [0, 1]
