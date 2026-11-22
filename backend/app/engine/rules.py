"""Forced-escalation rules (CLAUDE.md hard rule 5, PRD 4.11, ARCHITECTURE.md section 4 step 3).

Two independent detectors; either one is enough:
a) `keyword_rule`: patterns on normalized Arabic text. No LLM, so it still works when the
   LLM is down, and it runs before the LLM is even called.
b) The LLM's `sensitive_reason` and "a question we have no data for" (`llm_rule`).
Plus code checks done in the order flow: large order by the store's limits, no delivery zones.

An address outside the store's zones is NOT a forced escalation (owner's decision):
the order flow declines it and lists the zones.
"""

import re

from app.llm.schemas import ExtractedItem, MessageAnalysis
from app.services.escalation import Rule
from app.utils.arabic import normalize_arabic

# Patterns are written in normalized form: bare alef, ta marbuta -> ha, alef maqsura -> ya.
# Checked in this order; the first rule that matches wins.
_PATTERNS: list[tuple[Rule, list[str]]] = [
    ("human_requested", [
        r"(احكي|احاكي|اتكلم|اتواصل|احچي) مع (ال)?(حدا|حد|شخص|موظف|انسان|صاحب|مسؤول|مدير|اداره|ادمن)",
        r"(حول|وصل)(ني|يني|وني|ونا) ?(ل|لل|على|ع)? ?(ال)?(موظف|حدا|شخص|صاحب|مسؤول|مدير|اداره|انسان)",
        r"موظف",
        r"(انسان|شخص|حدا) حقيقي",
        r"بني ادم",
        r"خدمه (ال)?عملاء",
        r"(اتصلو|اتصلوا|اتصل|رنو|رنوا) (فيي|فيني|علي|عليي)",
        r"(رنولي|رنلي|اتصلولي)",
    ]),
    ("complaint", [
        r"شكو(ي|ه|ا)|اشتكي|بشتكي|اشتكيت",
        r"مكسور|مخزوق|مشقوق|مقطوع|خربان|معطل|تالف|مضروب|ممزع",
        r"زعلان|متضايق|مستاء|مش راضي|مو راضي|مش مبسوط|مقهور",
        r"(^| )(ال)?سي[يء]",  # سيء / سيئة
        r"(وصل|وصلني|وصلتني|اجاني|اجتني|جاني)\S* (\S+ ){0,3}(ناقص|غلط|غير|مختلف)",
        r"(مش|مو|ما) (مثل|متل|زي) (ال)?صور",
        r"غشاش|نصب|نصابين|حراميه|احتيال",
        r"تاخر|متاخر|تاخير",
        r"(رجع|رجعو|ارجع|استرجع)\S* ?(لي|ولي)? ?(ال)?(مصاري|فلوس|مبلغ|حقي)",
    ]),
    ("allergy_ingredients", [
        r"حساسي|تحسس|يحسس|بحسس",
        r"مكونات",
        r"كحول",
        r"للحامل|للحوامل|للرضع|للرضيع",
    ]),
    ("discount", [
        r"خصم|تخفيض|حسم|ديسكاونت|discount|كوبون",
        r"سعر (خاص|احسن|افضل|مميز)|اخر سعر",
        r"(نزل|قلل|خفف|وطي)\S*( \S+)? (ال)?سعر",
        r"ارخص شوي|بتعطيني ارخص",
    ]),
    ("large_order", [
        r"جمله",
        r"(كميه|كميات|طلبيه|طلبيات) كبير",
        r"عدد كبير",
    ]),
    ("return_request", [
        r"(بدي|بدنا|ابغي|عايز) (ا|ن)?رجع (ال)?\S",
        r"ارجاع|استرجاع|استبدال",
    ]),
    ("order_status", [
        r"وين (ال)?(طلب|اوردر|شحن|غرض|اغراض)",
        r"(طلب|اوردر|غرض)\S* (لسا|لسه|لهلا|لحد هلا|لحتي هلا)? ?(ما|مش) ?(وصل|اجا|اجت)",
        r"(متي|امتي|ايمتي|اديش|قديش)\S* (بيوصل|رح يوصل|بتوصل|بيصل) (ال)?(طلب|اوردر|غرض)",
        r"(تتبع|حاله|وضع) (ال)?(طلب|شحن|اوردر)",
    ]),
]

_COMPILED = [(rule, [re.compile(p) for p in patterns]) for rule, patterns in _PATTERNS]
_PUNCT = re.compile(r"[^\w\s]")
_REPEATS = re.compile(r"(.)\1{2,}")  # "زعلاااان" -> "زعلان"


def _prepare(text: str) -> str:
    text = normalize_arabic(text)
    text = _REPEATS.sub(r"\1", text)
    return " ".join(_PUNCT.sub(" ", text).split())


def keyword_rule(text: str) -> Rule | None:
    """The forced-escalation rule this message triggers by its words alone, or None."""
    prepared = _prepare(text)
    for rule, patterns in _COMPILED:
        if any(p.search(prepared) for p in patterns):
            return rule
    return None


def llm_rule(analysis: MessageAnalysis) -> Rule | None:
    """Escalation the LLM's reading implies: a sensitive reason, or a question with no order in it.

    Used by the escalation eval as "needs store data". Since task 2.2 the engine first tries
    engine/qa.py and only hands over when the store data has no answer (hard rule 4).
    """
    if analysis.sensitive_reason:
        return analysis.sensitive_reason
    if analysis.intent == "question" and not analysis.items:
        return "not_in_data"
    return None


def exceeds_quantity_limits(items: list[ExtractedItem], item_limit: int, total_limit: int) -> bool:
    """Large order by quantity alone (the amount limit needs prices, checked in order_totals)."""
    return any(i.quantity >= item_limit for i in items) or sum(i.quantity for i in items) >= total_limit


def decide(text: str, analysis: MessageAnalysis | None, item_limit: int, total_limit: int) -> Rule | None:
    """All message-level checks in the engine's order, for the escalation eval.

    `analysis=None` means the LLM failed, which always escalates (hard rule 6).
    """
    rule = keyword_rule(text)
    if rule:
        return rule
    if analysis is None:
        return "llm_failure"
    rule = llm_rule(analysis)
    if rule:
        return rule
    if exceeds_quantity_limits(analysis.items, item_limit, total_limit):
        return "large_order"
    return None
