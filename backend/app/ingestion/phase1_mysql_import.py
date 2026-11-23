# ruff: noqa: E501
"""从一期服务器备份（`mysql_backup.sql` + `all_documents_backup.tar.gz` + 官方补充文本）幂等吸纳多国法规。

设计原则（严格对齐十大红线）：
1. 保护既有核心国别（`CN, SG, EG, TN, MA, LB`）零退化：已精译、已建图谱及简报的国家原样保留，不混入重复未译底本；
2. 原件字节级归档与 SHA-256 去重：支持 PDF / DOCX / DOC(HTML) / RTF / HTML 多格式原文归档与 Phase-2 条款切分；
3. 归档站点隔离：新增国家的静态归档站点默认 `enabled=False`，不干扰在用定时监测站点的 100% 健康率与后台内存预算；
4. 真实国别官方立法门户锚定：无单篇直链时绑定该国法定官方立法数据库门户加文档锚点，不虚构死链域名。
"""

from __future__ import annotations

import hashlib
import json
import logging
import pathlib
import re
import tarfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analysis.workflow_engine import country_name_zh
from app.ingestion.archive import ArchiveStore
from app.ingestion.models import (
    Document,
    DocumentVersion,
    LegalStatus,
    PipelineEvent,
)
from app.ingestion.ocr import OcrEngine, OcrResult
from app.ingestion.parsing import parse_document
from app.ingestion.pipeline import parse_version
from app.knowledge.countries import DEFAULT_ORIG_NAMES
from app.sources.models import SourceSite, Strategy

log = logging.getLogger(__name__)

# 既有已完成全量精译、图谱抽取与合规简报的核心法域：原样保护，避免混入未译重复文本扰动简报指纹
PROTECTED_COUNTRIES = frozenset({"CN", "SG", "EG", "TN", "MA", "LB"})

