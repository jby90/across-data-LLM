"""Plain text out of a Japanese statute in the official e-Gov XML (the legally structured text).

The portal publishes each law as XML: chapters, sections, articles (``Article`` with
``ArticleTitle`` and ``ArticleCaption``), paragraphs, items. This writes it out one structural unit
per line, with the article heading ("第一条" plus its caption) on a line of its own, which is what
the clause splitter reads. The text itself is copied unchanged.
"""

from __future__ import annotations

from xml.etree import ElementTree

# element names that carry running text of a paragraph, item or sub-item
_TEXT_UNITS = {"ParagraphSentence", "ItemSentence", "Subitem1Sentence", "Subitem2Sentence"}
_STRUCTURE = {"Part", "Chapter", "Section", "Subsection", "Division"}


class EgovError(ValueError):
    pass


def is_egov_xml(content: bytes) -> bool:
    head = content[:400].lstrip()
    return head.startswith(b"<?xml") and b"<Law " in content[:1200]


def _text(node: ElementTree.Element) -> str:
    return "".join(node.itertext()).strip()


def _lines(node: ElementTree.Element, out: list[str]) -> None:
    tag = node.tag
    if tag in _STRUCTURE:
        title = node.find(f"{tag}Title")
        if title is not None:
            out.append(_text(title))
    elif tag == "Article":
        title = node.find("ArticleTitle")
        caption = node.find("ArticleCaption")
        heading = _text(title) if title is not None else ""
        if caption is not None:
            heading = f"{heading} {_text(caption)}".strip()
        if heading:
            out.append(heading)
    elif tag in _TEXT_UNITS:
        text = _text(node)
        if text:
            out.append(text)
        return
    elif tag == "ParagraphNum" or tag == "ItemTitle" or tag == "Subitem1Title":
        text = _text(node)
        if text:
            out.append(text)
        return
    for child in node:
        if child.tag in ("ArticleTitle", "ArticleCaption") or child.tag.endswith("Title"):
            if tag == "Article" or tag in _STRUCTURE:
                continue
        _lines(child, out)


def egov_lines(content: bytes) -> list[str]:
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise EgovError("无法读取 e-Gov 法令 XML") from exc
    body = root.find("LawBody")
    if body is None:
        raise EgovError("e-Gov 法令 XML 没有正文")
    out: list[str] = []
    title = body.find("LawTitle")
    if title is not None:
        out.append(_text(title))
    for child in body:
        if child.tag in ("LawTitle", "EnactStatement", "Preamble"):
            if child.tag != "LawTitle":
                out.append(_text(child))
            continue
        _lines(child, out)
    return out
