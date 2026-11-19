"""PaddleOCR 的子进程入口：只在装有 paddleocr 的独立环境里运行，不被主程序直接 import。

用法：python ocr_paddle_worker.py <输入pdf> <输出json> <语种> <渲染倍率>
输出：{"pages": [每页文本], "scores": [[每行置信度]...]}
"""

import json
import sys
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
from paddleocr import PaddleOCR

sys.path.insert(0, str(Path(__file__).resolve().parent))  # 独立环境里按脚本所在目录找同级模块
from ocr_layout import reading_order  # noqa: E402
from ocr_preprocess import suppress_watermark  # noqa: E402

MAX_LONG_SIDE = 3000.0  # 渲染后长边的像素上限
pdf_path, out_path, lang, scale = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
# 第 5 个参数 raw：不压水印（正文本身浅灰的页，压水印会把正文一起抹掉；重读一页时两种都试）
keep_gray = len(sys.argv) > 5 and sys.argv[5] == "raw"
ocr = PaddleOCR(
    lang=lang,
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
    enable_mkldnn=False,
)
doc = pdfium.PdfDocument(pdf_path)
pages, scores = [], []

for i in range(len(doc)):
    width, height = doc[i].get_size()
    # 页面本身很大（如 1275×1754 点的扫描页）时，按默认倍率会渲染出两千万像素，识别在一页上就超时；
    # 长边封顶，普通 A4 页（长边约 2500 像素）不受影响
    effective = min(scale, MAX_LONG_SIDE / max(width, height, 1.0))
    pil = doc[i].render(scale=effective).to_pil()
    cleaned = None if keep_gray else suppress_watermark(np.array(pil.convert("L")))
    image = cleaned if cleaned is not None else np.array(pil.convert("RGB"))
    texts, page_scores = [], []
    for result in ocr.predict(image):
        page_texts = list(result["rec_texts"])
        page_conf = [float(s) for s in result["rec_scores"]]
        boxes = result.get("rec_boxes") if hasattr(result, "get") else None
        if boxes is not None and len(boxes) == len(page_texts):
            order = reading_order(
                [tuple(float(v) for v in b) for b in boxes],
                float(image.shape[1]),
                rtl=lang in ("ar", "fa", "ur", "arabic"),
            )
            page_texts = [page_texts[k] for k in order]
            page_conf = [page_conf[k] for k in order]
        texts += page_texts
        page_scores += page_conf
    pages.append("\n".join(t for t in texts if t.strip()))
    scores.append(page_scores)
    print(f"page {i + 1}/{len(doc)}", file=sys.stderr, flush=True)
with open(out_path, "w", encoding="utf-8") as f:
    json.dump({"pages": pages, "scores": scores}, f, ensure_ascii=False)
