"""Conservative quality gate for automatically extracted graph evidence."""

import re
import unicodedata

_ACTION = re.compile(
    r"负责|担任|隶属|属于|任职|组织|开展|建设|提供|用于|通过|将|由|向|对|"
    r"是|为|管理|承担|参与|执行|依据|按照|参考|包含|包括|形成|标识|出具|"
    r"发布|审核|报送|关联|组成|归口|主管|统筹|编制|维护|提出|实现|促进|支持|"
    r"开发|解决|申请|批准|审批|解释|指定|要求|部署|建立|联系"
)
_DATE = re.compile(r"(?:19|20)\d{2}(?:[-./年]\d{1,2})?")


def evidence_review_reason(
    quote: str,
    relation: str,
    source_type: str,
    target_type: str,
) -> str | None:
    """Flag short personnel/signature listings that state no explicit relationship.

    This only catches a narrow, observed error class. Other model relations
    remain unreviewed and must not be treated as verified facts.
    """
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", quote)).casefold()
    relation_text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", relation)).casefold()
    if len(compact) > 100 or not compact or (relation_text and relation_text in compact):
        return None
    if re.search(r"[。；;！!?？]", quote) or _ACTION.search(compact):
        return None
    has_person = source_type == "人物" or target_type == "人物"
    if has_person or _DATE.search(compact):
        return "signature_listing_without_predicate"
    return None
