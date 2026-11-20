# ruff: noqa: E501  规则是长正则
"""What is not part of a clause, in any language: removed from the stored text (originals and their
Chinese translations, by the cleaning script) and again when a clause is shown or quoted
(``display_text.for_reading``), so a clause that comes in later is shown clean before it is cleaned.

Six kinds, one rule set for every country:

* web page furniture copied with the text (“学习强国”“返回顶部”“打印本页”“分享到”);
* download links and bare file names / addresses on a line of their own;
* gazette mastheads that ended up inside a sentence of the translation (“国家印刷事务总局 与原件相符的
  副本 埃及官方公报第 244 期 续于 9225 年11月1日”) — only a segment that names the printing office or
  the gazette *and* carries an issue or date is removed, so a sentence about certified copies stays;
* a publisher's postal address, telephone, fax and e-mail block (PDF letterheads);
* the heading of the next chapter or part left at the end of a clause (“第八章 / 直接电子营销”);
* in a Chinese translation, Arabic fragments the translator left untranslated (the currency word is
  translated, other fragments dropped; the original text keeps its Arabic).

Each function returns the cleaned text; ``what_changed`` says which kinds were found (for counts).
"""

from __future__ import annotations

import re

_NAV_TOKENS = re.compile(r"学习强国|返回顶部|打印本页|关闭窗口|第\s*\d+\s*页\s*[，,/]?\s*共\s*\d+\s*页|第\s*\d+\s*页\s*of\s*\d+|\bPage\s+\d+\s+of\s+\d+\b|【\s*字体[:：]?\s*大\s*中\s*小\s*】|【\s*打印\s*】|【\s*关闭\s*】")
_NAV_LINE = re.compile(
    r"^\s*(分享到[:：]?.*|扫一扫.{0,20}|相关链接[:：]?|附件下载[:：]?|下载附件[:：]?|上一篇[:：].*|下一篇[:：].*|责任编辑[:：].{0,20}|"
    r"来源[:：].{0,30}|浏览次数[:：].{0,10}|字号[:：]?\s*大\s*中\s*小|back to top|print this page|share this page)\s*$",
    re.IGNORECASE,
)
_LINK_LINE = re.compile(r"^\s*(附件\s*\d*\s*[:：]?\s*)?(\S+\.(pdf|docx?|xlsx?|zip|rar)|https?://\S+|www\.\S+)\s*$", re.IGNORECASE)

# 译文里夹进句子的公报页眉：印刷局 / 公报名 + 期号或日期，到日期（或页码）为止
_PRESS = r"(?:(?:国家|阿米里|阿拉伯埃及共和国国家)?印刷(?:事务)?(?:总局|管理局|局|事务局)|印刷事务总局|主管机构\s*与原件相符的副本)"
_GAZETTE = r"(?:(?:埃及|突尼斯共和国|黎巴嫩|摩洛哥)?(?:官方公报|官报|政府公报)|《?埃及(?:事实报|公报|官方公报)》?)"
_DATE = r"(?:续\s*)?(?:重复)?于\s*[\d\s]{1,9}年\s*\d{1,2}\s*月\s*(?:\d{1,2}\s*日)?(?:增刊|续)?"
_ISSUE = r"(?:第\s*\d+\s*[号期]|期号\s*\d+)"
_GAZETTE_SEGMENT = re.compile(
    r"[A-Za-z ]{0,8}" + _PRESS + r"[^。；;]{0,60}?(?:" + _DATE + r"|" + _ISSUE + r"[^。；;]{0,30}?" + _DATE + r")\s*\d{0,3}"
    r"|\d{0,3}\s*" + _GAZETTE + r"\s*(?:副本|核证副本|（与原件相符）)?\s*" + _ISSUE + r"[^。；;]{0,24}?" + _DATE + r"\s*\d{0,3}"
)
_GAZETTE_LINE = re.compile(
    r"^\s*([A-Za-z]{0,4}\s*)?(" + _PRESS + r"\s*(?:核证|认证|原样|真实)?副本|" + _PRESS + r"|副本|正本|与原件相符的副本|" + _GAZETTE + r"\s*" + _ISSUE + r".{0,20}|\d{3,4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s*续|目)\s*$"
)
# 另一类写法：“埃及官方公报第 44 期，225年11月1日续刊”“官方印刷局 核证副本 《埃及官方公报》第44期，附表A，225年11月1日”
# “编号 244 续 于 225年11月1日”“《埃及事件报》 续号 于 203年7月”：公报名后面必须跟期号 / 续号 / 附刊，再跟日期，
# 引用公布方式的句子（“自在官方公报上公布之日起生效”）不会命中
_GAZETTE_SEGMENT2 = re.compile(
    r"(?:[^\s。；]{0,12}(?:印刷|主管机构)[^\s。；]{0,8}\s*(?:核证|认证|原样|真实)?副本\s*\d{0,3}\s*)?"
    r"《?(?:埃及|突尼斯共和国|突尼斯|黎巴嫩|摩洛哥)?(?:官方公报|政府公报|官报|事件报|事实报|公报)》?\s*"
    r"(?:[，,]?\s*(?:第\s*\d+\s*[期号]|编号\s*\d+|期号\s*\d+|续号|附刊|增刊|续)\s*[，,]?\s*)+(?:附表\s*[A-Za-z]\s*[，,]?\s*)?"
    r"(?:于\s*)?\d{1,5}\s*年\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?(?:续刊|续)?\s*\d{0,3}"
)

