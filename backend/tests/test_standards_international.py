from app.standards.international import in_domain, parse_row, status_of, topics_of


def test_a_catalogue_row_is_split_into_designation_title_stage_and_committee():
    e = parse_row(
        "ISO/TS 4654:2025 Road vehicles — Advanced automatic collision notification (AACN) systems "
        "— Methodology for creating algorithms 60.60 ISO/TC 22/SC 36",
        "https://www.iso.org/standard/80215.html",
    )
    assert e.number == "ISO/TS 4654:2025" and e.status == "in_force" and e.stage == "60.60"
    assert e.title.startswith("Road vehicles") and e.committee.startswith("ISO/TC 22")
    assert e.url.endswith("80215.html")


def test_numbers_with_dots_dashes_and_amendments_stay_whole():
    a = parse_row(
        "ISO/SAE DIS 1979-3.2 [Under development] Road vehicles — On-board diagnostics 40.60 ISO/TC 22/SC 31"
    )
    assert a is not None and a.number.startswith("ISO/SAE DIS 1979-3.2") and a.status == "draft"
    b = parse_row(
        "ISO 26262-1:2018 Road vehicles — Functional safety — Part 1: Vocabulary 95.99 ISO/TC 22/SC 32"
    )
    assert b.number == "ISO 26262-1:2018" and b.status == "withdrawn"


def test_rows_without_a_stage_and_headlines_are_not_entries():
    assert parse_row("Just released: the new edition of ISO 9001") is None
    assert parse_row("") is None


def test_stage_codes_map_to_status():
    assert status_of("60.60") == "in_force" and status_of("90.92") == "in_force"
    assert status_of("95.99") == "withdrawn" and status_of("40.20") == "draft"


def test_scope_keeps_vehicle_data_safety_and_security_titles_and_drops_parts_and_fuel():
    keep = (
        "Road vehicles — Cybersecurity engineering",
        "Road vehicles — Functional safety — Part 1: Vocabulary",
        "Road vehicles — Driver readiness and intervention management",
        "Intelligent transport systems — Cooperative systems — Messages",
        "Road vehicles — Diagnostic communication over Internet Protocol",
        "Road vehicles — Data communication between sensors and data fusion unit for automated driving",
    )
    drop = (
        "Road vehicles — Tyre rolling resistance test method",
        "Road vehicles — Fuel injection equipment — Test oils",
        "Road vehicles — Electric propulsion — Charging connectors",
        "Textiles — Determination of colour fastness",
        "Road vehicles — Multi-core connecting cables — Part 1: Test methods",
        "Road vehicles — Connection interface for pyrotechnic devices",
        "Road vehicles — Component test methods for electrical disturbances from narrowband radiated electromagnetic energy",
        "Road vehicles — Pedestrian protection — Child head impact test method",
        "Intelligent transport systems — Traffic and travel information messages via broadcast",
        "Electronic fee collection — Application interface definition for autonomous systems",
    )
    assert all(in_domain(t) for t in keep), [t for t in keep if not in_domain(t)]
    assert not any(in_domain(t) for t in drop), [t for t in drop if in_domain(t)]


def test_topics_follow_the_data_classes_of_the_requirement():
    assert "数据与信息安全" in topics_of("Road vehicles — Cybersecurity engineering")
    assert "路侧协同数据" in topics_of("Cooperative intelligent transport systems")
    assert topics_of("Plain paper") == ()
