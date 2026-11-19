"""Is a document about the three businesses of the project?

The project covers three things: in-vehicle (automotive) data, driving safety of intelligent
vehicles, and cross-border transfer of data. A statute belongs in the library when a meaningful part
of it deals with one of them (personal and vehicle data, cybersecurity and critical systems,
transfer abroad, vehicle approval and traffic rules for automated vehicles). A text that never
touches them (unfair competition, civil procedure, fintech licensing) does not.

The terms are data, one table per theme with the words each language uses; a new language is a new
row of words, not new logic. The check counts how many clauses mention a theme and how much of the
text they make up, so a long statute with one relevant chapter is kept and a short unrelated one
is not. It only proposes: the caller decides what to do with the answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

THEMES: dict[str, tuple[str, ...]] = {
    "车载与个人数据": (
        r"personal data",
        r"personal information",
        r"data protection",
        r"data subject",
        r"données à caractère personnel",
        r"données personnelles",
        r"protection des données",
        r"personenbezogene",
        r"datenschutz",
        r"datenverarbeitung",
        r"البيانات الشخصية",
        r"بيانات شخصية",
        r"حماية البيانات",
        r"البيانات ذات الطابع الشخصي",
        r"個人情報",
        r"개인정보",
        r"vehicle data",
        r"telematic",
        r"event data recorder",
        r"données du véhicule",
        r"fahrzeugdaten",
        r"بيانات المركبة",
        r"الصندوق الأسود",
    ),
    "数据出境与网络安全": (
        r"cross-border",
        r"transfer(?:red|s)? (?:of )?(?:personal )?data",
        r"third countr",
        r"transfert(?:s)? (?:de )?données",
        r"pays tiers",
        r"flux transfrontaliers",
        r"übermittlung",
        r"drittland",
        r"نقل البيانات",
        r"عبر الحدود",
        r"خارج (?:الدولة|الجمهورية|لبنان|مصر)",
        r"cyber",
        r"cybersécurité",
        r"sécurité des systèmes d.information",
        r"information security",
        r"it-sicherheit",
        r"informationssicherheit",
        r"الأمن السيبراني",
        r"أمن المعلومات",
        r"الأمن المعلوماتي",
        r"critical information infrastructure",
        r"kritische infrastruktur",
    ),
    "智驾与车辆安全": (
        r"autonomous vehicle",
        r"automated driving",
        r"self-driving",
        r"driverless",
        r"driving automation",
        r"véhicule autonome",
        r"conduite automatisée",
        r"autonomes fahren",
        r"automatisierte fahrfunktion",
        r"قيادة ذاتية",
        r"المركبات ذاتية",
        r"المركبات ذاتية القيادة",
        r"motor vehicle",
        r"road vehicle",
        r"vehicle type approval",
        r"véhicule(?:s)? à moteur",
        r"homologation",
        r"kraftfahrzeug",
        r"fahrzeug",
        r"المركبات",
        r"مركبة",
        r"السيارات",
        r"road traffic",
        r"circulation routière",
        r"straßenverkehr",
        r"قانون المرور",
        r"حركة المرور",
        r"道路交通",
        r"自動運転",
        r"자동차",
        r"자율주행",
    ),
}
_COMPILED = {name: re.compile("|".join(words), re.IGNORECASE) for name, words in THEMES.items()}

MIN_THEME_CLAUSES = 3  # fewer than this many clauses on a theme is a passing mention
MIN_SHARE = 0.03  # share of clauses (and of text) that touch a theme for the document to count


@dataclass(frozen=True)
class ScopeResult:
    in_scope: bool
    themes: dict[str, int]  # clauses per theme
    share: float  # share of clauses touching at least one theme


def assess_scope(clause_texts: list[str]) -> ScopeResult:
    total = len(clause_texts)
    if total == 0:
        return ScopeResult(False, {}, 0.0)
    counts = dict.fromkeys(THEMES, 0)
    touching = 0
    for text in clause_texts:
        hit = False
        for name, pattern in _COMPILED.items():
            if pattern.search(text):
                counts[name] += 1
                hit = True
        touching += hit
    share = touching / total
    on_theme = any(n >= MIN_THEME_CLAUSES for n in counts.values())
    return ScopeResult(on_theme and share >= MIN_SHARE, counts, share)
