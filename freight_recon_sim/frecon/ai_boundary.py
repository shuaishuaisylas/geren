"""Reserved boundary for an AI difference explanation (M-AI, not in this phase).

This phase connects no AI or other external service. If a future version adds one, it may
only read already-decided, redacted evidence fields; it may never write output_amount,
diff_amount or biz_status, and its failure must leave deterministic evidence unchanged
(ai_explanation_status=failed_isolated).
"""


def explanation_placeholder():
    return {"ai_explanation": None, "ai_explanation_status": "skipped"}
