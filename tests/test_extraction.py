"""Unit tests for strict JSON extraction."""

from __future__ import annotations

import unittest

import _bootstrap  # noqa: F401
from local_llm_gateway import JSONExtractionError
from local_llm_gateway.extraction import extract_json


class StrictExtractionTests(unittest.TestCase):
    def test_plain_object(self):
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})

    def test_plain_array(self):
        self.assertEqual(extract_json("[1, 2, 3]"), [1, 2, 3])

    def test_surrounding_whitespace(self):
        self.assertEqual(extract_json('  \n {"a": 1} \n '), {"a": 1})

    def test_fenced_with_language(self):
        self.assertEqual(extract_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_fenced_without_language(self):
        self.assertEqual(extract_json('```\n{"a": 1}\n```'), {"a": 1})

    def test_fenced_inside_prose(self):
        text = '结果如下：\n```json\n{"amount": "100.00"}\n```\n请复核。'
        self.assertEqual(extract_json(text), {"amount": "100.00"})

    def test_broken_json_raises(self):
        with self.assertRaises(JSONExtractionError):
            extract_json('{"a": 1,}')

    def test_scalar_top_level_is_rejected(self):
        with self.assertRaises(JSONExtractionError):
            extract_json('"just a string"')
        with self.assertRaises(JSONExtractionError):
            extract_json("42")

    def test_prose_wrapped_object_rejected_by_default(self):
        with self.assertRaises(JSONExtractionError):
            extract_json('好的，结果是 {"a": 1}。')

    def test_prose_wrapped_object_allowed_on_opt_in(self):
        self.assertEqual(extract_json('好的，结果是 {"a": 1}。', allow_prose=True), {"a": 1})

    def test_empty_and_non_string_raise(self):
        with self.assertRaises(JSONExtractionError):
            extract_json("   ")
        with self.assertRaises(JSONExtractionError):
            extract_json(None)  # type: ignore[arg-type]

    def test_error_detail_is_bounded(self):
        try:
            extract_json("x" * 1000)
        except JSONExtractionError as exc:
            self.assertEqual(exc.detail["text_bytes"], 1000)
            self.assertNotIn("x" * 100, str(exc))
        else:  # pragma: no cover
            self.fail("expected JSONExtractionError")


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