# 各国权威官方立法公报/法规库门户（用于构建归档 SourceSite 与保底跳转入口）
OFFICIAL_PORTALS: dict[str, tuple[str, str]] = {
    "EU": ("欧盟官方公报 EUR-Lex", "https://eur-lex.europa.eu"),
    "GB": ("英国国家立法数据库 Legislation.gov.uk", "https://www.legislation.gov.uk"),
    "DE": ("德国联邦司法部法律门户 Gesetze im Internet", "https://www.gesetze-im-internet.de"),
    "FR": ("法国政府公共法律门户 Légifrance", "https://www.legifrance.gouv.fr"),
    "IT": ("意大利国家官方法规库 Normattiva", "https://www.normattiva.it"),
    "ES": ("西班牙国家官方公报 BOE", "https://www.boe.es"),
    "NL": ("荷兰中央政府法律法规库 Wetten.overheid.nl", "https://wetten.overheid.nl"),
    "BE": ("比利时联邦司法公共服务部公报 Justel", "https://www.ejustice.just.fgov.be"),
    "PL": ("波兰众议院在线法律公报系统 ISAP", "https://isap.sejm.gov.pl"),
    "SE": ("瑞典议会法规汇编 Riksdagen", "https://www.riksdagen.se/sv/dokument-och-lagar/"),
    "RU": ("俄罗斯联邦官方门户法律信息系统 Pravo.gov.ru", "http://pravo.gov.ru"),
    "KZ": ("哈萨克斯坦共和国规范性法律文件信息系统 Adilet", "https://adilet.zan.kz"),
    "KG": ("吉尔吉斯共和国司法部中央法律数据库", "http://cbd.minjust.gov.kg"),
    "TR": ("土耳其总统府立法信息系统 Mevzuat", "https://www.mevzuat.gov.tr"),
    "IL": ("以色列司法部隐私保护局官方法规门户", "https://www.gov.il/en/departments/the_privacy_protection_authority"),
    "AE": ("阿联酋联邦政府官方立法门户 UAE Legislation", "https://uaelegislation.gov.ae"),
    "SA": ("沙特阿拉伯内阁专家局官方法律门户", "https://laws.boe.gov.sa"),
    "QA": ("卡塔尔司法部官方法律门户 Al-Meezan", "https://www.almeezan.qa"),
    "KW": ("科威特政府官方电子政务法律门户", "https://www.e.gov.kw"),
    "OM": ("阿曼司法与法律事务部官方门户 Qanoon", "https://qanoon.om"),
    "DZ": ("阿尔及利亚民主人民共和国官方公报 JORADP", "https://www.joradp.dz"),
    "JP": ("日本数字厅电子政府法令检索 e-Gov", "https://laws.e-gov.go.jp"),
    "KR": ("韩国法制处国家法令情报中心", "https://www.law.go.kr"),
    "IN": ("印度法制部国家法典数据库 India Code", "https://www.indiacode.nic.in"),
    "PK": ("巴基斯坦司法部联邦法典门户 Pakistan Code", "https://pakistancode.gov.pk"),
    "BD": ("孟加拉国司法与议会事务部法律库 Laws of Bangladesh", "http://bdlaws.minlaw.gov.bd"),
    "VN": ("越南国家法律法规数据库 VBPL", "https://vbpl.vn"),
    "MY": ("马来西亚总检察署联邦立法门户 LOM", "https://lom.agc.gov.my"),
    "ID": ("印度尼西亚国家审计署法律法规库 JDIH BPK", "https://peraturan.bpk.go.id"),
    "TH": ("泰国皇家政府公报门户 Ratchakitcha", "https://ratchakitcha.soc.go.th"),
    "PH": ("菲律宾国家隐私委员会官方法规库 NPC", "https://privacy.gov.ph"),
    "AU": ("澳大利亚联邦立法登记处 Federal Register of Legislation", "https://www.legislation.gov.au"),
    "CA": ("加拿大司法部联邦法规网站 Justice Laws", "https://laws-lois.justice.gc.ca"),
    "MX": ("墨西哥联邦众议院法律图书馆与官方公报 DOF", "https://www.diputados.gob.mx/LeyesBiblio/"),
    "BR": ("巴西总统府民事办公室立法门户 Planalto", "https://www.planalto.gov.br"),
    "AR": ("阿根廷司法部国家立法信息系统 InfoLEG", "https://www.argentina.gob.ar/normativa"),
    "CO": ("哥伦比亚国家统一法律信息系统 SUIN-Juriscol", "https://www.suin-juriscol.gov.co"),
    "PE": ("秘鲁司法与人权部国家法律信息系统 SPIJ", "https://spijweb.minjus.gob.pe"),
    "ZA": ("南非共和国政府官方公报门户", "https://www.gov.za/documents/acts"),
    "NG": ("尼日利亚数据保护委员会官方法规门户 NDPC", "https://ndpc.gov.ng"),
}

COUNTRY_DEFAULT_LANG: dict[str, str] = {
    "AE": "en", "AR": "es", "AU": "en", "BD": "bn", "BE": "fr", "BR": "pt",
    "CA": "en", "CL": "es", "CO": "es", "DE": "de", "DZ": "fr", "ES": "es",
    "EU": "en", "FR": "fr", "GB": "en", "ID": "id", "IL": "he", "IN": "en",
    "IT": "it", "JP": "ja", "KG": "ru", "KR": "ko", "KW": "ar", "KZ": "ru",
    "MX": "es", "MY": "en", "NG": "en", "NL": "nl", "OM": "ar", "PE": "es",
    "PH": "en", "PK": "en", "PL": "pl", "QA": "ar", "RU": "ru", "SA": "ar",
    "SE": "sv", "TH": "th", "TR": "tr", "VN": "vi", "ZA": "en",
}


