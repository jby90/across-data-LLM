# ruff: noqa: E501
"""PaddleOCR 引擎（Apache-2.0，可免费商用）：阿拉伯语等非拉丁文字的 OCR。

为什么单独放一个进程：PaddleOCR 依赖 paddlepaddle，体积大、与主环境的依赖会冲突，
所以在独立的 Python 环境里运行（配置 CYSH_PADDLE_PYTHON 指向该环境的解释器），
主程序通过子进程调用 worker 脚本，逐页返回文本和置信度。这样主环境不装、不背依赖，
没配置时 available 为 False，自动退回 Tesseract，行为和以前完全一致。

实测（埃及第 151 号法扫描件，31 页）：Tesseract 只识别出 11 页内容，PaddleOCR 识别出全部 31 页。
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# 识别很吃 CPU：同一台机器上同时识别多份文件，每页耗时会成倍增加（实测三份并行时，
# 28 页的文件超过 28 分钟仍未完成）。进程内同一时刻只识别一份，上层并发导入也不会把识别挤爆。
_RECOGNITION_LOCK = threading.Lock()

WORKER = Path(__file__).with_name("ocr_paddle_worker.py")
# PaddleOCR 的语种代码；没列出的语言不走这个引擎
# 阿拉伯文字用阿拉伯模型；法语用拉丁（法语）模型：本机没有 Tesseract 时，法语扫描件靠它才能入库
PADDLE_LANG = {"ar": "ar", "fa": "ar", "ur": "ar", "fr": "fr"}
# 识别子进程每完成一页会在标准错误里写一行“page i/n”。只要还在出页就不算超时；
# 连续这么久一页都出不来才判定卡死。不再用“每页秒数 × 页数”的总上限：页面文字密的文件
# 一页要好几分钟，按总上限会把正常前进的识别在中途杀掉，已做的页全部白费（实测 28 页的电信监管法）。
STALL_SECONDS = int(os.environ.get("CYSH_OCR_STALL_SECONDS", "900"))


@dataclass
class PaddleResult:
    pages: list[str]
    mean_confidence: float  # 0-100
    languages: str


class PaddleOcrEngine:
    def __init__(self, python: str | None, dpi_scale: float = 3.0) -> None:
        self._python = python
        self._scale = dpi_scale

    @property
    def available(self) -> bool:
        return bool(self._python and Path(self._python).exists() and WORKER.exists())

    def supports(self, language: str) -> bool:
        return self.available and language.split("-")[0].lower() in PADDLE_LANG

    def run(self, content: bytes, language: str, *, keep_gray: bool = False) -> PaddleResult:
        """同步执行（调用方放到线程里）。全部页都识别，不做页数截断。"""
        lang = PADDLE_LANG[language.split("-")[0].lower()]
        with tempfile.TemporaryDirectory(prefix="cysh-paddle-") as tmp:
            pdf = Path(tmp) / "in.pdf"
            out = Path(tmp) / "out.json"
            pdf.write_bytes(content)
            # 同一进程内一次只识别一份：并行识别会让每页耗时成倍增加，超时后整份白做
            with _RECOGNITION_LOCK:
                code, tail = _run_worker(
                    [self._python, str(WORKER), str(pdf), str(out), lang, str(self._scale), *(["raw"] if keep_gray else [])],
                    env=_clean_env(),
                    stall_seconds=STALL_SECONDS,
                )
            if code != 0 or not out.exists():
                raise RuntimeError("PaddleOCR 执行失败: " + tail[-300:])
            data = json.loads(out.read_text(encoding="utf-8"))
        scores = [s for page in data["scores"] for s in page]
        mean = statistics.fmean(scores) * 100 if scores else 0.0
        return PaddleResult(
            pages=data["pages"], mean_confidence=round(mean, 1), languages="paddle:" + lang
        )


def _run_worker(cmd: list[str], *, env: dict[str, str], stall_seconds: float) -> tuple[int, str]:
    """运行识别子进程，返回 (退出码, 末尾日志)。进度（输出）停滞超过 stall_seconds 才终止。"""
    proc = subprocess.Popen(  # noqa: S603 - 解释器与脚本均来自配置
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",  # 子进程日志里的非 UTF-8 字节不能让识别失败
    )
    state = {"last": time.monotonic(), "tail": ""}

    def drain() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            if line.strip().startswith("page "):
                state["last"] = time.monotonic()  # 出了一页：进度前进
            state["tail"] = (state["tail"] + line)[-2000:]

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    while proc.poll() is None:
        time.sleep(min(1.0, stall_seconds / 4))
        if time.monotonic() - state["last"] > stall_seconds:
            proc.kill()
            reader.join(timeout=5)
            raise RuntimeError(f"PaddleOCR 连续 {int(stall_seconds)} 秒没有完成任何一页，已终止")
    reader.join(timeout=5)
    return proc.returncode, state["tail"]


def _clean_env() -> dict[str, str]:
    env = dict(os.environ)
    # Windows CPU 上 oneDNN 路径会报 ConvertPirAttribute2RuntimeAttribute；关闭后稳定
    env.update(FLAGS_use_mkldnn="0", PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK="True", PYTHONUTF8="1")
    return env
