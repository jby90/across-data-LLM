"""Country display metadata, not an allow-list of supported legal systems."""

from collections.abc import Iterable

# Preserve the original pilot labels and ordering, including empty pilot libraries.
# Further countries are discovered from records/configuration, not added here.
COUNTRY_NAMES: dict[str, tuple[str, str]] = {
    "SG": ("新加坡", "Republic of Singapore"),
    "EG": ("埃及", "Arab Republic of Egypt"),
    "TN": ("突尼斯", "Republic of Tunisia"),
    "MA": ("摩洛哥", "Kingdom of Morocco"),
    "LB": ("黎巴嫩", "Republic of Lebanon"),
}

DEFAULT_ORIG_NAMES: dict[str, str] = {
    "CN": "People's Republic of China",
    "EU": "European Union",
    "DE": "Federal Republic of Germany",
    "FR": "French Republic",
    "IT": "Italian Republic",
    "ES": "Kingdom of Spain",
    "NL": "Kingdom of the Netherlands",
    "BE": "Kingdom of Belgium",
    "SE": "Kingdom of Sweden",
    "PL": "Republic of Poland",
    "GB": "United Kingdom",
    "RU": "Russian Federation",
    "TR": "Republic of Türkiye",
    "JP": "Japan",
    "KR": "Republic of Korea",
    "TH": "Kingdom of Thailand",
    "MY": "Malaysia",
    "ID": "Republic of Indonesia",
    "VN": "Socialist Republic of Viet Nam",
    "PH": "Republic of the Philippines",
    "IN": "Republic of India",
    "PK": "Islamic Republic of Pakistan",
    "BD": "People's Republic of Bangladesh",
    "KZ": "Republic of Kazakhstan",
    "KG": "Kyrgyz Republic",
    "UZ": "Republic of Uzbekistan",
    "AE": "United Arab Emirates",
    "SA": "Kingdom of Saudi Arabia",
    "QA": "State of Qatar",
    "KW": "State of Kuwait",
    "OM": "Sultanate of Oman",
    "IL": "State of Israel",
    "DZ": "People's Democratic Republic of Algeria",
    "ZA": "Republic of South Africa",
    "NG": "Federal Republic of Nigeria",
    "US": "United States of America",
    "CA": "Canada",
    "MX": "United Mexican States",
    "BR": "Federative Republic of Brazil",
    "AR": "Argentine Republic",
    "CL": "Republic of Chile",
    "CO": "Republic of Colombia",
    "PE": "Republic of Peru",
    "AU": "Commonwealth of Australia",
    "NZ": "New Zealand",
}


def resolve_country_code(raw: str | None) -> str:
    """Resolve Chinese/English country name or ISO code to uppercase country code."""
    val = (raw or "").strip()
    if not val:
        return ""
    if len(val) == 2 and val.isascii() and val.isalpha():
        return val.upper()
    from app.analysis.document_basis import NAMES, country_of
    from app.analysis.workflow_engine import COUNTRY_NAMES_ZH

    hit = country_of(val)
    if hit:
        return hit
    low = val.casefold()
    for cc, (zh, orig) in COUNTRY_NAMES.items():
        if val == zh or low == orig.casefold():
            return cc
    for cc, orig in DEFAULT_ORIG_NAMES.items():
        if low == orig.casefold():
            return cc
    for cc, zh in {**NAMES, **COUNTRY_NAMES_ZH}.items():
        if val == zh:
            return cc
    if val in ("国际通用标准", "国际标准"):
        return "ZZ"
    return val.upper()


def country_catalog(codes: Iterable[str], site_metadata: Iterable[tuple[str, dict]]) -> list[dict]:
    """Unknown/conflicting display names fall back to the code, never inferred law facts."""
    discovered = {code.upper() for code in codes}
    metadata: dict[str, dict[str, set[str]]] = {}
    for code, config in site_metadata:
        if not isinstance(config, dict):
            continue
        names = metadata.setdefault(code.upper(), {})
        for key in ("country_name_zh", "country_name_orig"):
            value = config.get(key)
            if isinstance(value, str) and value.strip():
                names.setdefault(key, set()).add(value.strip())

    result = []
    for code in [*COUNTRY_NAMES, *sorted(discovered - COUNTRY_NAMES.keys())]:
        if code in COUNTRY_NAMES:
            zh, original = COUNTRY_NAMES[code]
        else:
            names = metadata.get(code, {})
            zh_values = names.get("country_name_zh", set())
            orig_values = names.get("country_name_orig", set())
            if len(zh_values) == 1:
                zh = next(iter(zh_values))
            elif len(zh_values) > 1:
                zh = code
            else:  # 采集站点没写国名的（如作为数据发出国入库的中国）：用平台的国家名称表
                from app.analysis.document_basis import NAMES

                zh = NAMES.get(code, code)
            original = (
                next(iter(orig_values))
                if len(orig_values) == 1
                else DEFAULT_ORIG_NAMES.get(code, code)
            )
        result.append({"country_code": code, "country_name_zh": zh, "country_name_orig": original})
    return result
