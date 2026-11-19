"""指向已不存在条文的引用：按引用自带的摘录在同一部法规的现存条文里找到对应条文改指向。"""

import uuid

from sqlalchemy import select

from app.analysis.models import AnalysisTask
from app.ingestion.ref_check import repair, scan

from .review_s1_s2_contracts import make_document


async def test_a_reference_to_a_gone_clause_is_pointed_at_the_clause_holding_the_same_text(session_factory, seed):
    async with session_factory() as s:
        doc, version, clauses = await make_document(s, texts=["An organisation must notify the Commission of a breach within three days of assessing it."])
        gone = str(uuid.uuid4())
        task = AnalysisTask(org_id=seed["org_a"].id, title="t", country_code="SG", vehicle_model="x", scenario_type="x", status="completed",
                            findings_json={"D1": [{"clause_id": gone, "document_id": str(doc.id), "snippet": "must notify the Commission of a breach within three days"}]})
        s.add(task)
        await s.commit()
        assert await scan(s) == 1
        done = await repair(s)
        await s.commit()
        assert done["found"] == 1 and await scan(s) == 0
    async with session_factory() as s2:  # 新会话里读，不拿上一个会话缓存的对象
        again = await s2.scalar(select(AnalysisTask).where(AnalysisTask.id == task.id))
        assert again.findings_json["D1"][0]["clause_id"] == str(clauses[0].id)
