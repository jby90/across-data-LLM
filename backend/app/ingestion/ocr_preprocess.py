"""扫描页预处理：压掉斜向水印。只依赖 numpy，主程序和 PaddleOCR 独立环境都能用。

水印是浅灰色，正文是黑色。固定取“比灰度 130 更深”的像素，水印被压成白底。
实测（人工逐字转写对照，词级准确率）：埃及法第 26 页 89% → 95%，第 14 页 80% → 95%；
处理后水印穿过的文字行明显减少读错的词。

只对检测到水印的页启用：水印页的灰带像素（灰度 150～225）约为深色像素的 4～5 倍，
干净扫描页只有 0.7～1.1 倍（突尼斯、摩洛哥实测）。
固定阈值对干净页没有收益，还可能吃掉浅色笔画，所以不动。
"""

from __future__ import annotations

import numpy as np

WATERMARK_RATIO = 2.5
INK_LEVEL = 130


def watermark_ratio(gray: np.ndarray) -> float:
    dark = int((gray < INK_LEVEL).sum())
    band = int(((gray >= 150) & (gray < 225)).sum())
    return band / max(dark, 1)


def suppress_watermark(gray: np.ndarray) -> np.ndarray | None:
    """检测到水印返回处理后的三通道图，否则返回 None（调用方用原图）。"""
    if watermark_ratio(gray) <= WATERMARK_RATIO:
        return None
    ink = np.where(gray < INK_LEVEL, 0, 255).astype(np.uint8)
    return np.stack([ink] * 3, axis=-1)
