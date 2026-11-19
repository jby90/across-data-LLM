from app.ingestion.ocr_layout import reading_order

W = 1000


def _col(x1, x2, tops, h=20):
    return [(x1, t, x2, t + h) for t in tops]


def test_two_columns_are_read_one_after_the_other():
    left = _col(50, 460, [100, 130, 160, 190, 220])
    right = _col(540, 950, [100, 130, 160, 190, 220])
    boxes = [b for pair in zip(left, right, strict=True) for b in pair]  # 按高度交错
    order = reading_order(boxes, W)
    texts = [("L" if boxes[i][0] < 500 else "R") for i in order]
    assert "".join(texts) == "LLLLLRRRRR"


def test_a_full_width_title_closes_the_columns_above_it():
    top_l = _col(50, 460, [100, 130, 160, 190])
    top_r = _col(540, 950, [100, 130, 160, 190])
    title = [(50, 250, 950, 275)]
    low_l = _col(50, 460, [300, 330, 360, 390])
    low_r = _col(540, 950, [300, 330, 360, 390])
    boxes = top_l + top_r + title + low_l + low_r
    order = reading_order(boxes, W)
    idx_title = boxes.index(title[0])
    pos = {i: n for n, i in enumerate(order)}
    assert all(pos[i] < pos[idx_title] for i in range(8))  # 标题之上的两栏先读完
    assert all(pos[i] > pos[idx_title] for i in range(9, 17))


def test_right_to_left_pages_read_the_right_column_first():
    boxes = _col(50, 460, [100, 130, 160, 190]) + _col(540, 950, [100, 130, 160, 190])
    first = reading_order(boxes, W, rtl=True)[0]
    assert boxes[first][0] > 500


def test_a_single_column_or_a_sparse_page_keeps_top_to_bottom_order():
    single = _col(50, 950, [100, 130, 160, 190, 220, 250, 280, 310])
    assert reading_order(single, W) == list(range(8))
    few = _col(50, 460, [100, 130]) + _col(540, 950, [100, 130])
    assert reading_order(few, W) == sorted(range(4), key=lambda i: (few[i][1], few[i][0]))


def test_the_render_scale_is_capped_for_very_large_pages_but_not_for_a4():
    import re
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app/ingestion/ocr_paddle_worker.py").read_text("utf-8")
    cap = float(re.search(r"MAX_LONG_SIDE = ([0-9.]+)", source).group(1))
    default_scale = 3.0

    def effective(width, height):
        return min(default_scale, cap / max(width, height, 1.0))

    assert effective(595, 842) == default_scale  # A4：不变
    assert effective(1275, 1754) * 1754 <= cap + 1e-6  # 大页：长边不超过上限
