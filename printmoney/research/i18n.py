"""Thai and English wording for anything a person actually reads.

The calendar entry and the phone page are built from structured data rather than
from the English prose in ``brief.py``, so switching language never means
translating a sentence that has already been assembled - it means assembling a
different sentence from the same numbers.
"""
from __future__ import annotations

from typing import Any

DEFAULT_LANG = "th"
LANGS = ("th", "en")


def norm(lang: str | None) -> str:
    lang = (lang or DEFAULT_LANG).lower()[:2]
    return lang if lang in LANGS else DEFAULT_LANG


# --------------------------------------------------------------------------- #
#: Market names as a Thai reader would say them, not transliterations.
MARKET_TH: dict[str, str] = {
    "SPY": "หุ้นสหรัฐ S&P 500",
    "QQQ": "หุ้นเทค Nasdaq 100",
    "IWM": "หุ้นเล็กสหรัฐ",
    "DIA": "ดาวโจนส์",
    "EFA": "หุ้นประเทศพัฒนาแล้ว (นอกสหรัฐ)",
    "EEM": "ตลาดเกิดใหม่",
    "EWJ": "หุ้นญี่ปุ่น",
    "FXI": "หุ้นจีน",
    "THD": "หุ้นไทย",
    "GLD": "ทองคำ",
    "SLV": "เงิน",
    "USO": "น้ำมัน",
    "TLT": "พันธบัตรสหรัฐระยะยาว",
    "HYG": "หุ้นกู้ผลตอบแทนสูง",
    "VNQ": "อสังหาฯ สหรัฐ",
    "XLE": "กลุ่มพลังงาน",
    "XLF": "กลุ่มการเงิน",
    "XLK": "กลุ่มเทคโนโลยี",
    "XLV": "กลุ่มสุขภาพ",
    "XLU": "กลุ่มสาธารณูปโภค",
    "XLP": "กลุ่มสินค้าจำเป็น",
    "UUP": "ดอลลาร์สหรัฐ",
    "BTC-USD": "บิตคอยน์",
    "ETH-USD": "อีเธอเรียม",
}


def market_name(symbol: str, english: str, lang: str) -> str:
    if norm(lang) == "th":
        return MARKET_TH.get(symbol, english)
    return english


