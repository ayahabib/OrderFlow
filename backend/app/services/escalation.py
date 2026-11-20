"""Hand a conversation to the store owner.

Creates an `escalations` row and sets the conversation to 'waiting_owner', so the
bot stops replying (handler checks the status). Which messages escalate is decided in
engine/rules.py and engine/order_flow.py; the owner's Telegram alert is sent by the
handler after the transaction commits (services/notifications.py).
"""

from typing import Literal
from uuid import UUID

import asyncpg

Rule = Literal[
    "complaint", "discount", "large_order", "allergy_ingredients", "unknown_zone",
    "human_requested", "not_in_data", "order_status", "return_request", "llm_failure",
]

# escalation_reason enum: forced_rule | low_confidence | customer_requested
_REASON = {"human_requested": "customer_requested", "not_in_data": "low_confidence"}

CUSTOMER_REPLY = {
    "human_requested": "أكيد، رح يتواصل معك صاحب المتجر بأقرب وقت.",
    "complaint": "بعتذر منك على هالشي. حوّلت رسالتك لصاحب المتجر وبيتواصل معك بأقرب وقت.",
    # Only used when the store has no delivery zones set up; out-of-area customers are declined in order_flow.
    "unknown_zone": "منطقتك مش من المناطق المسجلة عنا. حوّلت طلبك لصاحب المتجر ليتأكد من التوصيل ويرد عليك.",
    "llm_failure": "وصلتني رسالتك، وحوّلتها لصاحب المتجر ليرد عليك بأقرب وقت.",
    "discount": "الأسعار الخاصة والخصومات بيقررها صاحب المتجر. حوّلتله رسالتك وبيرد عليك بأقرب وقت.",
    "large_order": "الطلبيات الكبيرة بيتابعها صاحب المتجر بنفسه. حوّلتله طلبك وبيتواصل معك بأقرب وقت.",
    "allergy_ingredients": "ما بدي أعطيك معلومة غلط عن المكونات أو الحساسية. حوّلت سؤالك لصاحب المتجر وبيرد عليك.",
    "order_status": "حوّلت سؤالك عن طلبك لصاحب المتجر ليتأكد ويرد عليك بأقرب وقت.",
    "return_request": "حوّلت طلب الإرجاع أو التبديل لصاحب المتجر وبيتواصل معك بأقرب وقت.",
}
DEFAULT_REPLY = "بتأكدلك من هالشي وبرجعلك. حوّلت رسالتك لصاحب المتجر."


async def escalate(conn: asyncpg.Connection, store_id: UUID, conversation_id: UUID, question: str, rule: Rule) -> str:
    """Record the escalation and pause the bot on this conversation. Returns the reply for the customer."""
    await conn.execute(
        """
        insert into escalations (store_id, conversation_id, question, reason, rule)
        values ($1, $2, $3, $4::escalation_reason, $5)
        """,
        store_id, conversation_id, question[:2000], _REASON.get(rule, "forced_rule"), rule,
    )
    await conn.execute(
        "update conversations set status = 'waiting_owner' where id = $1 and store_id = $2",
        conversation_id, store_id,
    )
    return CUSTOMER_REPLY.get(rule, DEFAULT_REPLY)
