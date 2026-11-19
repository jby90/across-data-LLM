"""导入所有 ORM 模型,保证任何入口(API、worker、CLI、迁移)里外键都能解析。

新增模型模块时必须在这里登记。
"""

from app.admission import models as admission_models  # noqa: F401
from app.analysis import attachments as attachment_models  # noqa: F401
from app.analysis import document_instances as document_instance_models  # noqa: F401
from app.analysis import document_narrative as document_narrative_models  # noqa: F401
from app.analysis import document_templates as document_template_models  # noqa: F401
from app.analysis import models as analysis_models  # noqa: F401
from app.audit import models as audit_models  # noqa: F401
from app.core import egress_models  # noqa: F401
from app.forms import models as forms_models  # noqa: F401
from app.iam import models as iam_models  # noqa: F401
from app.ingestion import models as ingestion_models  # noqa: F401
from app.knowledge import country_page_text as country_page_text_models  # noqa: F401
from app.knowledge import models as knowledge_models  # noqa: F401
from app.knowledge import whitepaper as whitepaper_models  # noqa: F401
from app.opinion import models as opinion_models  # noqa: F401
from app.sources import models as sources_models  # noqa: F401
from app.standards import models as standards_models  # noqa: F401
from app.updates import models as updates_models  # noqa: F401
