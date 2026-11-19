# ruff: noqa: E501  国家名称表是数据
"""What the platform can fill on an official form without asking: values that follow from the
project (who files, to which country, whether that country has a supervisory authority, the legal
basis of the transfer when the receiving country is not on the sending country's adequacy list).

Keyed on what an item means (its id in the catalogue), the same for every form that has such an
item; the country names are written in the form's own language, as the authority reads them."""

from __future__ import annotations

import re
from typing import Any

from app.analysis.document_basis import country_of

_CORP_RE = re.compile(
    r"\b(?:SARL|SAS|SASU|SA|SUARL|Ltd|Pte|LLC|Inc|GmbH|Corp|Company|Soci[eé]t[eé]|Automotive|Mobility|Motors|Technologies)\b|公司|企业|集团|车企|有限",
    re.IGNORECASE,
)
_PUB_RE = re.compile(r"\b(?:Minist[eè]re|Public|Gouvernement|Agence Nationale|Office National)\b|政府|机关|事业单位|部委", re.IGNORECASE)

# 表格语言里的国家名称
_NAMES = {
    "fr": {"CN": "Chine", "MA": "Maroc", "EG": "Égypte", "TN": "Tunisie", "LB": "Liban", "SG": "Singapour", "FR": "France", "DE": "Allemagne", "ES": "Espagne", "IT": "Italie", "US": "États-Unis", "GB": "Royaume-Uni", "AE": "Émirats arabes unis", "SA": "Arabie saoudite", "JP": "Japon", "KR": "Corée du Sud"},
    "en": {"CN": "China", "MA": "Morocco", "EG": "Egypt", "TN": "Tunisia", "LB": "Lebanon", "SG": "Singapore", "FR": "France", "DE": "Germany", "ES": "Spain", "IT": "Italy", "US": "United States", "GB": "United Kingdom", "AE": "United Arab Emirates", "SA": "Saudi Arabia", "JP": "Japan", "KR": "Republic of Korea"},
}
# 设有数据保护监管机构的国家（其个人数据保护法设立了监管机构）
_AUTHORITY = {"CN", "MA", "EG", "TN", "SG", "FR", "DE", "ES", "IT", "NL", "BE", "SE", "PL", "AT", "IE", "PT", "CZ", "HU", "GB", "CH", "NO", "JP", "KR", "TH", "MY", "ID", "VN", "PH", "IN", "TR", "AE", "SA", "QA", "IL", "JO", "DZ", "ZA", "NG", "KE", "BR", "AR", "CL", "PE", "CO", "MX", "CA", "AU", "NZ", "HK", "MO"}


def country_name(code: str | None, lang: str) -> str:
    if not code:
        return ""
    return _NAMES.get(lang, _NAMES["en"]).get(code, "")


