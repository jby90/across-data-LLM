"""OCR:给没有文字层的扫描件 PDF 补文字(Tesseract,Apache-2.0)。

OCR 文本是机器识别结果,必然有错字、错号。因此:
- 版本记录 text_source='ocr' 和平均置信度,界面必须显著提示"需人工核对";
- OCR 文本不会冒充 PDF 文字层原文,只用于检索与初步切分。
"""

from __future__ import annotations

import logging
import os
import shutil
import statistics
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
import pytesseract

from app.ingestion.heading_sequence import repair_headings
from app.ingestion.ocr_paddle import PaddleOcrEngine
from app.ingestion.page_quality import document_quality

log = logging.getLogger(__name__)

# 文档语言 -> Tesseract 语言包。阿拉伯文/法文文件里常夹英文,带上 eng 提升混排识别。
LANG_MAP = {"en": "eng", "ar": "ara+eng", "fr": "fra+eng", "zh": "chi_sim+eng"}
LOW_CONFIDENCE = 60.0
MAX_OCR_PAGES = 40


class OcrUnavailable(Exception):
    pass


@dataclass
class OcrResult:
    pages: list[str]
    mean_confidence: float  # 0-100,按词平均
    languages: str


class OcrEngine:
    def __init__(
        self,
        cmd: str | None,
        tessdata_dir: str | None,
        dpi: int = 300,
        paddle_python: str | None = None,
    ) -> None:
        self._cmd = cmd or shutil.which("tesseract")
        self._tessdata = tessdata_dir
        self._dpi = dpi
        self._checked: bool | None = None
        self._paddle = PaddleOcrEngine(paddle_python)

    @property
    def available(self) -> bool:
        # 只要有一个引擎可用就算可用：阿拉伯语文档走 PaddleOCR 时不依赖本机 Tesseract
        return self._tesseract_available or self._paddle.available

    @property
    def _tesseract_available(self) -> bool:
        if self._checked is None:
            self._checked = False
            if self._cmd and Path(self._cmd).exists():
                try:
                    out = subprocess.run(
                        [self._cmd, "--version"], capture_output=True, text=True, timeout=15
                    )
                    self._checked = out.returncode == 0
                except (OSError, subprocess.SubprocessError):
                    self._checked = False
        return bool(self._checked)

    def _config(self) -> str:
        # 语言包目录通过 TESSDATA_PREFIX 传递(命令行里带路径,在 Windows 上会被 shlex 当成转义处理)
        return "--psm 3"

    def languages_for(self, language: str) -> str:
        return LANG_MAP.get(language.split("-")[0].lower(), "eng")

    def run(self, content: bytes, language: str, max_pages: int | None = None) -> OcrResult:
        """同步执行(调用方放到线程里)。默认识别全部页：以前单份最多 40 页、超出静默丢弃，
        会让长法规缺后半部分；现在逐页处理不设上限（max_pages 仅供测试和人工限制时显式传入）。

        阿拉伯文字文档有两个引擎时，两个都跑，择优，不凭印象指定谁更好。
        先比“读出成段通顺文字的页数”（整页读空最严重），再比识别出的条号数量，最后比字符数。
        实测两边各有输赢：埃及法 31 页里 PaddleOCR 读通顺 29 页、Tesseract 只有 6 页；
        突尼斯法恰好相反，PaddleOCR 把“الفصل”后面的编号漏掉了，Tesseract 保住了条号。"""
        return self._with_headings(self._pick(content, language, max_pages), language)

    def _pick(self, content: bytes, language: str, max_pages: int | None) -> OcrResult:
        if not self._paddle.supports(language):
            return self._run_tesseract(content, language, max_pages)
        paddle: OcrResult | None = None
        try:
            res = self._paddle.run(content, language)
            paddle = OcrResult(
                pages=res.pages, mean_confidence=res.mean_confidence, languages=res.languages
            )
        except Exception as exc:
            from app.core.jargon import scrub_engineering_jargon

            clean_exc = scrub_engineering_jargon(str(exc))
            if not self._tesseract_available:
                raise OcrUnavailable(
                    f"主版面识别引擎失败且无备用识别引擎可用: {clean_exc}"
                ) from exc
            log.warning("主版面识别引擎失败，切换备用识别引擎: %s", clean_exc[:200])
        if paddle is None:
            return self._run_tesseract(content, language, max_pages)
        if not self._tesseract_available:
            return paddle
        tess = self._run_tesseract(content, language, max_pages)
        qp = document_quality(paddle.pages, language)
        qt = document_quality(tess.pages, language)
        winner = paddle if qp > qt else tess
        log.info(
            "版面识别择优：主引擎（可用页 %d / 条号 %d / 字符 %d），"
            "备用引擎（可用页 %d / 条号 %d / 字符 %d），选用 %s",
            *qp,
            *qt,
            winner.languages,
        )
        return winner

    @staticmethod
    def _with_headings(result: OcrResult, language: str) -> OcrResult:
        """阿拉伯文法规的条标题常只读出“ماده”而丢了编号：按条号逐条递增的规律补回，让条款能切分、能引用。"""
        if language.split("-")[0].lower() not in ("ar", "fa", "ur"):
            return result
        pages, stats = repair_headings(result.pages)
        if stats.found:
            log.info(
                "条标题补全：找到 %d 条（读出 %d、推出 %d），末条 %d",
                stats.found,
                stats.from_anchor,
                stats.inferred,
                stats.last,
            )
        return OcrResult(
            pages=pages, mean_confidence=result.mean_confidence, languages=result.languages
        )

    def read_page(self, content: bytes, page: int, language: str, *, dpi: int = 450) -> str:
        """One page again, at a higher resolution, in the law's own language only (no English mixed
        in), with the page's own layout: for a page the first reading could not make out."""
        from app.ingestion.parsing import _PDFIUM_LOCK

        if self._paddle.supports(language) and self._paddle.available:  # 本机阿拉伯文等走 PaddleOCR
            import io as _io

            buf = _io.BytesIO()
            with _PDFIUM_LOCK:
                one = pdfium.PdfDocument(content)
                single = None
                try:
                    single = pdfium.PdfDocument.new()
                    single.import_pages(one, [page - 1])
                    single.save(buf)
                finally:
                    if single is not None:
                        single.close()
                    one.close()
            from app.ingestion.quality_card import _readable_ratio

            hi = PaddleOcrEngine(self._paddle._python, dpi_scale=dpi / 72)
            best, best_score = "", -1.0
            for keep_gray in (False, True):  # 压水印与不压水印各读一遍，取读得通的那个
                text = "\n".join(hi.run(buf.getvalue(), language, keep_gray=keep_gray).pages)
                score = (_readable_ratio([text], language) or 0) * min(len(text), 2000)
                if score > best_score:
                    best, best_score = text, score
            return best
        if not self._tesseract_available:
            raise OcrUnavailable("未找到可用的版面文字识别引擎")
        assert self._cmd is not None
        pytesseract.pytesseract.tesseract_cmd = self._cmd
        if self._tessdata:
            os.environ["TESSDATA_PREFIX"] = self._tessdata
        lang = self.languages_for(language).split("+")[0]
        with _PDFIUM_LOCK:
            doc = pdfium.PdfDocument(content)
            page_obj = None
            bitmap = None
            try:
                page_obj = doc[page - 1]
                bitmap = page_obj.render(scale=dpi / 72)
                image = bitmap.to_pil().convert("L")
            finally:
                if bitmap is not None:
                    bitmap.close()
                if page_obj is not None:
                    page_obj.close()
                doc.close()
        best = ""
        for psm in ("--psm 6", "--psm 4", "--psm 3"):
            text = pytesseract.image_to_string(image, lang=lang, config=psm)
            text = "\n".join(ln.rstrip() for ln in text.splitlines() if ln.strip())
            if len(text) > len(best):
                best = text
        return best

    def _run_tesseract(
        self, content: bytes, language: str, max_pages: int | None = None
    ) -> OcrResult:
        from app.ingestion.parsing import _PDFIUM_LOCK

        if not self._tesseract_available:
            raise OcrUnavailable("未找到可用的版面文字识别引擎")
        assert self._cmd is not None
        pytesseract.pytesseract.tesseract_cmd = self._cmd
        if self._tessdata:
            os.environ["TESSDATA_PREFIX"] = self._tessdata
        langs = self.languages_for(language)
        pages: list[str] = []
        confs: list[float] = []
        pil_images = []
        with _PDFIUM_LOCK:
            doc = pdfium.PdfDocument(content)
            try:
                page_limit = len(doc) if max_pages is None else min(len(doc), max_pages)
                for i in range(page_limit):
                    page_obj = doc[i]
                    bitmap = None
                    try:
                        bitmap = page_obj.render(scale=self._dpi / 72)
                        pil_images.append(bitmap.to_pil().convert("L"))
                    finally:
                        if bitmap is not None:
                            bitmap.close()
                        page_obj.close()
            finally:
                doc.close()
        for image in pil_images:
            text = pytesseract.image_to_string(image, lang=langs, config=self._config())
            data = pytesseract.image_to_data(
                image,
                lang=langs,
                config=self._config(),
                output_type=pytesseract.Output.DICT,
            )
            confs.extend(
                float(c)
                for c, w in zip(data["conf"], data["text"], strict=False)
                if str(w).strip() and float(c) >= 0
            )
            pages.append("\n".join(ln.rstrip() for ln in text.splitlines() if ln.strip()))
        mean = statistics.fmean(confs) if confs else 0.0
        return OcrResult(pages=pages, mean_confidence=round(mean, 1), languages=langs)
