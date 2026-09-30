"""上传与下载边界回归：这些测试不需要 Ollama、Chroma 或真实 Office。"""
from __future__ import annotations

import asyncio
import io
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi import UploadFile

_PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJ / "src"))

from edu_agent import pdf_upload  # noqa: E402
from edu_agent import web_server as ws  # noqa: E402


class TestPdfPathSafety(unittest.TestCase):
    def test_client_filename_never_becomes_a_path(self):
        self.assertEqual(pdf_upload.safe_filename("../../secrets.pdf"), "secrets.pdf")
        self.assertEqual(pdf_upload.safe_filename(r"C:\\temp\\教材.pdf"), "教材.pdf")
        self.assertEqual(pdf_upload.safe_filename("../../evil.exe"), "evil.pdf")

    def test_stored_path_stays_inside_pdf_root(self):
        with tempfile.TemporaryDirectory() as d:
            old = pdf_upload.PDF_DIR
            pdf_upload.PDF_DIR = Path(d) / "pdfs"
            pdf_upload.PDF_DIR.mkdir()
            try:
                path = pdf_upload.stored_path("../../outside.pdf")
                self.assertEqual(path.parent, pdf_upload.PDF_DIR.resolve())
                self.assertEqual(path.name, "outside.pdf")
            finally:
                pdf_upload.PDF_DIR = old

    def test_existing_pdf_can_move_between_personal_and_official_library(self):
        with tempfile.TemporaryDirectory() as d:
            old_meta = pdf_upload.META
            pdf_upload.META = Path(d) / "pdf_sources.json"
            try:
                pdf_upload._save_meta([{"source": "math", "file": "数学.pdf"}])
                self.assertEqual(pdf_upload.set_library("math", "official"),
                                 {"ok": True, "source": "math", "library": "official"})
                self.assertEqual(pdf_upload.list_sources()[0]["library"], "official")
                self.assertEqual(pdf_upload.set_library("math", "personal")["library"], "personal")
                with self.assertRaisesRegex(ValueError, "归属"):
                    pdf_upload.set_library("math", "shared")
            finally:
                pdf_upload.META = old_meta


class TestUploadAndDownloadGuards(unittest.TestCase):
    def test_oversize_upload_is_rejected_before_full_read(self):
        upload = UploadFile(filename="big.pdf", file=io.BytesIO(b"12345"))
        with self.assertRaisesRegex(ValueError, "超过上限"):
            asyncio.run(ws._read_upload_limited(upload, 4))

    def test_download_token_is_one_time_and_not_a_path(self):
        with tempfile.TemporaryDirectory() as d:
            exported = Path(d) / "lesson.docx"
            exported.write_bytes(b"PK")
            token = ws.register_download(exported)
            response = ws.api_wps_download(token)
            self.assertEqual(Path(response.path), exported)
            expired = ws.api_wps_download(token)
            self.assertEqual(expired.status_code, 404)
            arbitrary = ws.api_wps_download(str(exported))
            self.assertEqual(arbitrary.status_code, 404)
