"""长法规按业务相关章节入库：按编、章和随附法令切分；标题或所属部分讲业务、被引用、看不出标题的都在范围内；
质量卡只按范围内的条文算译文覆盖；新版本要重新判断。规则通用，不按国家写。"""

import uuid
from dataclasses import dataclass

from app.ingestion import intake_scope as S
from app.ingestion.quality_card import ClauseSample, assess


@dataclass
class C:
    ordinal: int
    original_text: str
    id: uuid.UUID = None

    def __post_init__(self):
        self.id = self.id or uuid.uuid4()


FR = [
    C(0, "Loi n° 1 du 1 janvier 2000, portant code de la route"),
    C(1, "TITRE PREMIER\nDISPOSITIONS GENERALES\nChapitre premier Vitesse\nArticle 1 La vitesse est limitée."),
    C(2, "Article 2 Le dépassement est interdit à droite."),
    C(3, "Chapitre II Immatriculation des véhicules\nArticle 3 Tout véhicule doit être immatriculé."),
    C(4, "Article 4 La plaque est apposée à l'arrière. " + "x" * 400 + "\nDécret n° 2000-148 du 24 janvier 2000, fixant la périodicité de la visite technique des véhicules"),
    C(5, "Chapitre premier Périodicité\nArticle 1 Les voitures sont soumises tous les ans."),
    C(6, "Arrêté du ministre du 5 février 2002, fixant la composition des commissions de l'enseignement de la conduite\nArticle 1 La commission comprend cinq membres."),
    C(7, "Article 2 Elle se réunit deux fois par an."),
]


def _part(parts, start):
    return next(p for p in parts if p.title.startswith(start))


def test_parts_follow_titles_chapters_and_decrees_printed_with_the_code():
    parts = S.cut_parts(FR)
    assert _part(parts, "Loi n° 1").first == 0
    chapter = _part(parts, "Chapitre II Immatric")
    assert (chapter.first, chapter.last) == (3, 4) and chapter.parent.startswith("TITRE PREMIER")
    assert _part(parts, "Décret n° 2000-148").first == 5  # 标题在上一条后半段：从下一条起算


def test_business_parts_are_in_and_unrelated_ones_out():
    parts = S.decide(S.cut_parts(FR), FR, cited=set())
    assert _part(parts, "Chapitre II Immatric").in_scope  # 标题讲登记与牌照
    assert _part(parts, "Décret n° 2000-148").in_scope  # 标题讲技术检验
    assert not _part(parts, "TITRE PREMIER").in_scope  # 限速
    assert not _part(parts, "Arrêté du ministre").in_scope  # 驾校委员会


def test_a_chapter_belongs_to_the_decree_above_it():
    rows = [
        C(0, "Décret n° 2002-1 du 4 septembre 2002, fixant les règles techniques d'équipement des véhicules"),
        C(1, "CHAPITRE PREMIER DISPOSITIONS RELATIVES AUX ORGANES\nArticle 1 Les réservoirs sont fixés solidement."),
    ]
    parts = S.decide(S.cut_parts(rows), rows, cited=set())
    assert _part(parts, "CHAPITRE PREMIER").in_scope and "所属部分" in _part(parts, "CHAPITRE PREMIER").why


def test_a_cited_clause_keeps_its_part_in_scope_and_untitled_text_stays_in():
    teaching = FR[6]
    parts = S.decide(S.cut_parts(FR), FR, cited={teaching.id})
    assert next(p for p in parts if teaching.id in p.clause_ids).in_scope
    lone = [C(0, "Some guidance without headings about roads."), C(1, "More text.")]
    assert all(p.in_scope for p in S.decide(S.cut_parts(lone), lone, cited=set()))


def test_the_same_rule_reads_an_english_code():
    en = [
        C(0, "PART I\nGENERAL\nArticle 1 Speed limits apply on all roads."),
        C(1, "PART II\nTYPE APPROVAL OF VEHICLES\nArticle 2 No vehicle may be registered without type approval."),
        C(2, "PART III\nDRIVING LICENCES\nArticle 3 A licence is valid for ten years."),
    ]
    state = [p.in_scope for p in S.decide(S.cut_parts(en), en, cited=set())]
    assert state == [False, True, False]


def test_fill_in_dots_are_not_a_table_of_contents():
    assert S._is_contents("Chapitre III : Saisie.......... 103 64\nChapitre IV : Immobilisation........ 104 65")
    assert not S._is_contents("Poids : …………… T\nHeure : ………… Minute : ………")


def test_quality_card_counts_translation_only_inside_the_scope():
    samples = [ClauseSample(str(i), "Le véhicule doit être immatriculé auprès du service compétent.", i < 3) for i in range(10)]
    whole = assess(samples, "fr", needs_translation=True)
    assert whole.translated == 0.3 and not whole.passed
    flags = [i < 3 for i in range(10)]
    scoped = assess(samples, "fr", needs_translation=True, in_scope=flags)
    assert scoped.translated == 1.0 and scoped.passed
    assert "只收与业务相关的章节：3 条" in scoped.scope_note


def test_scope_applies_to_the_version_it_was_decided_on():
    v1, v2 = uuid.uuid4(), uuid.uuid4()
    scope = {"mode": S.MODE, "version_id": str(v1), "in_scope_ordinals": [1, 2], "parts": []}
    assert S.out_of_scope(scope, v1, 5) and not S.out_of_scope(scope, v1, 2)
    assert not S.out_of_scope(scope, v2, 5)  # 新版本重新判断前按整份处理
    assert S.note_for(scope, v1, 5) == "与汽车数据和准入无关" and S.note_for({}, v1, 5) is None
