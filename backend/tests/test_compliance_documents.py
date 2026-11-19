from app.analysis.compliance_documents import CATALOGUE, KNOWN_COUNTRIES, required_documents

TRANSFER = {"personal_data_transfer": True}


def ids(result):
    return {d["document_id"] for d in result["documents"]}


def test_china_to_eu_triggers_outbound_and_inbound_documents():
    result = required_documents("CN", "EU", TRANSFER)
    assert result["covered"]
    assert ids(result) == {
        "cn-outbound-standard-contract",
        "cn-outbound-pia-report",
        "eu-inbound-dpa-contract",
        "eu-inbound-dpia",
    }


def test_documents_depend_on_origin_and_destination_not_on_a_fixed_count():
    to_eu = ids(required_documents("CN", "EU", TRANSFER))
    to_sg = ids(required_documents("CN", "SG", TRANSFER))
    to_mx = ids(required_documents("SG", "EU", TRANSFER))
    assert to_eu != to_sg and to_sg != to_mx
    assert not any(i.startswith("cn-") for i in to_mx)


def test_a_business_condition_must_be_met_before_a_document_is_required():
    assert ids(required_documents("CN", "EU", {})) == set()
    assert "sg-av-trial-application" not in ids(required_documents("CN", "SG", TRANSFER))
    assert "sg-av-trial-application" in ids(required_documents("CN", "SG", {"road_trial": True}))


def test_only_documents_with_an_official_template_are_generated():
    result = required_documents("CN", "SG", TRANSFER)
    by_id = {d["document_id"]: d for d in result["documents"]}
    # 官方模板未逐项核对前一律不生成：当前只有核对过的才可生成，中国网信办、欧盟的模板尚未核对
    assert by_id["cn-outbound-standard-contract"]["template_status"] == "unverified"
    assert by_id["cn-outbound-standard-contract"]["generate"] is False
    sg = required_documents("CN", "SG", {"road_trial": True})
    trial = next(d for d in sg["documents"] if d["document_id"] == "sg-av-trial-application")
    assert trial["template_status"] == "official" and trial["generate"] is True
    # Listed as a requirement, but no official template: never generated.
    assert by_id["sg-pdpa-transfer-assurance"]["template_status"] == "none"
    assert by_id["sg-pdpa-transfer-assurance"]["generate"] is False
    eg = required_documents("CN", "EG", TRANSFER)
    assert all(
        d["generate"] is False for d in eg["documents"] if d["document_id"].startswith("eg-")
    )


def test_unknown_countries_are_reported_not_guessed():
    result = required_documents("CN", "ZZ", TRANSFER)
    assert not result["covered"]
    assert any("ZZ" in text for text in result["not_covered"])
    assert required_documents("", "EU", TRANSFER)["not_covered"]


def test_catalogue_entries_are_unique_and_state_their_basis():
    assert len({e.document_id for e in CATALOGUE}) == len(CATALOGUE)
    for entry in CATALOGUE:
        assert entry.basis and entry.authority
        assert entry.template_status in {"official", "none", "unverified"}
    assert {"CN", "EU", "SG", "EG", "TN", "MA", "LB"} <= KNOWN_COUNTRIES