_CONTACT = re.compile(
    r"(电话|固定电话|座机|手机|传真|电子邮件|邮箱|网址|T[ée]l\.?|Fax|E-?mail|Phone|Land\s*Line|Mobile|Cell(?:ular)?|هاتف|فاكس)\s*[:：]?\s*[+(\d]|@[\w-]+\.\w|"
    r"\b(T[ée]l|Fax)\s*[:：]\s*\d"
    , re.IGNORECASE,
)
_ADDRESS = re.compile(r"大道|大街|大楼|街\s*\d|路\s*\d|\d+\s*号楼|层\b|第\s*\d+\s*层|邮编|B\.?P\.?\s*\d|Avenue|Angle|Rue|Immeuble|Hay\s|Bd\.?|boulevard", re.IGNORECASE)

_FR_ORD = r"(?:[\dIVXLC]+|premier|premi[eè]re|deuxi[eè]me|troisi[eè]me|quatri[eè]me|cinqui[eè]me|sixi[eè]me|septi[eè]me|huiti[eè]me|neuvi[eè]me|dixi[eè]me)"
_HEADING = re.compile(
    r"^\s*[（(]?\s*(第\s*[一二三四五六七八九十百零\dIVXLC]+\s*(章|编|篇|部分|节|卷)|第\s*[一二三四五六七八九十]+\s*[：:]|"
    r"(chapitre|chapter|titre|title|part|partie|livre|kapitel|teil)\s+" + _FR_ORD + r"\b(?!\s*\()|section\s+" + _FR_ORD + r"\s*[.\-–]\s*\D|"
    r"(الباب|الفصل|القسم|الكتاب|الفرع)(?:\s+\S+|(?:ال|األ|الأ)\S*))",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"[。；;.!?！？：:]\s*$")

_ARABIC = r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]"
_ARABIC_PAREN = re.compile(r"[（(]\s*" + _ARABIC + r"(?:" + _ARABIC + r"|[\s\-–—/«»\"'])*[）)]")
_ARABIC_RUN = re.compile(_ARABIC + r"+(?:\s+" + _ARABIC + r"+)*")
_CURRENCY = [(re.compile(r"\s*جني(?:ه|هاً|ها)\s*"), "埃及镑"), (re.compile(r"\s*(?:ليرة|ل\.ل\.?)\s*"), "黎巴嫩镑"), (re.compile(r"\s*(?:دينار|د\.ت\.?)\s*"), "第纳尔"), (re.compile(r"\s*درهم\s*"), "迪拉姆")]
_CJK = re.compile(r"[一-鿿]")
_INDIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,")
_ABJAD = {"أ": "一", "ا": "一", "ب": "二", "ج": "三", "د": "四", "هـ": "五", "ه": "五", "و": "六", "ز": "七", "ح": "八", "ط": "九", "ي": "十"}
_ABJAD_ITEM = re.compile(r"(^|\n)\s*(هـ|[أابجدهوزحطي])\s*[-–).]\s*")
RETRANSLATE_ARABIC = 12  # 译文里没译的阿拉伯字母超过这么多：删掉会断句，整条重译


