"""标准目录里的 UN 法规：从 UNECE 官方 1958 协定法规目录页读出各编号段的页面，每条法规链到列出它的那一段。"""

from app.standards.international import unece_page_for, unece_range_pages

PAGE = """
<li><a href="https://unece.org/node/19417">Regulations 121-140</a></li>
<li><a href="https://unece.org/node/19418">Regulations 141-160</a></li>
<li><a href="https://unece.org/transport/standards/x/addenda-1958-agreement-regulations-161-180">Regulations 161-180</a></li>
<li><a href="https://unece.org/178th-session">178th session</a></li>
"""


def test_each_regulation_links_to_the_official_range_page_that_lists_it():
    ranges = unece_range_pages(PAGE)
    assert len(ranges) == 3  # 会议届次（178th session）不是法规编号段
    assert unece_page_for("UN R155", ranges) == "https://unece.org/node/19418"
    assert unece_page_for("UN R160", ranges) == "https://unece.org/node/19418"
    assert unece_page_for("UN R178", ranges).endswith("regulations-161-180")
    assert unece_page_for("UN R190", ranges) is None
    assert unece_page_for("ISO 21434", ranges) is None