class CachedOcrEngine(OcrEngine):
    """基于预核验 Tesseract 5 逐页缓存（按 SHA-256 精确命中）并支持回退本机引擎的识别适配器。"""

    def __init__(self, cache_dir: Path | None, fallback: OcrEngine | None = None) -> None:
        super().__init__(None, None)
        self._cache_dir = cache_dir
        self._fallback = fallback

    @property
    def available(self) -> bool:
        return bool(
            (self._cache_dir and self._cache_dir.exists())
            or (self._fallback and self._fallback.available)
        )

    def _lookup(self, content: bytes) -> dict | None:
        if not self._cache_dir:
            return None
        sha = hashlib.sha256(content).hexdigest()
        p = self._cache_dir / f"{sha}.json"
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def run(self, content: bytes, language: str, max_pages: int | None = None) -> OcrResult:
        hit = self._lookup(content)
        if hit is not None:
            pages = list(hit["pages"][:max_pages] if max_pages else hit["pages"])
            return self._with_headings(
                OcrResult(
                    pages=pages,
                    mean_confidence=float(hit.get("mean_confidence", 84.0)),
                    languages=str(hit.get("tesseract_langs") or language),
                ),
                language,
            )
        if self._fallback and self._fallback.available:
            return self._fallback.run(content, language, max_pages)
        return super().run(content, language, max_pages)

    def read_page(self, content: bytes, page_no: int, language: str) -> str:
        hit = self._lookup(content)
        if hit is not None:
            pages = hit.get("pages") or []
            if 1 <= page_no <= len(pages):
                return str(pages[page_no - 1])
        if self._fallback and hasattr(self._fallback, "read_page"):
            return self._fallback.read_page(content, page_no, language)
        return ""


@dataclass
class Phase1MysqlImportStats:
    countries_covered: set[str] = field(default_factory=set)
    sites_created: int = 0
    documents_created: int = 0
    versions_created: int = 0
    skipped_protected: int = 0
    skipped_existing_hash: int = 0
    states: Counter[str] = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)


def parse_mysql_table(sql_text: str, table: str) -> list[dict[str, object]]:
    cm = re.search(rf"CREATE TABLE `{re.escape(table)}` \((.*?)\) ENGINE=", sql_text, re.S)
    if not cm:
        return []
    cols = re.findall(r"^\s*`([^`]+)`\s+", cm.group(1), re.M)
    rows: list[dict[str, object]] = []
    pat = re.compile(rf"INSERT INTO `{re.escape(table)}` VALUES (.*?);\n", re.S)
    for im in pat.finditer(sql_text):
        blob = im.group(1)
        i, n = 0, len(blob)
        while i < n:
            while i < n and blob[i] != "(":
                i += 1
            if i >= n:
                break
            i += 1
            vals: list[object] = []
            while i < n:
                while i < n and blob[i] in " \t\r\n":
                    i += 1
                if i < n and blob[i] == ")":
                    i += 1
                    break
                if blob[i : i + 4] == "NULL":
                    vals.append(None)
                    i += 4
                elif blob[i] == "'":
                    i += 1
                    chars: list[str] = []
                    while i < n:
                        ch = blob[i]
                        if ch == "\\" and i + 1 < n:
                            nxt = blob[i + 1]
                            esc = {"n": "\n", "r": "\r", "t": "\t", "0": "\x00", "'": "'", '"': '"', "\\": "\\"}
                            chars.append(esc.get(nxt, nxt))
                            i += 2
                            continue
                        if ch == "'":
                            i += 1
                            break
                        chars.append(ch)
                        i += 1
                    vals.append("".join(chars))
                else:
                    j = i
                    while j < n and blob[j] not in ",)":
                        j += 1
                    tok = blob[i:j].strip()
                    try:
                        vals.append(int(tok))
                    except ValueError:
                        vals.append(tok)
                    i = j
                while i < n and blob[i] in " \t\r\n":
                    i += 1
                if i < n and blob[i] == ",":
                    i += 1
            rows.append(dict(zip(cols, vals, strict=False)))
            while i < n and blob[i] in ", \t\r\n":
                if blob[i] == "(":
                    break
                i += 1
    return rows


