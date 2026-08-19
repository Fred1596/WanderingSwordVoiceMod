from __future__ import annotations

import hashlib
import unittest

from bridge.synthesize_offline_dialogue import (
    batch_size_for,
    hit_codec_limit,
    max_new_tokens_for,
)
from catalog.build_offline_jobs import (
    make_tts_text,
    normalize_runtime,
    normalize_runtime_compat,
)


class RuntimeNormalizationTests(unittest.TestCase):
    def test_normalize_runtime_keeps_meaningful_newlines(self) -> None:
        self.assertEqual(normalize_runtime("  甲  \r\n  乙\t "), "甲\n乙")

    def test_compat_normalizes_full_width_punctuation(self) -> None:
        self.assertEqual(normalize_runtime_compat("ＡＢＣ！"), "ABC!")

    def test_runtime_key_is_stable(self) -> None:
        speaker = normalize_runtime("测试角色")
        text = normalize_runtime("这是一句合成测试台词。")
        first = hashlib.sha256(f"{speaker}\0{text}".encode("utf-8")).hexdigest()
        second = hashlib.sha256(f"{speaker}\0{text}".encode("utf-8")).hexdigest()
        self.assertEqual(first, second)


class TextPreparationTests(unittest.TestCase):
    def test_dialogue_text_is_preserved(self) -> None:
        self.assertEqual(make_tts_text("前路小心。"), ("前路小心。", "dialogue_text"))

    def test_question_marks_become_vocalization(self) -> None:
        self.assertEqual(make_tts_text("？？？"), ("嗯？", "nonverbal_vocalization"))

    def test_ellipsis_becomes_neutral_vocalization(self) -> None:
        self.assertEqual(make_tts_text("……"), ("嗯……", "nonverbal_vocalization"))


class GenerationGuardTests(unittest.TestCase):
    def test_batch_size_reduces_for_long_lines(self) -> None:
        self.assertEqual(batch_size_for(121, 20), 1)
        self.assertEqual(batch_size_for(90, 20), 2)
        self.assertEqual(batch_size_for(20, 20), 20)

    def test_codec_budget_is_bounded(self) -> None:
        short = max_new_tokens_for([{"tts_text": "你好"}])
        long = max_new_tokens_for([{"tts_text": "字" * 1000}])
        self.assertEqual(short, 96)
        self.assertEqual(long, 2048)

    def test_codec_boundary_detection(self) -> None:
        self.assertFalse(hit_codec_limit(7.0, 96))
        self.assertTrue(hit_codec_limit(7.6, 96))


if __name__ == "__main__":
    unittest.main()

