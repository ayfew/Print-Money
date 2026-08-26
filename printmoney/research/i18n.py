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


#: Scheduled events, keyed by ``events.Event.kind`` rather than by their English
#: name, so a rename upstream cannot silently strand the Thai text.
EVENT_NAMES: dict[str, dict[str, str]] = {
    "th": {
        "fomc": "เฟดประกาศดอกเบี้ย",
        "payrolls": "ตัวเลขการจ้างงานสหรัฐ",
    },
    "en": {
        "fomc": "Fed rate decision (FOMC)",
        "payrolls": "US jobs report (non-farm payrolls)",
    },
}


def event_name(kind: str, lang: str) -> str:
    lang = norm(lang)
    return EVENT_NAMES.get(lang, {}).get(kind) or EVENT_NAMES["en"].get(kind, kind)


# --------------------------------------------------------------------------- #
STRINGS: dict[str, dict[str, str]] = {
    "th": {
        "calendar_name": "ช้างขาว",
        "title_quiet": "ช้างขาว: วันนี้ไม่มีอะไร",
        "title_actions": "ช้างขาว: มี {n} อย่างที่ควรทำ",
        "title_failed": "ช้างขาว: ดึงข้อมูลไม่ได้",
        "title_event": "ช้างขาว: {event} {when}",
        "title_risk": "ช้างขาว: {n} ตลาดกำลังร้อน",

        # ---- decision brief ------------------------------------------------
        "when_today": "วันนี้",
        "when_tomorrow": "พรุ่งนี้",
        "when_days": "อีก {days} วัน",
        "site_title": "ช้างขาว",
        "nav_today": "วันนี้",
        "nav_map": "แผนที่",
        "nav_evidence": "หลักฐาน",
        "chip_hint": "แตะชื่อตลาดหรือตัวเลขใดก็ได้ เพื่อกระโดดไปดูในแผนที่ว่าอะไรเชื่อมกับอะไร",
        "ev_intro": "ตัวเลขทั้งหมดนี้ commit ไว้ใน data/ ตรวจย้อนหลังได้ทีละ commit",
        "ev_scorecard": "บรีฟนี้ทายถูกกี่ %",
        "ev_indicators": "กวาดอินดิเคเตอร์ผ่านกำแพงค่าธรรมเนียม",
        "ev_contamination": "LLM จำอดีตได้แค่ไหน",
        "ev_impacts": "เหตุการณ์ตามกำหนดการขยับตลาดแค่ไหน",
        "ev_macro": "ค่าไหนเคลื่อนกับตลาดไหน",
        "ev_none": "ยังไม่มีหลักฐานที่วัดไว้",
        "graph_title": "แผนที่ความเชื่อมโยง",
        "graph_link": "ดูแผนที่ความเชื่อมโยง — อะไรขยับอะไร และหลักฐานคืออะไร →",
        "site_url_label": "ดูฉบับเต็ม (แผนที่ + หลักฐาน)",
        "graph_intro": "ลากได้ คลิกที่จุดเพื่อดูว่าอะไรทำให้มันขยับ และมันไปทำให้อะไรขยับต่อ",
        "graph_search": "ค้นหา...",
        "graph_click": "คลิกที่จุดใดก็ได้เพื่อเริ่ม",
        "graph_arithmetic": "เป็นเลขคณิต",
        "graph_documented": "มีเอกสารทางการ",
        "graph_measured": "วัดเองแล้ว",
        "graph_contested": "ยังเถียงกันอยู่",
        "graph_evidence": "เส้นแต่ละแบบ = หลักฐานคนละระดับ",
        "graph_legend_kind": "ประเภทของจุด",
        "graph_causes": "อะไรทำให้มันขยับ",
        "graph_effects": "มันทำให้อะไรขยับ",
        "graph_none": "ไม่มีในกราฟนี้",
        "graph_reset": "ล้างการเลือก",
        "hdr_chain": "สายเหตุผล",
        "hdr_context": "บริบทมหภาค",
        "hdr_why": "ทำไมถึงขยับ",
        "feed_effr": "ดอกเบี้ยนโยบายสหรัฐ (EFFR)",
        "feed_ust2y": "พันธบัตร 2 ปี",
        "feed_ust10y": "พันธบัตร 10 ปี",
        "feed_real10y": "ผลตอบแทนแท้จริง 10 ปี (TIPS)",
        "feed_curve": "ส่วนต่าง 2 ปี–10 ปี",
        "feed_vix": "VIX (ดัชนีความกลัว)",
        "feed_skew": "SKEW (ความเสี่ยงหางยาว)",
        "feed_vvix": "VVIX",
        "feed_auction_btc": "ยอดจองซื้อพันธบัตร (bid-to-cover)",
        "feed_auction_dealer": "สัดส่วนที่ดีลเลอร์ต้องรับไว้เอง",
        "feed_auction_indirect": "สัดส่วนที่ต่างชาติซื้อ",
        "feed_soma": "งบดุลเฟด (SOMA)",
        "curve_inverted": "กลับหัว",
        "curve_normal": "ปกติ",
        "strength_strong": "แรง",
        "strength_moderate": "ปานกลาง",
        "strength_weak": "อ่อน",
        "context_reading": "{label}: {value} ({change} จากเมื่อวาน, สูงกว่า {pct} ของ 2 ปีที่ผ่านมา)",
        "context_curve": "{label}: {value} — {state} ({change} จากเมื่อวาน)",
        "dir_with": "ไปทางเดียวกัน",
        "dir_against": "สวนทางกัน",
        "why_move": (
            "{name} {move} วันนี้ — {driver} ขยับ {change} "
            "ทั้งสองเคย{direction}ที่ r = {r} จาก {n} วัน (ความสัมพันธ์{strength})"
        ),
        "why_note": (
            "นี่คือการอธิบายสิ่งที่เกิดไปแล้ว ไม่ใช่การทำนาย — ค่า r วัดจากวันเดียวกัน "
            "ไม่ได้วัดว่าพรุ่งนี้จะเป็นยังไง"
        ),
        "hdr_focus": "วันนี้โฟกัสอะไร",
        "hdr_watch": "จับตา",
        "hdr_avoid": "ระวัง",
        "hdr_ignore": "ไม่ต้องสนใจ",
        "hdr_changed": "เปลี่ยนจากเมื่อวาน",
        "hdr_sources": "แหล่งข้อมูล",
        "hdr_score": "สถิติความแม่นของบรีฟนี้",
        "read_time": "อ่านประมาณ {sec} วินาที",

        "focus_event": (
            "{event}{when} — วันแบบนี้ตลาดขยับกว่าวันธรรมดา {ratio} "
            "ตัวที่ขยับจริงคือ {names}"
        ),
        "focus_risk": (
            "ความเสี่ยงวันนี้กระจุกอยู่ที่ {names} — {n} ตลาดผันผวนสูงสุด "
            "ของตัวเอง ตัวแรงสุดอยู่ที่ {vol} ({pct} ของประวัติตัวเอง)"
        ),
        "focus_watch": "มี {n} เรื่องที่ผ่านเกณฑ์ต้นทุนวันนี้ นอกนั้นไม่ต้องสนใจ",
        "focus_none": (
            "วันนี้ไม่มีอะไร — ทั้ง {n} ตลาดอยู่ในช่วงปกติของตัวเอง "
            "และไม่มีเหตุการณ์สำคัญในสัปดาห์นี้"
        ),
        "focus_careful": (
            "วันนี้ไม่มีอะไรต้องทำ แต่ {n} จาก {total} ตลาดผันผวนเกินปกติของตัวเอง "
            "เริ่มที่ {names} — เป็นเรื่องความเสี่ยง ไม่ใช่สัญญาณให้ซื้อหรือขาย"
        ),
        "focus_broken": (
            "ยังสรุปไม่ได้ — โหลดข้อมูลได้แค่ {loaded} จาก {requested} ตลาด "
            "อย่าเพิ่งเชื่อบรีฟฉบับนี้"
        ),

        "watch_event": (
            "{event} {when} ({note}) — จากสถิติ {n} ครั้ง วันแบบนี้ขยับ {ratio} "
            "ของวันปกติ แรงสุดถึง {top} ที่ควรดูคือ {names}"
        ),
        "watch_venue_spread": (
            "{symbol} จ่าย funding ไม่เท่ากันระหว่างกระดาน — long ที่ {long} ({long_rate}) "
            "short ที่ {short} ({short_rate}) ส่วนต่าง {spread} ต่อปี "
            "บนทุน {capital} แบ่งสองฝั่ง = {monthly} ต่อเดือน "
            "(มี {n} คู่ที่ผ่านเกณฑ์วันนี้) — ราคาหักล้างกัน แต่ต้องมีบัญชีสองที่"
        ),
        "ignore_venue_small": (
            "ส่วนต่าง funding ที่กว้างสุดคือ {symbol} {spread} ต่อปี "
            "แต่บนทุน {capital} ได้แค่ {monthly} ต่อเดือน — ไม่คุ้มเปิดบัญชีสองที่"
        ),
        "watch_carry": (
            "funding carry ให้ {rate} ต่อปีสุทธิ = {monthly} ต่อเดือน บนทุน {capital} "
            "ซึ่งเกินเกณฑ์ 15% แล้ว"
        ),
        "avoid_hot": (
            "{n} จาก {total} ตลาดผันผวนเกินปกติของตัวเอง เช่น {names} — "
            "สุดโต่งสุดคือ {name} ที่ผันผวน {worst_vol} ต่อปี "
            "สูงกว่า {worst_pct} ของประวัติตัวเอง"
        ),
        "ignore_untouched": (
            "{event}แทบไม่ขยับ {names} — จากสถิติ {n} ครั้งที่ผ่านมา วันแบบนี้ "
            "ขยับพวกนี้ต่ำสุดแค่ {ratio} ของวันปกติ คือไม่ต่างจากวันธรรมดา "
            "วันนี้ไม่ต้องเอาข่าวนี้ไปคิดกับพวกนี้"
        ),
        "ignore_calm": (
            "{n} จาก {total} ตลาดเงียบกว่าปกติของตัวเอง เช่น {names} "
            "ตลาดนิ่งคือสภาพที่แย่ที่สุดของอะไรก็ตามที่เสียค่าธรรมเนียมต่อครั้ง"
        ),
        "changed_risk_up": "{n} ตลาดความเสี่ยงขยับขึ้น ({was} → {now}): {names}",
        "changed_risk_down": "{n} ตลาดความเสี่ยงลดลง ({was} → {now}): {names}",
        "changed_event_entered": "{event}เข้ามาอยู่ในกรอบ 7 วันแล้ว ({when})",
        "changed_carry": "funding carry ข้ามเกณฑ์: {was} → {now} ต่อปี",
        "no_changes": "ไม่มีอะไรเปลี่ยนจากเมื่อวาน",
        "source_line": "  [{tier}] {name} — {url}",
        "score_line": (
            "ที่ผ่านมาบรีฟนี้ทายถูก {rate} จาก {n} ครั้ง (เหรียญได้ 50%) "
            "— ดูเองได้ด้วย `pm score`"
        ),
        "score_none": "ยังไม่มีสถิติพอจะรายงาน",
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
        "title_event": "printmoney: {event} {when}",
        "title_risk": "printmoney: {n} markets running hot",

        # ---- decision brief ------------------------------------------------
        "when_today": "today",
        "when_tomorrow": "tomorrow",
        "when_days": "in {days} days",
        "site_title": "printmoney",
        "nav_today": "today",
        "nav_map": "map",
        "nav_evidence": "evidence",
        "chip_hint": "Tap any market or reading to jump into the map and see what connects to what.",
        "ev_intro": "Every number here is committed under data/ and can be audited commit by commit.",
        "ev_scorecard": "how often this brief was right",
        "ev_indicators": "every indicator against the fee wall",
        "ev_contamination": "how much of the past an LLM remembers",
        "ev_impacts": "how far scheduled events move markets",
        "ev_macro": "which readings move with which markets",
        "ev_none": "no measured evidence yet",
        "graph_title": "Causal map",
        "graph_link": "Open the causal map - what moves what, and on what evidence →",
        "site_url_label": "Full version (map + evidence)",
        "graph_intro": "Drag it around. Click a node to see what moves it, and what it moves.",
        "graph_search": "search...",
        "graph_click": "Click any node to start.",
        "graph_arithmetic": "arithmetic",
        "graph_documented": "documented",
        "graph_measured": "measured",
        "graph_contested": "contested",
        "graph_evidence": "line style = kind of evidence",
        "graph_legend_kind": "node types",
        "graph_causes": "what moves it",
        "graph_effects": "what it moves",
        "graph_none": "nothing in this graph",
        "graph_reset": "clear selection",
        "hdr_chain": "CHAIN",
        "hdr_context": "MACRO BACKDROP",
        "hdr_why": "WHY THINGS MOVED",
        "feed_effr": "Fed funds rate (EFFR)",
        "feed_ust2y": "2-year Treasury",
        "feed_ust10y": "10-year Treasury",
        "feed_real10y": "10-year real yield (TIPS)",
        "feed_curve": "2s10s spread",
        "feed_vix": "VIX",
        "feed_skew": "SKEW",
        "feed_vvix": "VVIX",
        "feed_auction_btc": "auction bid-to-cover",
        "feed_auction_dealer": "primary dealer take-up",
        "feed_auction_indirect": "foreign/indirect take-up",
        "feed_soma": "Fed balance sheet (SOMA)",
        "curve_inverted": "inverted",
        "curve_normal": "normal",
        "strength_strong": "strong",
        "strength_moderate": "moderate",
        "strength_weak": "weak",
        "context_reading": "{label}: {value} ({change} on the day, above {pct} of the last two years)",
        "context_curve": "{label}: {value} - {state} ({change} on the day)",
        "dir_with": "with",
        "dir_against": "against",
        "why_move": (
            "{name} {move} today. {driver} moved {change}; the two have moved "
            "{direction} each other at r = {r} across {n} days ({strength})."
        ),
        "why_note": (
            "This explains what already happened; it is not a forecast. The "
            "correlation is same-day and says nothing about tomorrow."
        ),
        "hdr_focus": "WHAT TODAY IS ABOUT",
        "hdr_watch": "WATCH",
        "hdr_avoid": "CAREFUL",
        "hdr_ignore": "IGNORE",
        "hdr_changed": "CHANGED SINCE YESTERDAY",
        "hdr_sources": "SOURCES",
        "hdr_score": "THIS BRIEF'S OWN TRACK RECORD",
        "read_time": "about {sec} seconds to read",

        "focus_event": (
            "{event} {when}. Days like it move markets {ratio} an ordinary day, "
            "and the ones that actually move are {names}."
        ),
        "focus_risk": (
            "Today's risk sits in {names}. {n} markets are at the top of their own "
            "volatility range; the worst is at {vol}, {pct} of its own history."
        ),
        "focus_watch": "{n} thing(s) cleared the cost bar today. Nothing else needs you.",
        "focus_none": (
            "Nothing today. All {n} markets are inside their normal range and there "
            "is no scheduled event this week."
        ),
        "focus_careful": (
            "Nothing to do today, but {n} of {total} markets are more volatile than "
            "is normal for them, starting with {names}. That is a statement about "
            "risk, not a reason to buy or sell either one."
        ),
        "focus_broken": (
            "No verdict: only {loaded} of {requested} markets loaded. Do not trust "
            "this brief today."
        ),

        "watch_event": (
            "{event} {when} ({note}). Across {n} of them, days like it ran {ratio} "
            "an ordinary day, up to {top}. The ones to look at are {names}."
        ),
        "watch_venue_spread": (
            "{symbol} is funded differently across venues - long on {long} "
            "({long_rate}), short on {short} ({short_rate}), a {spread} a year "
            "spread. On {capital} split between them that is {monthly} a month "
            "({n} pairs clear the bar today). Price nets out; two accounts do not."
        ),
        "ignore_venue_small": (
            "The widest funding spread is {symbol} at {spread} a year, but on "
            "{capital} that is {monthly} a month - not worth two accounts."
        ),
        "watch_carry": (
            "Funding carry nets {rate} a year = {monthly} a month on {capital}, "
            "which is above the 15% bar."
        ),
        "avoid_hot": (
            "{n} of {total} markets are more volatile than is normal for them, "
            "including {names}. "
            "The most extreme is {name}, at {worst_vol} a year - above "
            "{worst_pct} of its own history."
        ),
        "ignore_untouched": (
            "{event} barely moves {names} - across {n} of them these ran as low as "
            "{ratio} an ordinary day, which is no different from a normal one. "
            "Do not read today's headline into them."
        ),
        "ignore_calm": (
            "{n} of {total} markets are quieter than usual for them, including "
            "{names}. A still tape is the worst environment for anything that pays "
            "a toll per trade."
        ),
        "changed_risk_up": "{n} markets got riskier ({was} -> {now}): {names}",
        "changed_risk_down": "{n} markets calmed down ({was} -> {now}): {names}",
        "changed_event_entered": "{event} has entered the 7-day window ({when})",
        "changed_carry": "Funding carry crossed the bar: {was} -> {now} a year",
        "no_changes": "Nothing changed since yesterday.",
        "source_line": "  [{tier}] {name} - {url}",
        "score_line": (
            "This brief has been right {rate} of {n} times so far (a coin gets 50%) "
            "- check it yourself with `pm score`"
        ),
        "score_none": "Not enough scored calls to report yet.",
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


# --------------------------------------------------------------------------- #
#: Thai and English both list with a comma, but Thai does not want the space
#: before the separator that a naive join produces around its own script.
def join_names(names: list[str], lang: str) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    sep = ", " if norm(lang) == "en" else " · "
    return sep.join(names)


def when_phrase(days: int, lang: str) -> str:
    if days <= 0:
        return t("when_today", lang)
    if days == 1:
        return t("when_tomorrow", lang)
    return t("when_days", lang, days=days)


def render_note(note: Any, lang: str = DEFAULT_LANG,
                names: dict[str, str] | None = None) -> str:
    """Turn a :class:`decide.Note` into a sentence in one language.

    The note is read by attribute rather than imported, which keeps this module
    free of any dependency on the analysis side and lets the same renderer serve
    the calendar, the page and the terminal.

    Three params are filled in here rather than at the callsite, because all
    three are language-shaped: the list of market names, the name of a scheduled
    event, and how to say "in three days".
    """
    lang = norm(lang)
    params = dict(getattr(note, "params", {}) or {})
    symbols = list(getattr(note, "symbols", ()) or ())
    lookup = names or {}

    if symbols:
        rendered = [market_name(s, lookup.get(s, s), lang) for s in symbols]
        params["names"] = join_names(rendered, lang)
        params["name"] = rendered[0]
    if "event_kind" in params:
        params["event"] = event_name(params["event_kind"], lang)
    if "days" in params:
        try:
            params["when"] = when_phrase(int(params["days"]), lang)
        except (TypeError, ValueError):
            params["when"] = ""

    # A handful of params are themselves keys rather than values: a feed name, a
    # curve state, a strength word. They are stored unlocalised so the analysis
    # side never has to know any Thai, and resolved here.
    for param, prefix in (("label", "feed_"), ("driver", "feed_"),
                          ("state", "curve_"), ("strength", "strength_"),
                          ("direction", "dir_")):
        if params.get(param):
            params[param] = t(prefix + params[param], lang)
    return t(getattr(note, "key", ""), lang, **params)