def value(item: dict[str, Any], facts: dict[str, str], form: dict[str, Any]) -> Any:
    """The derived value of ``item`` (option id, list of ids, or text); None when it does not follow."""
    iid, kind = item.get("id", ""), item.get("kind")
    options = item.get("options") or {}
    lang = form.get("language") or "fr"
    source, recipient = country_of(facts.get("source_country")), country_of(facts.get("recipient_country"))
    if kind == "radio" and iid.endswith("_type") and {"physique", "morale"} <= set(options):
        return "morale"  # 车企和接收方都是法人
    company_hint = " ".join(
        str(facts.get(k) or "") for k in ("company_name", "applicant_name", "exporter_name", "controller_name")
    ).strip()
    if kind == "radio" and company_hint and not _PUB_RE.search(company_hint) and _CORP_RE.search(company_hint):
        if iid == "s2_nature" and "societe" in options:
            return "societe"
        opt_zh = item.get("option_labels_zh") or {}
        if iid == "q_nature" and "o3" in options and str(opt_zh.get("o3") or "").startswith("公司"):
            return "o3"
    if kind == "text" and iid == "ctrl_pays":
        return country_name(form.get("country") or source, lang) or None
    if kind == "text" and iid == "dest_pays":
        return country_name(recipient, lang) or None
    if kind == "radio" and iid == "dest_authority" and recipient:
        return "oui" if recipient in _AUTHORITY else None
    if kind == "check" and iid == "derog_cases" and facts.get("recipient_adequate") == "否" and "garanties" in options:
        return ["garanties"]  # 不在充分保护名单内：以合同条款保证保护水平，申请授权（附条款文本）
    fk = facts.get("function_key")
    if kind == "text" and iid == "file_description" and fk in _CONTENT and not facts.get("data_types"):
        return _CONTENT[fk].get(lang, _CONTENT[fk]["en"])
    if kind == "text" and iid == "file_mode" and fk in _PURPOSE:
        return {"fr": "Transfert électronique chiffré (réseau) vers la plateforme cloud du destinataire", "en": "Encrypted electronic transfer over the network to the recipient's cloud platform"}.get(lang, "Encrypted electronic transfer")
    if kind == "text" and iid == "file_frequency" and fk in ("connected_vehicle_cloud", "autonomous_trial", "fleet_mobility"):
        return {"fr": "Continue (remontée périodique des données des véhicules)", "en": "Continuous (periodic upload of vehicle data)"}.get(lang, "Continuous")
    if kind == "radio" and iid == "sec_7" and "oui" in options and fk in _PURPOSE:
        return "oui"  # 传输方式写的是加密传输
    if kind == "check" and iid == "dest_kind" and fk in _PURPOSE and "sous_traitant" in options:
        return ["sous_traitant"]  # 接收方是车企云平台的运营方（受托处理），和第 V 节勾的接收方是同一种
    if kind == "radio" and iid == "sec_3" and "oui" in options and facts.get("recipient_adequate") == "否":
        return "oui"  # 不在充分保护名单内：靠合同条款保证保护水平
    if kind == "matrix" and iid == "matrix_v" and fk in ("connected_vehicle_cloud", "autonomous_trial", "fleet_mobility", "hd_mapping"):
        stage = str(facts.get("project_stage") or facts.get("stage") or "")
        is_trial = fk == "autonomous_trial" or "试验" in stage or "研发" in stage
        who = {"recipients": ["soustraitant"]}  # 接收方是车企云平台的运营方（受托处理）；栏位里没有“总部”一项，不写“其他”以免放不下
        src_sensor = {"origin": ["autre"], "origin_other": "Capteurs du véhicule"}  # 车载定位与状态由车载传感器采集，非本人手填
        duration = "Durée des essais" if is_trial else "Durée du service"
        loc_purposes = {
            "autonomous_trial": "Essais de conduite autonome et supervision à distance",
            "fleet_mobility": "Gestion de flotte, répartition et suivi des trajets",
            "hd_mapping": "Collecte et mise à jour de données cartographiques",
        }
        loc_purpose = loc_purposes.get(
            fk,
            "Essais routiers, télédiagnostic et validation du véhicule"
            if is_trial
            else "Services connectés, navigation et assistance à distance",
        )
        id_purpose = (
            "Télédiagnostic, analyse technique et sécurité des essais"
            if is_trial
            else "Télédiagnostic, maintenance et sécurité du véhicule connecté"
        )
        return {
            "localisation": {"on": True, "persons": ["clients"], **src_sensor, **who, "purpose": loc_purpose, "duration": duration},
            "id_autres": {"on": True, "persons": ["clients"], **src_sensor, **who, "purpose": id_purpose, "duration": duration, "label_other": "VIN, état du véhicule"},
        }
    if kind == "text" and iid in ("file_name", "file_purpose") and facts.get("function_key") in _PURPOSE:
        name, purpose = _PURPOSE[facts["function_key"]].get(lang, _PURPOSE[facts["function_key"]]["en"])
        return name if iid == "file_name" else purpose
    return None


_CONTENT = {
    "connected_vehicle_cloud": {"fr": "Position géographique, état et diagnostic technique du véhicule, identifiant du véhicule (VIN)",
                                "en": "Geolocation, vehicle technical status and diagnostics, vehicle identification number (VIN)"},
    "ota_diagnostics": {"fr": "Journaux de pannes, données de diagnostic, version logicielle et identifiant du véhicule (VIN)",
                        "en": "Fault logs, diagnostic data, software version and vehicle identification number (VIN)"},
}

# 车辆数据用途在官方表格语言里的写法（文件名称、传输目的）
_PURPOSE = {
    "connected_vehicle_cloud": {"fr": ("Données télématiques des véhicules connectés", "Services connectés, télédiagnostic et synchronisation avec la plateforme cloud du constructeur"),
                                "en": ("Connected vehicle telematics data", "Connected vehicle services, remote diagnostics and synchronisation with the manufacturer's cloud")},
    "ota_diagnostics": {"fr": ("Données de diagnostic à distance et de mise à jour OTA", "Télédiagnostic, mises à jour logicielles à distance (OTA) et analyse des journaux de pannes"),
                        "en": ("Remote diagnostics and OTA data", "Remote diagnostics, over-the-air software updates and fault log analysis")},
    "autonomous_trial": {"fr": ("Données d'essais routiers de conduite autonome", "Essais routiers de conduite autonome, validation technique et supervision de sécurité à distance"),
                         "en": ("Autonomous driving road trial data", "Autonomous driving road trials, technical validation and remote safety monitoring")},
    "cockpit_biometrics": {"fr": ("Données d'interaction du cockpit intelligent", "Commande vocale à bord et détection de la vigilance du conducteur pour la sécurité routière"),
                           "en": ("Smart cockpit interaction data", "In-vehicle voice control and driver alertness detection for road safety")},
    "fleet_mobility": {"fr": ("Données d'exploitation de flotte et de mobilité", "Exploitation de flotte, planification des trajets, maintenance et services de mobilité"),
                       "en": ("Fleet operations and mobility data", "Fleet operations, route planning, maintenance and mobility services")},
    "sales_service": {"fr": ("Données clients de vente et de service après-vente", "Gestion des ventes de véhicules, garantie constructeur et service après-vente"),
                      "en": ("Vehicle sales and after-sales customer data", "Vehicle sales management, manufacturer warranty and after-sales service")},
}
