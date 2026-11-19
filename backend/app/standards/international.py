# ruff: noqa: E501  词表是数据，不按代码行宽折行
"""International standards and regulations: directory entries from the catalogue pages.

Only the directory is kept (designation, title, stage, committee), never the text: standards belong
to the body that publishes them. What counts as in scope for the project (in-vehicle data, driving
safety of intelligent vehicles, cross-border transfer) is a table of English words below, in the
same way the Chinese titles are judged in ``collect.py``; another language is another row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 必须出现的主题词：车载数据、智能驾驶、车联网（感知、决策、控制、车载交互、路侧协同、数据安全）
STRONG = (
    "cyber",
    "software update",
    "software-update",
    "over-the-air",
    "automated driving",
    "driving automation",
    "autonomous",
    "automated lane",
    "lane keeping",
    "lane change",
    "advanced driver",
    "driver assistance",
    "driver readiness",
    "driver monitoring",
    "intervention management",
    "emergency braking",
    "collision avoidance",
    "collision mitigation",
    "functional safety",
    "safety of the intended",
    "sotif",
    "event data recorder",
    "data recorder",
    "accident emergency call",
    "ecall",
    "automatic collision notification",
    "advanced automatic collision",
    "cooperative",
    "c-its",
    "v2x",
    "vehicle-to-",
    "connected vehicle",
    "telematics",
    "diagnostic communication",
    "on-board diagnostic",
    "unified diagnostic",
    "vehicle communication interface",
    "sensors and data fusion",
    "data fusion",
    "perception",
    "lidar",
    "privacy",
    "data security",
    "data protection",
    "data exchange",
    "extended vehicle",
    "human-machine interface",
    "automated parking",
    "remote operation",
    "teleoperat",
    "roadside",
    "localized communications",
    "probe vehicle",
    "vehicle data",
)
VEHICLE = ()
SUBJECT = ()
# 出现这些词的标题讲的是线缆、接口件、电磁干扰、试验假人、碰撞试验、车身部件等，不属于上述主题
EXCLUDE = (
    "tyre",
    "tire",
    "wheel",
    "brake lining",
    "seat belt",
    "windscreen",
    "exhaust",
    "emission",
    "fuel",
    "engine oil",
    "paint",
    "wiper",
    "mirror",
    "lamp",
    "bulb",
    "horn",
    "glass",
    "trailer",
    "towing",
    "coupling",
    "motorcycle",
    "bicycle",
    "agricultural",
    "tractor",
    "charging",
    "battery",
    "hydrogen",
    "lubricant",
    "spark plug",
    "gasket",
    "bolt",
    "thread",
    "cable",
    "connector",
    "connection interface",
    "pyrotechnic",
    "tachograph",
    "wiring",
    "harness",
    "electromagnetic",
    "disturbance",
    "electrostatic",
    "environmental conditions",
    "switching device",
    "interior air",
    "dummy",
    "worldsid",
    "impact",
    "crash",
    "pedestrian protection",
    "head form",
    "measurement techniques",
    "media oriented",
    "optical",
    "pressure",
    "thermal",
    "noise",
    "vibration",
    "traffic and travel information",
    "tpeg",
    "broadcast",
    "tollin",
    "toll collection",
    "weigh",
    "freight",
    "rail",
    "tram",
    "fee collection",
    "equipment identification",
    "ticketing",
)
TOPIC_RULES = (
    ("感知数据", ("sensor", "lidar", "radar", "camera", "perception", "detection", "object")),
    (
        "决策数据",
        (
            "automated driving",
            "driving automation",
            "autonomous",
            "scenario",
            "planning",
            "safety of the intended",
            "sotif",
        ),
    ),
    (
        "控制数据",
        (
            "lane",
            "braking",
            "steering",
            "control",
            "driver assistance",
            "adas",
            "readiness",
            "intervention",
        ),
    ),
    (
        "车载交互数据",
        (
            "human-machine",
            "interface",
            "driver monitoring",
            "display",
            "voice",
            "infotainment",
            "diagnostic",
        ),
    ),
    (
        "路侧协同数据",
        (
            "cooperative",
            "v2x",
            "vehicle-to-",
            "intelligent transport",
            "roadside",
            "connected",
            "positioning",
        ),
    ),
    (
        "数据与信息安全",
        (
            "cyber",
            "security",
            "privacy",
            "data",
            "software",
            "update",
            "event data",
            "recorder",
            "remote",
        ),
    ),
    (
        "综合与测试",
        ("terminology", "vocabulary", "test", "evaluation", "assessment", "framework", "guideline"),
    ),
)
STAGE_STATUS = (("95.", "withdrawn"), ("90.", "in_force"), ("60.", "in_force"))

_DESIGNATION = re.compile(
    r"^(?P<d>.*?\d[\d\-.]*(?::\d{4})?(?:/(?:Amd|Cor|DAmd)\s*\d*:?\d*)*)\s+(?P<t>[A-Z].*)$"
)


@dataclass(frozen=True)
class IntlEntry:
    number: str
    title: str
    status: str
    stage: str
    committee: str
    url: str | None
    topics: tuple[str, ...]


def topics_of(title: str) -> tuple[str, ...]:
    low = title.lower()
    return tuple(name for name, words in TOPIC_RULES if any(w in low for w in words))


def in_domain(title: str) -> bool:
    low = title.lower()
    if any(w in low for w in EXCLUDE):
        return False
    return any(w in low for w in STRONG)


def status_of(stage: str) -> str:
    for prefix, status in STAGE_STATUS:
        if stage.startswith(prefix):
            return status
    return "draft"  # 20.xx–50.xx 在制定中，尚未发布，不计入现行


def parse_row(text: str, url: str | None = None) -> IntlEntry | None:
    """One catalogue row such as ``ISO/TS 4654:2025 Road vehicles — … 60.60 ISO/TC 22/SC 36``."""
    flat = re.sub(r"\s+", " ", text or "").strip()
    stage = re.search(r"\s(\d{2}\.\d{2})\s+(ISO\S*|IEC\S*|SAE\S*|ITU\S*|ETSI\S*)", flat)
    if not stage:
        return None
    head = re.sub(
        r"\s*\[[^\]]{3,40}\]", "", flat[: stage.start()]
    )  # “[Under development]”这类标注不属于标题
    m = _DESIGNATION.match(head)
    if not m:
        return None
    number, title = m.group("d").strip(), m.group("t").strip()
    if not number or len(title) < 8:
        return None
    return IntlEntry(
        number=number[:64],
        title=title[:500],
        status=status_of(stage.group(1)),
        stage=stage.group(1),
        committee=flat[stage.start(2) :][:80],
        url=url,
        topics=topics_of(title),
    )


_RANGE_LINK = re.compile(
    r'<a [^>]*href="(?P<url>https://unece\.org/[^"]+)"[^>]*>\s*Regulations\s+(?P<lo>\d+)\s*-\s*(?P<hi>\d+)\s*</a>',
    re.IGNORECASE,
)


def unece_range_pages(html: str) -> list[tuple[int, int, str]]:
    """The official pages of the UN Regulations addenda list, one per number range
    (“Regulations 141-160” → its page), read from the 1958 Agreement addenda page."""
    return [(int(m["lo"]), int(m["hi"]), m["url"]) for m in _RANGE_LINK.finditer(html or "")]


def unece_page_for(number: str, ranges: list[tuple[int, int, str]]) -> str | None:
    """The official page that lists ``UN R155`` (the range page holding regulation 155)."""
    m = re.fullmatch(r"UN R(\d+)", (number or "").strip())
    if not m:
        return None
    n = int(m.group(1))
    return next((url for lo, hi, url in ranges if lo <= n <= hi), None)