def detect_document_language(text: str, cc: str) -> str:
    ar = len(re.findall(r"[\u0600-\u06ff]", text))
    cyr = len(re.findall(r"[\u0400-\u04ff]", text))
    th = len(re.findall(r"[\u0e00-\u0e7f]", text))
    ja = len(re.findall(r"[\u3040-\u30ff]", text))
    ko = len(re.findall(r"[\uac00-\ud7af]", text))
    he = len(re.findall(r"[\u0590-\u05ff]", text))
    bn = len(re.findall(r"[\u0980-\u09ff]", text))
    lat = len(re.findall(r"[A-Za-z\u00C0-\u024F\u1E00-\u1EFF]", text))
    tot = ar + cyr + th + ja + ko + he + bn + lat
    default = COUNTRY_DEFAULT_LANG.get(cc, "en")
    if tot < 20:
        return default
    if ar / tot > 0.25:
        return "ar"
    if cyr / tot > 0.25:
        return "ru"
    if th / tot > 0.20:
        return "th"
    if ja > 20:
        return "ja"
    if ko > 20:
        return "ko"
    if he / tot > 0.20:
        return "he"
    if bn / tot > 0.20 or (cc == "BD" and "evsjv" in text):
        return "bn"
    if cc in ("OM", "KW") and lat > 0:
        en_words = len(re.findall(r"\b(the|of|and|to|in|shall|data|law)\b", text.lower()))
        if en_words < 8:
            return "ar"
    fr_hits = len(re.findall(r"\b(les|des|dans|pour|par|sur|est|aux|loi|chapitre)\b", text.lower()))
    if fr_hits > 15 and default in ("en", "ar", "fr"):
        return "fr"
    if default in ("ar", "he", "th", "ru", "ja", "ko", "bn"):
        return "en"
    return default


def infer_doc_type_and_kind(title_zh: str, title_orig: str, category: str) -> tuple[str, str]:
    both = f"{title_zh} {title_orig} {category}"
    if re.search(r"修正案|修订|修改|amendment|amending", both, re.I):
        return "LAW", "AMENDMENT"
    if re.search(r"指南|指引|准则|框架|建议|标准|guide|guidance|guideline|framework|code of practice|lineamientos", both, re.I):
        return "GUIDANCE", "GUIDANCE"
    if re.search(r"条例|决定|决议|命令|法令|规则|细则|regulation|decree|order|rule|decision|resolution|yönetmelik|besluit|verordnung", both, re.I) and not re.search(r"通用数据保护条例|General Data Protection Regulation", both, re.I):
        return "REGULATION", "REGULATION"
    return "LAW", "LAW"


def classify_topic_and_weight(
    title_zh: str, title_orig: str, category: str, doc_type: str, instrument_kind: str
) -> tuple[str, str]:
    both = f"{title_zh} {title_orig} {category}".lower()
    if re.search(r"车辆|道路交通|自动驾驶|汽车|vehicle|road traffic|autonomous", both):
        topic = "vehicle"
    elif re.search(
        r"个人数据|个人信息|数据保护|数据隐私|隐私|信用信息|健康数据|消费者数据|personal data|data protection|privacy|gegevensbescherming|données|datos personales|proteção de dados|персональных данных|개인정보",
        both,
    ):
        topic = "personal_data"
    elif re.search(
        r"网络安全|网络信息安全|网络犯罪|电子犯罪|计算机犯罪|滥用计算机|信息犯罪|关键信息|关键基础设施|信息安全|cyber|computer misuse|electronic crimes|network and information|кибербезопас|정보통신망",
        both,
    ):
        topic = "cybersecurity"
    elif re.search(
        r"电信|无线电|通信|电子交易|电子签名|电子商务|数字法典|数字服务|支付系统|telecom|electronic transactions|electronic communications|digital|telecommunicatie|электронн",
        both,
    ):
        topic = "telecom"
    else:
        topic = "other"

    if (
        doc_type in ("LAW", "REGULATION")
        and instrument_kind != "AMENDMENT"
        and topic in ("personal_data", "cybersecurity", "vehicle", "telecom")
    ):
        weight = "core"
    else:
        weight = "supporting"
    return topic, weight


