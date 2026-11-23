"""OCR 引擎选择：阿拉伯文字两个引擎都跑，先比“读出通顺文字的页数”，再比条号，最后比字符数。
不再按 40 页静默截断。"""

import io
import sys

import pypdfium2 as pdfium
import pytest

from app.ingestion import ocr as ocr_mod
from app.ingestion.ocr import OcrEngine, OcrResult, OcrUnavailable
from app.ingestion.ocr_paddle import PaddleResult
from app.ingestion.page_quality import document_quality, page_is_usable


def blank_pdf(pages: int) -> bytes:
    doc = pdfium.PdfDocument.new()
    for _ in range(pages):
        doc.new_page(200, 200)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


class FakePaddle:
    def __init__(self, pages=None, available=True, fail=False):
        self.pages = pages or ["صفحة"] * 3
        self.available = available
        self.fail = fail
        self.calls = 0

    def supports(self, language):
        return self.available and language.split("-")[0] in ("ar", "fa", "ur")

    def run(self, content, language):
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        return PaddleResult(pages=self.pages, mean_confidence=88.0, languages="paddle:ar")


def engine(paddle: FakePaddle, tesseract_ok=False):
    e = OcrEngine(None, None, 300, None)
    e._paddle = paddle
    e._checked = tesseract_ok
    return e


def with_tesseract(e: OcrEngine, pages: list[str]) -> None:
    e._run_tesseract = lambda content, language, max_pages=None: OcrResult(
        pages=pages, mean_confidence=90.0, languages="ara+eng"
    )


# 一页“读通顺了”的阿拉伯语法律文字：词数够多、常用功能词占比正常
PROSE = " ".join(
    ["يجب على المتحكم أن يقوم بحماية البيانات الشخصية التي يحوزها وفقا لما ورد في هذا القانون"] * 8
)
GARBAGE = " ".join(["ححخ ععغ ططظ صصض ذذز ببت"] * 40)  # 词够多但全是没有意义的碎片
HEADINGS_ONLY = "\n".join(f"الفصل {n} نص" for n in range(1, 40))  # 条号很多，但每页内容极少


def test_a_page_counts_as_usable_only_if_it_has_running_prose():
    assert page_is_usable(PROSE, "ar")
    assert not page_is_usable("", "ar")  # 整页读空
    assert not page_is_usable("الفصل 1 نص", "ar")  # 只有一个标题
    assert not page_is_usable(GARBAGE, "ar")  # 词数够，但不通顺


def test_quality_orders_usable_pages_before_article_numbers_before_characters():
    prose_pages = [PROSE] * 5
    assert document_quality(prose_pages, "ar")[0] == 5
    # 同样可用页数时，条号多的更好
    with_numbers = [PROSE + "\nالفصل 1 نص", PROSE + "\nالفصل 2 نص"] + [PROSE] * 3
    assert document_quality(with_numbers, "ar") > document_quality(prose_pages, "ar")


def test_engine_with_many_usable_pages_beats_one_with_a_few_extra_article_numbers():
    """埃及的真实情形：Tesseract 读到的条号多一点，但 31 页里 25 页整页读空；PaddleOCR 读通顺了 29 页。
    只数条号会把空页的那份选进库里。"""
    paddle = FakePaddle(pages=[PROSE] * 29 + ["", ""])
    e = engine(paddle, tesseract_ok=True)
    with_tesseract(e, [PROSE] * 6 + [HEADINGS_ONLY] + [""] * 24)
    assert e.run(b"%PDF", "ar").languages == "paddle:ar"


def test_engine_that_keeps_article_numbers_wins_when_both_read_the_pages():
    """突尼斯的真实情形：页面都读出来了，但 PaddleOCR 漏了条号，原有引擎保住了条号。"""
    no_numbers = [PROSE] * 9
    with_numbers = [PROSE + f"\nالفصل {i} نص" for i in range(1, 10)]
    e = engine(FakePaddle(pages=no_numbers), tesseract_ok=True)
    with_tesseract(e, with_numbers)
    assert e.run(b"%PDF", "ar").languages == "ara+eng"


def test_tie_goes_to_the_one_with_more_text():
    e = engine(FakePaddle(pages=[PROSE * 2]), tesseract_ok=True)
    with_tesseract(e, [PROSE])
    assert e.run(b"%PDF", "ar").languages == "paddle:ar"


def test_arabic_without_tesseract_uses_paddle():
    paddle = FakePaddle()
    e = engine(paddle, tesseract_ok=False)
    assert e.available  # 有 Paddle 就算可用
    res = e.run(b"%PDF", "ar")
    assert paddle.calls == 1 and res.languages == "paddle:ar" and len(res.pages) == 3


def test_paddle_failure_falls_back_to_tesseract():
    e = engine(FakePaddle(fail=True), tesseract_ok=True)
    with_tesseract(e, [PROSE])
    assert e.run(b"%PDF", "ar").languages == "ara+eng"


def test_paddle_failure_without_tesseract_is_a_clear_error():
    with pytest.raises(OcrUnavailable):
        engine(FakePaddle(fail=True), tesseract_ok=False).run(b"%PDF", "ar")


def test_non_arabic_documents_never_touch_paddle():
    paddle = FakePaddle()
    e = engine(paddle, tesseract_ok=True)
    with_tesseract(e, ["Article 1"])
    e.run(b"%PDF", "fr")
    assert paddle.calls == 0