# --------------------------------------------------------------------------- #
STRINGS: dict[str, dict[str, str]] = {
    "th": {
        "calendar_name": "ช้างขาว",
        "title_quiet": "ช้างขาว: วันนี้ไม่มีอะไร",
        "title_actions": "ช้างขาว: มี {n} อย่างที่ควรทำ",
        "title_failed": "ช้างขาว: ดึงข้อมูลไม่ได้",
        "verdict_quiet": "วันนี้ไม่มีอะไรต้องทำ ไม่มีสัญญาณไหนคุ้มค่าธรรมเนียมที่ต้องจ่าย",
        "verdict_actions": "มี {n} อย่างที่ผ่านเกณฑ์ต้นทุนวันนี้",
        "verdict_failed": "บรีฟไม่สมบูรณ์ — {error}",
        "hdr_actions": "ควรทำ",
        "hdr_movers": "ขยับมากสุด",
        "hdr_stretched": "ยืดตัวผิดปกติ",
        "hdr_carry": "ผลตอบแทน funding carry",
        "hdr_danger": "ระวัง — ผันผวนสูงกว่าปกติของตัวเอง",
        "danger_line": "  {name}: ผันผวน {vol} ({pct} ของประวัติตัวเอง) {dd} จากจุดสูงสุดปีนี้",
        "danger_note": (
            "ความผันผวนคือสิ่งเดียวที่พยากรณ์ได้จริง — เดือนนี้ทำนายเดือนหน้าได้ r = +0.76 "
            "จาก 24 ตลาด ส่วนผลตอบแทนได้แค่ +0.02 นี่บอกว่า *ความเสี่ยงอยู่ตรงไหน* "
            "ไม่ได้บอกว่าจะขึ้นหรือลง"
        ),
        "risk_extreme": "ร้อนจัด",
        "risk_elevated": "เริ่มร้อน",
        "risk_normal": "ปกติ",
        "risk_calm": "สงบ",
        "hdr_notes": "หมายเหตุ",
        "mover_line": "  {name}: {day} วันนี้, {month} ในเดือน",
        "stretched_line": "  {name}: {z} SD จากค่าเฉลี่ย 60 วัน ({month} ในเดือน)",
        "stretched_note": (
            "ยืดตัวคือข้อเท็จจริงเรื่องราคา ไม่ใช่สัญญาณ — งานวิจัยพบว่ากฎ "
            "mean-reversion แยกไม่ออกจากการสุ่ม"
        ),
        "carry_line": "  ตะกร้าให้ {rate} ต่อปีสุทธิ = {monthly} ต่อเดือน บนทุน {capital}",
        "carry_below": "  ต่ำกว่าเกณฑ์ {threshold} จึงยังไม่ทำอะไร",
        "carry_above": "  สูงกว่าเกณฑ์ {threshold} คุ้มที่จะเปิดสถานะ",
        "footer": (
            "ส่วนใหญ่มันจะบอกว่าไม่มีอะไร และส่วนใหญ่นั่นคือคำตอบที่ถูก — "
            "รัน `pm study` เพื่อดูเลขคณิต 10 ปีที่อยู่เบื้องหลัง"
        ),
        "page_title": "ช้างขาว — บรีฟประจำวัน",
        "th_where": "ภาพรวมตลาด",
        "th_market": "ตลาด",
        "th_day": "วันนี้",
        "th_week": "สัปดาห์",
        "th_month": "เดือน",
        "th_vol": "ผันผวน",
        "page_footer": (
            "สร้างจากข้อมูลตลาดสาธารณะ ไม่ใช่คำแนะนำการลงทุน "
            "บรีฟนี้จะบอกว่า &ldquo;ไม่มีอะไร&rdquo; เกือบทุกวัน ซึ่งเป็นคำตอบที่ถูกเกือบทุกวัน"
        ),
    },
    "en": {
        "calendar_name": "printmoney",
        "title_quiet": "printmoney: nothing today",
        "title_actions": "printmoney: {n} to act on",
        "title_failed": "printmoney: brief failed",
        "verdict_quiet": "Nothing today. No signal clears what it would cost to act on it.",
        "verdict_actions": "{n} thing(s) cleared the cost bar today.",
        "verdict_failed": "Brief incomplete - {error}",
        "hdr_actions": "ACT ON",
        "hdr_movers": "BIGGEST MOVES",
        "hdr_stretched": "STRETCHED",
        "hdr_carry": "FUNDING CARRY",
        "hdr_danger": "RUNNING HOT (vs their own history)",
        "danger_line": "  {name}: {vol} vol ({pct} of its own history), {dd} off the year high",
        "danger_note": (
            "Volatility is the one thing here that is forecastable - this month "
            "predicted next month at r = +0.76 across 24 markets, while return managed "
            "+0.02. This says where the risk is, never which way it goes."
        ),
        "risk_extreme": "extreme",
        "risk_elevated": "elevated",
        "risk_normal": "normal",
        "risk_calm": "calm",
        "hdr_notes": "NOTES",
        "mover_line": "  {name}: {day} on the day, {month} on the month",
        "stretched_line": "  {name}: {z} SD from its 60-day mean ({month} on the month)",
        "stretched_note": (
            "Stretched is a fact about price, not a signal - the study found "
            "mean-reversion rules indistinguishable from random."
        ),
        "carry_line": "  basket nets {rate} a year = {monthly} a month on {capital}",
        "carry_below": "  below the {threshold} bar, so nothing to do",
        "carry_above": "  above the {threshold} bar, worth opening",
        "footer": (
            "Most days this says nothing, and most days that is correct. "
            "Run `pm study` for the ten years of arithmetic behind it."
        ),
        "page_title": "printmoney - daily brief",
        "th_where": "where things stand",
        "th_market": "market",
        "th_day": "day",
        "th_week": "week",
        "th_month": "month",
        "th_vol": "vol",
        "page_footer": (
            "Generated from public market data. Not investment advice. "
            "This brief says &ldquo;nothing today&rdquo; most days, which is the correct "
            "answer most days."
        ),
    },
}


def t(key: str, lang: str = DEFAULT_LANG, **kwargs: Any) -> str:
    """Look up a string, falling back to English then to the key itself."""
    lang = norm(lang)
    text = STRINGS.get(lang, {}).get(key) or STRINGS["en"].get(key) or key
    try:
        return text.format(**kwargs) if kwargs else text
    except (KeyError, IndexError):
        return text


def calendar_name(lang: str = DEFAULT_LANG) -> str:
    return t("calendar_name", lang)