def extract_heading_title(sample_text: str, fallback_zh: str) -> str:
    skip_pat = re.compile(
        r"^(?:page\s+\d+|\d+|official gazette|journal officiel|bolet[ií]n oficial|gazzetta ufficiale|bundesgesetzblatt|staatsblad|authorised version|current to|republication|table of contents|arrangement of sections)$",
        re.I,
    )
    candidates: list[str] = []
    for raw_ln in sample_text.splitlines()[:18]:
        ln = " ".join(raw_ln.split()).strip(" -–—_•*.")
        if len(ln) < 5 or len(ln) > 180 or skip_pat.match(ln):
            continue
        if sum(1 for c in ln if c.isalpha()) < 4:
            continue
        candidates.append(ln)
        if len(candidates) >= 2:
            break
    if candidates:
        joined = " — ".join(candidates[:2]) if len(candidates[0]) < 36 and len(candidates) > 1 else candidates[0]
        return joined[:480]
    return fallback_zh[:480]


def _parse_date(val: object) -> date | None:
    s = str(val or "").strip()[:10]
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        try:
            return date.fromisoformat(s)
        except ValueError:
            return None
    return None


def _mime_for_ext(ext: str, blob: bytes) -> str:
    head = blob[:256].lstrip().lower()
    if head.startswith(b"%pdf"):
        return "application/pdf"
    if head.startswith(b"{\\rtf"):
        return "application/rtf"
    if head.startswith((b"<!doctype html", b"<html")):
        return "text/html"
    if ext == ".docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if ext == ".doc":
        return "application/msword"
    if ext == ".html" or ext == ".htm":
        return "text/html"
    return "application/pdf"