def test_no_engine_means_unavailable():
    e = engine(FakePaddle(available=False), tesseract_ok=False)
    assert not e.available
    with pytest.raises(OcrUnavailable):
        e.run(b"%PDF", "en")


def test_all_pages_are_recognised_without_the_old_40_page_cap(monkeypatch):
    seen = []

    def fake_to_string(image, lang, config):
        seen.append(1)
        return "text"

    monkeypatch.setattr(ocr_mod.pytesseract, "image_to_string", fake_to_string)
    monkeypatch.setattr(
        ocr_mod.pytesseract,
        "image_to_data",
        lambda *a, **k: {"conf": ["90"], "text": ["word"]},
    )
    e = engine(FakePaddle(available=False), tesseract_ok=True)
    e._cmd = __file__  # available 只检查路径存在
    res = e.run(blank_pdf(45), "en")
    assert len(res.pages) == 45 and len(seen) == 45
    # 显式传入上限仍然有效（仅供人工限制或测试）
    seen.clear()
    assert len(e.run(blank_pdf(45), "en", max_pages=5).pages) == 5


def test_the_paddle_wrapper_covers_arabic_script_and_french_but_not_other_languages():
    import sys

    from app.ingestion.ocr_paddle import PaddleOcrEngine

    e = PaddleOcrEngine(sys.executable)
    assert e.supports("ar") and e.supports("fa") and e.supports("fr-FR")
    assert not e.supports("en") and not e.supports("zh")
    assert not PaddleOcrEngine(None).supports("fr")  # 没配置解释器就不可用


def _stub(tmp_path, lines):
    script = tmp_path / "stub.py"
    script.write_text(chr(10).join(lines), encoding="utf-8")
    return [sys.executable, str(script)]


def test_slow_but_advancing_recognition_is_never_killed(tmp_path):
    from app.ingestion.ocr_paddle import _run_worker

    # 每页 0.6 秒、共 4 页（总共 2.4 秒），停滞上限只有 1.5 秒：总时长超过上限但一直在前进
    cmd = _stub(
        tmp_path,
        [
            "import sys, time",
            "for i in range(1, 5):",
            "    time.sleep(0.6)",
            "    print('page', i, file=sys.stderr, flush=True)",
        ],
    )
    code, tail = _run_worker(cmd, env={}, stall_seconds=1.5)
    assert code == 0 and "page 4" in tail


def test_a_recognition_that_stops_producing_pages_is_terminated(tmp_path):
    import pytest

    from app.ingestion.ocr_paddle import _run_worker

    cmd = _stub(
        tmp_path,
        [
            "import sys, time",
            "print('page 1', file=sys.stderr, flush=True)",
            "time.sleep(30)",
        ],
    )
    with pytest.raises(RuntimeError, match="没有完成任何一页"):
        _run_worker(cmd, env={}, stall_seconds=1.0)


def test_two_recognitions_never_run_at_the_same_time(monkeypatch, tmp_path):
    import json
    import threading
    import time

    from app.ingestion import ocr_paddle

    running, peak, lock = [0], [0], threading.Lock()

    def fake_run_worker(cmd, *, env, stall_seconds):
        out = cmd[3]
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.15)
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"pages": ["x"], "scores": [[0.9]]}, f)
        with lock:
            running[0] -= 1
        return 0, ""

    monkeypatch.setattr(ocr_paddle, "_run_worker", fake_run_worker)
    engine = ocr_paddle.PaddleOcrEngine(sys.executable)
    threads = [threading.Thread(target=engine.run, args=(b"%PDF", "ar")) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert peak[0] == 1  # 四份同时提交，识别始终一份一份来


def test_tesseract_streams_one_page_at_a_time_without_holding_all_bitmaps(monkeypatch):
    """回归：多页扫描件必须逐页渲染、识别后立刻释放位图，不得把整份 PDF 全部页同时驻留内存。"""
    import pytesseract

    live_images: list[object] = []
    peak_live = [0]

    orig_convert = None

    def wrap_image_to_string(img, **kwargs):
        assert getattr(img, "im", None) is not None
        return "Article 1\nValid text"

    def wrap_image_to_data(img, **kwargs):
        return {"conf": ["92", "90"], "text": ["Article", "1"]}

    from PIL import Image

    orig_convert = Image.Image.convert
    orig_close = Image.Image.close

    def tracked_convert(self, *a, **kw):
        out = orig_convert(self, *a, **kw)
        live_images.append(out)
        peak_live[0] = max(peak_live[0], len(live_images))
        return out

    def tracked_close(self):
        if self in live_images:
            live_images.remove(self)
        return orig_close(self)

    monkeypatch.setattr(Image.Image, "convert", tracked_convert)
    monkeypatch.setattr(Image.Image, "close", tracked_close)
    monkeypatch.setattr(pytesseract, "image_to_string", wrap_image_to_string)
    monkeypatch.setattr(pytesseract, "image_to_data", wrap_image_to_data)

    e = OcrEngine("tesseract", None, 150, None)
    e._checked = True
    res = e._run_tesseract(blank_pdf(8), "en")
    assert len(res.pages) == 8
    assert peak_live[0] == 1  # 8 页 PDF 任一时刻内存里最多只有 1 页灰度图
    assert len(live_images) == 0
