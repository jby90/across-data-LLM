"""Risk grading of automotive-data public-opinion items: high / medium / low.

The grade is decided by a rule table, not by the model. A model only reads an item and reports
facts (which data, how many people, whether it crosses the border, whether harm already
happened), each with the sentence of the item it comes from; a fact without such a sentence
counts as "no". The same facts always give the same grade, every grade names its rule and the
legal basis, and a new industry or a new threshold is a change of the table, not of this code.

The thresholds follow the rules in force for data leaving China (security assessment above one
million people's personal information or ten thousand people's sensitive personal information;
standard contract or certification from one hundred thousand people) and the list of important
data for automotive data (video and images of people or number plates outside the vehicle,
sensitive areas, traffic flows reflecting the economy, charging-network data, more than one
hundred thousand people's information).
"""

from __future__ import annotations

from dataclasses import dataclass, field

HIGH, MEDIUM, LOW = "high", "medium", "low"
ORDER = {LOW: 0, MEDIUM: 1, HIGH: 2}

PEOPLE_UNKNOWN = None


@dataclass(frozen=True)
class Facts:
    """What an item says, as extracted from the item itself. Every True carries its sentence."""

    important_data: tuple[str, ...] = ()  # which kinds of important data, if any
    outside_video_or_plates: bool = False  # people's faces or number plates filmed outside
    precise_location_or_biometrics: bool = False
    sensitive_area: bool = False
    people: int | None = PEOPLE_UNKNOWN  # accumulated number of people, if the item gives it
    sensitive_people: int | None = PEOPLE_UNKNOWN
    crosses_border: bool = False
    harm_happened: bool = False  # leak, unlawful export, regulator notice or penalty
    policy_signal: bool = False  # draft rule, consultation, regulator statement
    evidence: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Rule:
    code: str
    grade: str
    basis: str
    test: str  # name of a check below; the table, not the code, decides which rule applies


# Order does not matter: the highest grade among the rules that apply wins.
RULES: tuple[Rule, ...] = (
    Rule("important-data", HIGH, "《汽车数据安全管理若干规定（试行）》第三条", "important_data"),
    Rule("outside-video", HIGH, "《汽车数据安全管理若干规定（试行）》第三条", "outside_video"),
    Rule("sensitive-area", HIGH, "《汽车数据安全管理若干规定（试行）》第三条", "sensitive_area"),
    Rule("people-over-100k", HIGH, "《汽车数据安全管理若干规定（试行）》第三条", "people_100k"),
    Rule(
        "security-assessment",
        HIGH,
        "《促进和规范数据跨境流动规定》第七条",
        "assessment_scale",
    ),
    Rule("harm", HIGH, "《数据安全法》《个人信息保护法》法律责任章节", "harm"),
    Rule(
        "standard-contract-tier",
        MEDIUM,
        "《促进和规范数据跨境流动规定》第八条",
        "contract_scale",
    ),
    Rule("sensitive-data", MEDIUM, "《个人信息保护法》第二十八条", "sensitive_data"),
    Rule("policy", MEDIUM, "监管动向（征求意见稿、监管表态）", "policy"),
)


def _important(f: Facts) -> bool:
    return bool(f.important_data)


def _outside_video(f: Facts) -> bool:
    return f.outside_video_or_plates


def _sensitive_area(f: Facts) -> bool:
    return f.sensitive_area


def _people_100k(f: Facts) -> bool:
    return f.people is not None and f.people > 100_000


def _assessment_scale(f: Facts) -> bool:
    if not f.crosses_border:
        return False
    return (f.people is not None and f.people >= 1_000_000) or (
        f.sensitive_people is not None and f.sensitive_people >= 10_000
    )


def _contract_scale(f: Facts) -> bool:
    if not f.crosses_border or _assessment_scale(f):
        return False
    return (f.people is not None and f.people >= 100_000) or (
        f.sensitive_people is not None and 0 < f.sensitive_people < 10_000
    )


def _harm(f: Facts) -> bool:
    return f.harm_happened


def _sensitive_data(f: Facts) -> bool:
    return f.precise_location_or_biometrics


def _policy(f: Facts) -> bool:
    return f.policy_signal


TESTS = {
    "important_data": _important,
    "outside_video": _outside_video,
    "sensitive_area": _sensitive_area,
    "people_100k": _people_100k,
    "assessment_scale": _assessment_scale,
    "contract_scale": _contract_scale,
    "harm": _harm,
    "sensitive_data": _sensitive_data,
    "policy": _policy,
}


@dataclass(frozen=True)
class Grade:
    level: str
    rules: tuple[str, ...]
    bases: tuple[str, ...]
    scale_unknown: bool  # the item gives no head-count, so scale rules could not be applied

    @property
    def reason(self) -> str:
        return "；".join(self.bases)


def grade(facts: Facts) -> Grade:
    hit = [r for r in RULES if TESTS[r.test](facts)]
    level = max((r.grade for r in hit), key=ORDER.__getitem__, default=LOW)
    top = [r for r in hit if r.grade == level]
    unknown = facts.people is None and facts.sensitive_people is None and facts.crosses_border
    return Grade(
        level,
        tuple(r.code for r in top),
        tuple(dict.fromkeys(r.basis for r in top)),
        unknown,
    )


def keep_supported(facts: Facts) -> Facts:
    """Drop every claim the item itself does not back with a sentence."""
    ev = facts.evidence or {}

    def ok(key: str) -> bool:
        return bool((ev.get(key) or "").strip())

    return Facts(
        important_data=facts.important_data if ok("important_data") else (),
        outside_video_or_plates=facts.outside_video_or_plates and ok("outside_video_or_plates"),
        precise_location_or_biometrics=facts.precise_location_or_biometrics
        and ok("precise_location_or_biometrics"),
        sensitive_area=facts.sensitive_area and ok("sensitive_area"),
        people=facts.people if ok("people") else None,
        sensitive_people=facts.sensitive_people if ok("sensitive_people") else None,
        crosses_border=facts.crosses_border and ok("crosses_border"),
        harm_happened=facts.harm_happened and ok("harm_happened"),
        policy_signal=facts.policy_signal and ok("policy_signal"),
        evidence=dict(ev),
    )