async def _get_or_create_archive_site(session: AsyncSession, cc: str, stats: Phase1MysqlImportStats) -> SourceSite:
    pub_default, portal_url = OFFICIAL_PORTALS.get(
        cc, (f"{country_name_zh(cc)}官方立法机构", f"https://www.wipo.int/wipolex/en/members/profile/{cc.lower()}")
    )
    existing = (
        await session.execute(
            select(SourceSite).where(
                SourceSite.country_code == cc,
                SourceSite.entry_url == portal_url,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    site = SourceSite(
        country_code=cc,
        publisher=pub_default,
        language=COUNTRY_DEFAULT_LANG.get(cc, "en"),
        document_type="LAW",
        entry_url=portal_url,
        strategy=Strategy.DIRECT.value,
        config={
            "country_name_zh": country_name_zh(cc),
            "country_name_orig": DEFAULT_ORIG_NAMES.get(cc, cc),
            "phase1_static_archive": True,
        },
        enabled=False,
        terms_status="approved",
        terms_note="一期服务器官方法规原文静态归档导入（在线增量监测源待独立核准后开启）",
    )
    session.add(site)
    await session.flush()
    stats.sites_created += 1
    return site


async def _ingest_one_item(
    sf: async_sessionmaker[AsyncSession],
    archive: ArchiveStore,
    ocr: OcrEngine,
    existing_hashes: set[str],
    *,
    cc: str,
    title_zh: str,
    title_orig_hint: str | None,
    category: str,
    source_url: str | None,
    published_on: date | None,
    effective_on: date | None,
    legal_status_raw: str | None,
    blob: bytes,
    ext: str,
    forced_lang: str | None,
    stats: Phase1MysqlImportStats,
) -> None:
    sha = ArchiveStore.sha256(blob)
    if sha in existing_hashes:
        stats.skipped_existing_hash += 1
        return

    mime = _mime_for_ext(ext, blob)
    sample_txt = ""
    try:
        preview = parse_document(blob, mime)
        sample_txt = "\n".join(preview.pages[:4])
    except Exception:  # noqa: BLE001
        sample_txt = ""

    lang = forced_lang or detect_document_language(sample_txt, cc)
    clean_zh = " ".join((title_zh or "").split()).strip()
    clean_orig = " ".join((title_orig_hint or "").split()).strip()
    if not clean_orig:
        clean_orig = extract_heading_title(sample_txt, clean_zh)
    doc_type, inst_kind = infer_doc_type_and_kind(clean_zh, clean_orig, category)
    topic, weight = classify_topic_and_weight(clean_zh, clean_orig, category, doc_type, inst_kind)

    lraw = str(legal_status_raw or "").strip().lower()
    if lraw in ("repealed", "已废止", "废止"):
        lstatus = LegalStatus.REPEALED.value
    elif lraw in ("draft", "草案", "征求意见"):
        lstatus = LegalStatus.DRAFT.value
    elif lraw in ("amended", "已修订"):
        lstatus = LegalStatus.AMENDED.value
    else:
        lstatus = LegalStatus.IN_FORCE.value

    stored_digest, rel_path = archive.put(blob)
    async with sf() as session:
        site = await _get_or_create_archive_site(session, cc, stats)
        final_source_url = (
            source_url.strip()
            if source_url and source_url.strip().startswith("http")
            else f"{site.entry_url.rstrip('/')}/#doc-{stored_digest[:12]}"
        )
        dup_doc = (
            await session.execute(
                select(Document).where(
                    Document.site_id == site.id,
                    Document.source_url == final_source_url,
                )
            )
        ).scalar_one_or_none()
        if dup_doc is not None:
            stats.skipped_existing_hash += 1
            return

        doc = Document(
            site_id=site.id,
            country_code=cc,
            source_url=final_source_url[:2048],
            title=clean_orig[:500],
            title_zh=clean_zh[:500] if clean_zh else None,
            business_topic=topic,
            business_weight=weight,
            language=lang,
            document_type=doc_type,
            instrument_kind=inst_kind,
            relevance="relevant",
            relevance_note="一期服务器多国官方法规原文归档入库（汽车数据保护/网络安全/电信合规底座）",
            relevance_source="rule",
        )
        session.add(doc)
        await session.flush()

        version = DocumentVersion(
            document_id=doc.id,
            version_no=1,
            content_hash=stored_digest,
            archive_path=rel_path,
            size_bytes=len(blob),
            mime_type=mime,
            final_url=final_source_url[:2048],
            fetched_at=datetime.now(UTC),
            published_date=published_on,
            effective_date=effective_on,
            legal_status=lstatus,
            metadata_source="phase1_mysql_backup",
        )
        session.add(version)
        await session.flush()
        session.add(
            PipelineEvent(
                version_id=version.id,
                step="import",
                status="ok",
                message=f"从一期备份吸纳归档，文件指纹前12位 {stored_digest[:12]} 已核验",
            )
        )
        await parse_version(session, archive, version, lang, ocr)
        # 文本层清晰解析通过的官方归档版本标记为已核验
        if version.state == "parsed" and version.text_source == "text_layer":
            version.review_status = "approved"
        await session.commit()

        existing_hashes.add(stored_digest)
        stats.countries_covered.add(cc)
        stats.documents_created += 1
        stats.versions_created += 1
        stats.states[version.state] += 1
        if version.state != "parsed":
            stats.errors.append(f"[{cc}] {clean_zh}: state={version.state} err={version.error}")


async def import_phase1_mysql_backup(
    backup_root: Path,
    sf: async_sessionmaker[AsyncSession],
    archive: ArchiveStore,
    *,
    ocr_cache_dir: Path | None = None,
    supplement_manifest: Path | None = None,
    fallback_ocr: OcrEngine | None = None,
) -> Phase1MysqlImportStats:
    stats = Phase1MysqlImportStats()
    ocr = CachedOcrEngine(ocr_cache_dir, fallback=fallback_ocr)

    async with sf() as session:
        existing_hashes = set(
            (await session.scalars(select(DocumentVersion.content_hash))).all()
        )

    sql_text = (backup_root / "mysql_backup.sql").read_text(encoding="utf-8", errors="replace")
    regs_list = parse_mysql_table(sql_text, "policy_regulation")
    atts_list = parse_mysql_table(sql_text, "policy_attachment")
    regs_by_id = {int(r["id"]): r for r in regs_list if r.get("id") is not None}

    tar_by_bn: dict[str, tuple[str, bytes]] = {}
    with tarfile.open(backup_root / "all_documents_backup.tar.gz", "r:gz") as tf:
        for m in tf:
            if m.isfile():
                b = tf.extractfile(m).read()  # type: ignore[union-attr]
                bn = pathlib.PurePosixPath(m.name).name
                tar_by_bn[bn] = (m.name, b)

    # 1. 导入 mysql_backup.sql 中的 166 部法规（保护既有 6 国，并将误挂在 GB 下的一期英文版欧盟 GDPR 归位至 EU）
    OCR_LANG_OVERRIDE = {
        207: "th", 114: "ms", 118: "en", 246: "en", 65: "ru",
        297: "en", 298: "vi", 300: "vi", 301: "vi", 303: "vi", 309: "en",
        314: "bn", 316: "bn", 323: "ar", 324: "ar", 325: "ar",
        326: "ar", 327: "ar", 328: "ar", 331: "en", 333: "en",
    }
    for att in atts_list:
        rid_raw = att.get("regulation_id")
        if not isinstance(rid_raw, int) or rid_raw not in regs_by_id:
            continue
        reg = regs_by_id[rid_raw]
        cc = str(reg.get("country_code") or "").strip().upper()
        if cc in PROTECTED_COUNTRIES:
            stats.skipped_protected += 1
            continue

        title_zh = str(reg.get("regulation_title") or "").strip()
        title_en = str(reg.get("regulation_title_en") or "").strip() or None
        src_url: str | None = None
        if rid_raw == 219 and "欧盟通用数据保护条例" in title_zh:
            cc = "EU"
            title_en = "Regulation (EU) 2016/679 of the European Parliament and of the Council (General Data Protection Regulation)"
            src_url = "https://eur-lex.europa.eu/eli/reg/2016/679/oj"

        bn = pathlib.PurePosixPath(str(att.get("file_url_or_path") or "").replace("\\", "/")).name
        hit = tar_by_bn.get(bn)
        if hit is None:
            stats.errors.append(f"[{cc}] rid={rid_raw} missing file {bn}")
            continue
        _, blob = hit
        ext = pathlib.PurePosixPath(bn).suffix.lower()

        await _ingest_one_item(
            sf,
            archive,
            ocr,
            existing_hashes,
            cc=cc,
            title_zh=title_zh,
            title_orig_hint=title_en,
            category=str(reg.get("category") or ""),
            source_url=src_url,
            published_on=_parse_date(reg.get("publish_date")),
            effective_on=_parse_date(reg.get("effective_date")),
            legal_status_raw=str(reg.get("legal_status") or ""),
            blob=blob,
            ext=ext,
            forced_lang=OCR_LANG_OVERRIDE.get(rid_raw),
            stats=stats,
        )

    # 2. 吸纳备份包中未挂接表记录的净增官方法规 PDF（澳大利亚首都领地 2014 年信息隐私法）
    unlinked_act = tar_by_bn.get("5689eef5_2014年信息隐私法.PDF")
    if unlinked_act is not None:
        await _ingest_one_item(
            sf,
            archive,
            ocr,
            existing_hashes,
            cc="AU",
            title_zh="2014年信息隐私法（首都领地）",
            title_orig_hint="Information Privacy Act 2014 (Australian Capital Territory)",
            category="个人数据保护",
            source_url="https://www.legislation.act.gov.au/a/2014-24/",
            published_on=date(2014, 6, 11),
            effective_on=date(2014, 9, 1),
            legal_status_raw="in_force",
            blob=unlinked_act[1],
            ext=".pdf",
            forced_lang="en",
            stats=stats,
        )

    # 3. 吸纳一期前端制度原文目录 `.xlsx` 官方链接直采成功的净增法规（巴西 LGPD/EC115、菲律宾 DPA/IRR、土耳其跨境条例）
    if supplement_manifest and supplement_manifest.exists():
        sup_dir = supplement_manifest.parent
        sup_items = json.loads(supplement_manifest.read_text(encoding="utf-8"))
        for item in sup_items:
            fpath = sup_dir / item["file"]
            if not fpath.exists():
                continue
            blob = fpath.read_bytes()
            ext = fpath.suffix.lower()
            await _ingest_one_item(
                sf,
                archive,
                ocr,
                existing_hashes,
                cc=str(item["cc"]).upper(),
                title_zh=str(item["title_zh"]),
                title_orig_hint=str(item["title"]),
                category="个人数据保护",
                source_url=str(item["url"]),
                published_on=None,
                effective_on=None,
                legal_status_raw="in_force",
                blob=blob,
                ext=ext,
                forced_lang=str(item["lang"]),
                stats=stats,
            )

    return stats
