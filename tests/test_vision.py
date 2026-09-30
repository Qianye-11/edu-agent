"""图片与公式分析：全 mock，不发送真实图片或网络请求。"""
from __future__ import annotations

import json
import asyncio
import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import UploadFile

from edu_agent import vision
from edu_agent import web_server as ws


PNG = b"\x89PNG\r\n\x1a\n" + b"fake-png-body"


class _Response:
    def __init__(self, payload):
        self._payload = payload
        self.text = json.dumps(payload, ensure_ascii=False)

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _settings(**overrides):
    base = dict(
        vision_ready=True,
        vision_api_key="sk-test",
        vision_base_url="https://vision.example/v1",
        vision_model="vision-test",
        vision_max_bytes=1024,
        vision_timeout=30,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestVisionInput(unittest.TestCase):
    def test_detects_actual_content_not_filename(self):
        self.assertEqual(vision.detect_mime(PNG), "image/png")
        self.assertIsNone(vision.detect_mime(b"not-image"))

    def test_rejects_unsupported_and_oversize_images(self):
        with patch.object(vision, "load_settings", return_value=_settings(vision_max_bytes=10)):
            with self.assertRaisesRegex(vision.VisionError, "超过上限"):
                vision.validate_image(PNG, "fake.jpg")
        with patch.object(vision, "load_settings", return_value=_settings()):
            with self.assertRaisesRegex(vision.VisionError, "仅支持"):
                vision.validate_image(b"hello", "fake.png")


class TestFormulaChecks(unittest.TestCase):
    def test_numeric_and_identity_checks(self):
        self.assertEqual(vision.check_formula("2+2=4")["status"], "numeric_true")
        self.assertEqual(vision.check_formula("(x+1)^2=x^2+2*x+1")["status"], "identity_true")

    def test_unsafe_formula_is_not_parsed(self):
        self.assertEqual(vision.check_formula("__import__('os')")["status"], "unsupported")


class TestVisionAnalysis(unittest.TestCase):
    def test_multimodal_request_and_structured_result(self):
        model_result = {
            "image_type": "formula",
            "summary": "一元二次方程",
            "ocr_text": "解方程",
            "formulas": [{"latex": "x^2=4", "plain": "x^2=4", "confidence": 0.95}],
            "analysis": "移项并开方。",
            "answer": "x=2 或 x=-2",
            "warnings": [],
            "confidence": 0.9,
        }
        payload = {"choices": [{"message": {"content": "```json\n" +
                    json.dumps(model_result, ensure_ascii=False) + "\n```"}}]}
        captured = {}

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return _Response(payload)

        with patch.object(vision, "load_settings", return_value=_settings()), \
                patch.object(vision.requests, "post", side_effect=fake_post):
            result = vision.analyze_image(PNG, "题目.png", "请解题")
        self.assertEqual(result["image_type"], "formula")
        self.assertEqual(result["formula_checks"][0]["status"], "parsed_equation")
        self.assertEqual(captured["url"], "https://vision.example/v1/chat/completions")
        content = captured["json"]["messages"][0]["content"]
        image = next(x for x in content if x["type"] == "image_url")
        self.assertTrue(image["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertNotIn("sk-test", json.dumps(captured["json"]))

    def test_non_json_model_output_degrades_to_answer(self):
        parsed = vision.normalize_result(vision._parse_json("识别到函数图象，但坐标轴不清楚。"))
        self.assertIn("函数图象", parsed["answer"])
        self.assertLess(parsed["confidence"], 0.5)

    def test_markdown_contains_formula_and_warning(self):
        md = vision.to_markdown({
            "image_type": "formula", "confidence": 0.8, "summary": "题目",
            "ocr_text": "求值", "formulas": [{"plain": "2+2=4", "confidence": 0.9}],
            "analysis": "计算", "answer": "4", "warnings": ["图片略模糊"],
            "formula_checks": [{"input": "2+2=4", "status": "numeric_true"}],
        }, "题目.png")
        self.assertIn("2+2=4", md)
        self.assertIn("数值等式成立", md)
        self.assertIn("图片略模糊", md)


class TestVisionApi(unittest.TestCase):
    def test_upload_endpoint_honors_permission_gate(self):
        upload = UploadFile(filename="题目.png", file=io.BytesIO(PNG))
        gate = {"ok": False, "gate": "permission", "need": "confirm"}
        with patch("edu_agent.permissions.guard", return_value=gate):
            result = asyncio.run(ws.api_vision_analyze(upload, "请解题", "", False))
        self.assertEqual(result["gate"], "permission")

    def test_upload_endpoint_returns_markdown_and_session(self):
        upload = UploadFile(filename="题目.png", file=io.BytesIO(PNG))
        result = {"image_type": "formula", "confidence": 0.9, "summary": "方程",
                  "ocr_text": "", "formulas": [], "analysis": "", "answer": "x=2",
                  "warnings": [], "formula_checks": []}
        with patch("edu_agent.permissions.guard", return_value=None), \
                patch.object(vision, "analyze_image", return_value=result), \
                patch.object(ws, "_vision_session", return_value="vision-session"):
            response = asyncio.run(ws.api_vision_analyze(upload, "请解题", "", True))
        self.assertTrue(response["ok"])
        self.assertEqual(response["session_id"], "vision-session")
        self.assertIn("图片与公式分析", response["markdown"])


if __name__ == "__main__":
    unittest.main()
