from app.ingestion.pdf_columns import column_text
from app.ingestion.reparse import _continuity


class _Page:
    def get_size(self):
        return (600.0, 800.0)


class _TextPage:
    """Glyph rectangles for lines of text, the way pdfium reports them (one box per glyph run)."""

    def __init__(self, lines):
        self.boxes = []
        self.texts = {}
        for text, x0, y_top in lines:
            width = 5.5 * len(text)
            box = (x0, 800.0 - y_top - 12.0, x0 + width, 800.0 - y_top)
            self.boxes.append(box)
            self.texts[box] = text

    def count_rects(self):
        # 每个字符一个矩形，行内字距很小
        self._rects = []
        for box, text in self.texts.items():
            step = (box[2] - box[0]) / len(text)
            for k in range(len(text)):
                self._rects.append((box[0] + k * step, box[1], box[0] + (k + 1) * step, box[3]))
        return len(self._rects)

    def get_rect(self, k):
        return self._rects[k]

    def get_text_bounded(self, left, bottom, right, top):
        for box, text in self.texts.items():
            if left <= box[0] + 1 and right >= box[2] - 1 and bottom <= box[3] and top >= box[1]:
                return text
        return ""


def _two_column_lines(rows=14):
    out = []
    for i in range(rows):
        top = 100.0 + i * 20
        out.append((f"Art. {i * 2 + 1} - texte de la colonne gauche numero {i}", 40.0, top))
        out.append((f"Art. {i * 2 + 2} - texte de la colonne droite numero {i}", 330.0, top))
    return out


def test_two_columns_are_read_left_column_first():
    page, text_page = _Page(), _TextPage(_two_column_lines())
    out = column_text(page, text_page)
    assert out is not None
    lines = out.splitlines()
    assert lines[0].startswith("Art. 1 -") and "gauche numero 0" in lines[0]
    assert "gauche numero 13" in lines[13] and "droite numero 0" in lines[14]


def test_a_single_column_page_keeps_the_plain_text():
    lines = [
        (f"Art. {i} - une seule colonne de texte qui occupe toute la largeur", 40.0, 100.0 + i * 20)
        for i in range(30)
    ]
    assert column_text(_Page(), _TextPage(lines)) is None


def test_a_broken_page_object_never_raises():
    class Broken:
        def count_rects(self):
            raise RuntimeError("pdfium")

    assert column_text(_Page(), Broken()) is None


def test_numbering_continuity_counts_each_number_once():
    class D:
        def __init__(self, n):
            self.clause_number = n

    assert _continuity([D(str(i)) for i in range(1, 11)]) == 1.0
    assert _continuity([D(n) for n in ("1", "2", "5", "6", "9", "10")]) == 0.6
    assert _continuity([D(None)] * 3) == 0.0
