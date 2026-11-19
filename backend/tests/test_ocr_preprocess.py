"""水印页预处理：只处理有水印的页，水印被压掉、黑色笔画保留；干净页原样不动。"""

import numpy as np

from app.ingestion.ocr_preprocess import suppress_watermark, watermark_ratio


def _page(with_watermark: bool) -> np.ndarray:
    page = np.full((400, 300), 255, dtype=np.uint8)
    for row in range(40, 360, 40):  # 正文：黑色横条
        page[row : row + 6, 30:270] = 10
    if with_watermark:
        for k in range(400):  # 斜向浅灰色水印，面积远大于正文笔画
            for width in range(-30, 30):
                x = k * 3 // 4 + width
                if 0 <= x < 300:
                    page[k, x] = 190
    return page


def test_watermark_page_is_detected_and_only_ink_survives():
    page = _page(with_watermark=True)
    assert watermark_ratio(page) > 2.5
    cleaned = suppress_watermark(page)
    assert cleaned is not None and cleaned.shape == (400, 300, 3)
    assert (cleaned[..., 0] == 0).sum() == (page < 130).sum()  # 黑色笔画全部保留
    assert not ((cleaned[..., 0] == 0) & (page >= 130)).any()  # 灰色水印一个像素都不剩


def test_clean_page_is_left_alone():
    page = _page(with_watermark=False)
    assert watermark_ratio(page) < 2.5
    assert suppress_watermark(page) is None


def test_blank_page_does_not_divide_by_zero():
    assert suppress_watermark(np.full((50, 50), 255, dtype=np.uint8)) is None