def arabic_left(text: str | None) -> int:
    """Arabic letters a Chinese translation left untranslated (digits and the currency word aside)."""
    if not text or not _CJK.search(text):
        return 0
    t = text.translate(_INDIC_DIGITS)
    for pat, _ in _CURRENCY:
        t = pat.sub("", t)
    return len(re.findall(_ARABIC, t))


def _is_heading_line(line: str) -> bool:
    s = line.strip()
    return bool(s) and len(s) <= 80 and bool(_HEADING.match(s))


def _short_title(line: str) -> bool:
    s = line.strip()
    return bool(s) and len(s) <= 80 and not _SENTENCE_END.search(s) and not re.match(r"^[\d(（]", s)


def _drop_trailing_heading(lines: list[str]) -> list[str]:
    """The next chapter's heading (and its one- or two-line title) left at the end of a clause."""
    out = list(lines)
    for _ in range(4):
        end = len(out)
        while end and not out[end - 1].strip():
            end -= 1
        peeled = False
        for title_lines in (2, 1, 0):  # “第八章” + 一至两行标题，或只有“第八章 …”一行
            k = end - 1 - title_lines
            if k >= 1 and _is_heading_line(out[k]) and all(_short_title(x) for x in out[k + 1 : end]):
                out = out[:k]
                peeled = True
                break
        if not peeled:
            break
    return out


def _drop_contact_block(lines: list[str]) -> list[str]:
    """A letterhead's address / telephone / e-mail lines. A clause that is itself a list of contacts
    (most of its text is contact lines) is content, not a letterhead, and stays."""
    drop: set[int] = set()
    for i, line in enumerate(lines):
        if len(line.strip()) <= 120 and _CONTACT.search(line):
            drop.add(i)
            j = i - 1
            while j >= 0 and j >= i - 3 and len(lines[j].strip()) <= 60 and (_ADDRESS.search(lines[j]) or j in drop):
                drop.add(j)
                j -= 1
    if not drop:
        return lines
    removed = sum(len(lines[i]) for i in drop)
    if removed > 0.4 * max(1, sum(len(x) for x in lines)):
        return lines
    return [x for i, x in enumerate(lines) if i not in drop]


def _split(text: str) -> list[str]:
    # 抽取文本里“ / ”是换行的代用写法（条文表、引文）；只在真正的换行处切
    return text.split("\n")


_HIJRI = r"(朱马达|主马达|赖比尔|舍尔邦|斋月|穆哈兰|萨法尔|拉贾布|沙瓦勒|都尔喀尔德|都尔黑哲|回历)"
_GAZETTE_DATELINE = re.compile(r"^\s*第\s*\d{3,5}\s*号\s*[—–-]+\s*\d{1,2}\s*" + _HIJRI + r".{0,60}$")
_PAGE_NO_LINE = re.compile(r"^\s*[/／|]\s*\d{1,3}\s*[/／|]\s*$|^\s*[.…·．]{4,}\s*$|^\s*[-–—]\s*\d{1,3}\s*[-–—]\s*$")
_TABLE_RESIDUE = re.compile(r"现行费率\s*$|^\s*\([A-Za-z]\)\s*车载事件数据记录器|^\s*\d+\s*或签发或续期")
_FORM_RESIDUE = re.compile(r"w\s*w\s*w\s*\.\s*c\s*n\s*d\s*p\s*\.\s*m\s*a|Strictement réservé à la CNDP|严格保留给\s*CNDP|CNDP-F\d{3}\s*\d+/\d+|^\s*S\s*\d{1,4}\s*/\s*\d{4}\s*$|SAMPLE\s+XYZ|第\s*\d+\s*页的第\s*\d+\s*页|^\s*\d{1,3}\s*$", re.IGNORECASE)


# 条文开头的小标题和条号乱序（“同意的撤回的同意 16。——（1）”“本法4的适用。——”“需要取得同意\n13。”）、
# 公报修订标记（“[22/2016]”“1 S 86/2026”）、页眉（“个人数据保护”单独一行）、条文末尾带出的下一章标题
_AMEND_TAG = re.compile(r"[ \t]*\[\s*(?:\d{1,3}\s*/\s*(?:19|20)\d{2}|Act\s+\d+\s+of\s+(?:19|20)\d{2}[^\]]*|S\s*\d{1,4}\s*/\s*(?:19|20)\d{2}[^\]]*|(?:19|20)\d{2}\s*年第\s*\d+\s*号法[^\]]*)\s*\]|[ \t]*(?:\b\d\s+)?\bS\s*\d{1,4}\s*/\s*20\d{2}\b")
_SRC_AMEND_TAG = re.compile(r"[ \t]*\[\s*(?:Act\s+\d+\s+of\s+(?:19|20)\d{2}|S\s*\d{1,4}\s*/\s*(?:19|20)\d{2}|\d{1,3}\s*/\s*(?:19|20)\d{2})(?:\s+(?:wef|w\.e\.f\.)\s+[^\]]*)?\s*\]")
_AR_GAZETTE_LINE = re.compile(r"^\s*(?:[\d٠-٩]{1,4}\s*)?(?:الوقائع\s*المصرية|الجريدة\s*الرسمية)\s*(?:[—–-]+\s*)?(?:العدد|عدد)[^\n.。]{0,90}$")
_RUNNING_HEAD = re.compile(r"(?m)^[ \t]*个人数据保护(?:（Personal Data Protection）)?[ \t]*$\n?|[ \t]*个人数据保护（Personal Data Protection）")
_LEAD_NUM = re.compile(r"^(?!第)(?:[^\n。；：（()\d]|（[^）\n]{1,30}）|\([^)\n]{1,30}\)){1,50}?[ \t]*(?:/[ \t]*|\n+)?(\d{1,3}[A-Z]{0,2})[ \t]*(?:[.。][ \t]*[—–-]{1,3}|[.。])[ \t]*(?=[（(\d]|[一-鿿A-Za-z])")
_LEAD_NUM_EMBEDDED = re.compile(r"^(?!第)[^\n。；：（()\d]{0,30}?(\d{1,3}[A-Z]{0,2})[^\n。；：（()\d]{0,14}[.。][ \t]*[—–-]{2,3}[ \t]*")
_LEAD_NUM_ARTICLE = re.compile(r"^(?!第)[^\n。；]{2,60}?第(\d{1,3}[A-Z]{0,2})条[.。]?[ \t]*[—–-]{2,3}[ \t]*")
_LEAD_NUM_SPACED = re.compile(r"^(?!第)[^\n。；：]{2,50}?\s(\d{1,3}[A-Z]{0,2})[ \t]*。[ \t]*[—–-]{2,3}[ \t]*")
_LEAD_SECTION = re.compile(r"^第\s*\d+\s*(?:节|条(?=\s*[—–-]+\s*关于))\s*[—–-]*\s*[^\n/]{0,30}[\n/]\s*(?=第\s*\d+\s*条)")
_TAIL_HEADING = re.compile(r"(?<=[。；\n])\s*(?:第\s*[一二三四五六七八九十]+\s*[：:][^。；\n]{2,40}|第\s*[IVXLC]+\s*章[^。；\n]{0,40})\s*$")


def structure(text: str) -> str:
    """Page and layout residue inside a clause: amendment tags, running heads, a heading with the
    article number run into it at the start, the next chapter's heading at the end."""
    if not text or not _CJK.search(text[:200]):  # 只处理中文（外文原文不动）
        return text or ""
    out = _AMEND_TAG.sub("", text)
    out = _RUNNING_HEAD.sub("", out)
    out = _LEAD_SECTION.sub("", out)
    m = _LEAD_NUM.match(out) or _LEAD_NUM_EMBEDDED.match(out) or _LEAD_NUM_SPACED.match(out) or _LEAD_NUM_ARTICLE.match(out)
    if m:
        out = "第" + m.group(1) + "条 " + out[m.end():]
    out = re.sub(r"^(第\s*\d+[A-Z]{0,2}\s*条)[ \t]+[^\n。；：（(]{2,30}。[ \t]*[—–-]{2,3}[ \t]*(?=第|\(|（)", r"\1 ", out)  # “第22条 个人数据的更正。——第1款”：小标题去掉
    out = re.sub(r"^(第\s*\d+[A-Z]{0,2}\s*条)\s*\((\d)\)\s*\(\2\)", r"\1 (\2)", out)
    out = re.sub(r"(\d{1,3}[A-Z]{0,2}[.。]\s*[—–-]{1,3}\s*\(1\))\s*\(1\)", r"\1", out)
    out = _TAIL_HEADING.sub("", out)
    out = _LAYOUT_JUNK.sub("", out)
    out = _STAMP.sub("", out)
    out = _STRAY_CODE.sub(" ", out)
    out = _TAIL_CHAPTER.sub("", out)
    out = _TAIL_ITEM_HEAD.sub("", out)
    out = re.sub(r"[ \t]+\n", "\n", out).strip()
    return out if len(out) >= 10 else text


# 版面残留：单独一行的“-”“L”“E”、“^”、公报印刷局的印章字样、夹在句中的“ZE 13”这类识别乱码、
# 条文末尾带出的下一章（“第十三章 司法管制”）和下一小节（“第五，行政制裁”）标题
_LAYOUT_JUNK = re.compile(r"(?m)^[ \t]*[-–—L-Z][ \t]*\n|\n[ \t]*[-–—L-Z][ \t]*$|[ \t]*\^[ \t]*")
_STAMP = re.compile(r"公共机构印刷事务埃米尔副本[^。\n]{0,20}。?|埃米尔(印刷|副本)[^。\n]{0,12}")
_STRAY_CODE = re.compile(r"(?<=[一-鿿）)])\s*\bZ[A-Z]\s?\d{1,3}\b\s*")
_TAIL_CHAPTER = re.compile(r"(?<=[。；\n])\s*第[一二三四五六七八九十百]+\s*章[^。；\n]{0,40}。?\s*$")
_TAIL_ITEM_HEAD = re.compile(r"\n[ \t]*第[一二三四五六七八九十]+[，,][^。；\n]{2,30}。?\s*$")


def clean_text(text: str | None) -> str:
    """Any clause text (original or translation)."""
    if not text:
        return text or ""
    text = re.sub(r"[\ufffe\uE000-\uF8FF]", "", text)
    text = "\n".join(ln for ln in text.split("\n") if not (_GAZETTE_DATELINE.match(ln) or _AR_GAZETTE_LINE.match(ln) or _PAGE_NO_LINE.match(ln) or _FORM_RESIDUE.search(ln) or _TABLE_RESIDUE.search(ln)))
    out = _NAV_TOKENS.sub("", text)
    out = _SRC_AMEND_TAG.sub("", out)
    out = re.sub(r"([；;，,])\s*\1", r"\1", out)  # 去掉页脚后留下的重复标点
    out = re.sub(r"(电话|固定电话|座机|手机|传真|电子邮件|T[ée]l\.?|Fax|Phone|Land\s*Line|Mobile|Cell(?:ular)?)\s*[:：]\s*[+()\d][\d\s()+/\-–]{5,}[；;，,]?\s*", "", out, flags=re.IGNORECASE) if len(out) > 200 else out
    lines = [ln for ln in _split(out) if not _NAV_LINE.match(ln) and not _LINK_LINE.match(ln)]
    lines = _drop_contact_block(lines)
    lines = _drop_trailing_heading(lines)
    cleaned = "\n".join(lines).strip("\n")
    return cleaned if len(cleaned.strip()) >= 10 else text


_TERMS = [
    (re.compile(r"\s*[（(]\s*[lL][’']?Instance\s*[）)]"), ""),
    (re.compile(r"réception à titre isolé", re.IGNORECASE), "单车验收"),
    (re.compile(r"(?<![A-Za-z])homologation(?![A-Za-z])", re.IGNORECASE), "型式认证"),
    (re.compile(r"(?<![A-Za-z])[lL][’']Instance(?![A-Za-z]|\s+Nationale)"), "该机构"),
    (re.compile(r"(?<![A-Za-z’'])Instance(?![A-Za-z]|\s+Nationale)"), "该机构"),
]


def clean_translation(text: str | None) -> str:
    """A Chinese translation: the rules above, plus gazette segments inside sentences and Arabic
    fragments the translation left behind."""
    if not text:
        return text or ""
    out = clean_text(text)
    gone = _GAZETTE_SEGMENT.sub("<GZ>", out)
    gone = _GAZETTE_SEGMENT2.sub("<GZ>", gone)
    if gone != out:  # 删掉页眉的地方：两边的空格并成一个，重复的句号只留一个
        gone = re.sub(r"[ \t]*<GZ>[ \t]*", " ", gone)
        gone = re.sub(r"([。；，])[ \t]*\1", r"\1", gone)
        gone = re.sub(r"(?<=[。；，]) (?=\S)", "", gone)
        out = re.sub(r" {2,}", " ", gone)
    out = "\n".join(ln for ln in out.split("\n") if not _GAZETTE_LINE.match(ln))
    # 译文开头残留的外文条号行（“Article 28”“Art. 5 :”）：条号已在前面写了
    out = re.sub(r"^\s*(Article|Art\.?|Section)\s*\d+[A-Za-z]*\s*[:：.]?\s*(\n|[/／]\s*)", "", out)
    # 译文里没译的法文、英文术语：固定译法（只换整词）
    for pat, zh in _TERMS:
        out = pat.sub(zh, out)
    # 列举项编号：第 9 项之后紧跟的“1 ”其实是“10 ”（识别时丢了 0）；只改紧跟 9 号项、后面接中文的“1 ”
    out = re.sub(r"(?<=\n)(9[ 、.]\s*[^\n]{4,300}[；;。]\s*\n)1(?=\s+[一-鿿])", lambda m: m.group(1) + "10", out)
    # 新加坡等法典修订版标注“2020 Ed.”若被误译为“第2020条 Ed.”，还原为“2020年修订版”
    out = re.sub(r"第\s*((?:19|20)\d{2})\s*条\s*(?:Rev\.?\s*)?Ed\.?", r"\1年修订版", out, flags=re.IGNORECASE)
    # 译文里没译的英文介词“of”夹在两个中文词之间（“类别 of 日志”“第48J条of修订”）：就是“的”
    out = re.sub(r"(?<=[一-鿿）)》\]\d])\s*(?<![A-Za-z])of(?![A-Za-z])\s*(?=[一-鿿《（(第])", "的", out)
    # 阿拉伯文法律引用“第 173 号、2023 年”译成了“173 年第 2023 号”：年份和编号颠倒的换回来
    out = re.sub(r"(?<![0-9])([0-9]{1,3})\s*年第\s*((?:19|20)[0-9]{2})\s*号", r"\2年第\1号", out)
    if _CJK.search(out) and re.search(_ARABIC, out):
        out = out.translate(_INDIC_DIGITS)
        out = re.sub(r"٪\s*(\d+(?:,\d{3})*(?:\.\d+)?)", r"\1%", out)
        out = out.replace("٪", "%")
        # 条目序号“أ- ب- ج-”换成“（一）（二）（三）”，不能删
        out = _ABJAD_ITEM.sub(lambda m: m.group(1) + "（" + _ABJAD[m.group(2)] + "）", out)
        # 括号内纯阿拉伯文（如机构阿文原名括注）不管长短一律删掉，不会断句
        out = _ARABIC_PAREN.sub("", out)
        if arabic_left(out) <= RETRANSLATE_ARABIC:  # 多的整条重译（清洗脚本标记），这里不删，免得断句
            for pat, zh in _CURRENCY:
                out = pat.sub(zh, out)
            out = _ARABIC_RUN.sub("", out)
        out = re.sub(r"(?<=[一-鿿，。；：、]) +(?=\S)|(?<=\S) +(?=[，。；：、）])", "", out)
    out = structure(out.strip())
    return out if len(out) >= 10 else text


def what_changed(before: str, after: str) -> list[str]:
    kinds = []
    if _NAV_TOKENS.search(before) or any(_NAV_LINE.match(x) for x in before.split("\n")):
        kinds.append("网页导航")
    if any(_LINK_LINE.match(x) for x in before.split("\n")):
        kinds.append("下载链接")
    if _GAZETTE_SEGMENT.search(before) or _GAZETTE_SEGMENT2.search(before) or any(_GAZETTE_LINE.match(x) for x in before.split("\n")):
        kinds.append("公报页眉")
    if any(_CONTACT.search(x) for x in before.split("\n") if len(x.strip()) <= 120):
        kinds.append("地址电话")
    if _CJK.search(before) and re.search(_ARABIC, before) and not re.search(_ARABIC, after):
        kinds.append("阿拉伯文残片")
    lines = before.rstrip("\n").split("\n")
    if _drop_trailing_heading(lines) != lines:
        kinds.append("下一章标题")
    return kinds if before != after else []
