"""从职位描述（JD）里自动判断：JD 语言、德语要求、经验年限、级别。

全部是规则匹配，不调用任何外部服务。判断不一定 100% 准确，网页上会标注"自动判断"。
"""
from __future__ import annotations

import html
import re

# ─────────────────────────── JD 语言 ───────────────────────────

EN_WORDS = {
    "the", "and", "with", "you", "we", "our", "your", "for", "of", "to", "is", "are", "will", "be", "as", "on",
    "an", "this", "that", "who", "what", "have", "experience", "team", "skills", "working", "about", "role",
}
DE_WORDS = {
    "der", "die", "das", "und", "mit", "für", "wir", "sie", "ihre", "ihr", "bei", "von", "zu", "ist", "auf",
    "eine", "ein", "den", "dem", "des", "oder", "als", "sowie", "erfahrung", "kenntnisse", "unser", "unsere",
    "aufgaben", "profil", "bieten", "deine", "dein", "du", "dich", "im", "zur", "zum", "werden", "nicht",
}
TITLE_DE = re.compile(r"\b[mwf]\s*/\s*[wmf]\s*/\s*d\b|\(gn\*?\)|\b(und|für|mit|werkstudent\w*|mitarbeiter\w*|"
                      r"sachbearbeiter\w*|referent\w*|datenanalyst\w*|leiter\w*|berater\w*|kaufm\w*)\b", re.I)


def strip_html(text: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text or "", flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>|</li>|</h\d>", ". ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def detect_lang(text: str) -> str | None:
    """返回 'en' / 'de' / None（文字太少无法判断）。"""
    tokens = re.findall(r"[a-zäöüß]+", (text or "").lower())
    en = sum(t in EN_WORDS for t in tokens)
    de = sum(t in DE_WORDS for t in tokens)
    if en + de < 8:
        return None
    return "en" if en > de else "de"


def lang_from_title(raw_title: str) -> str | None:
    """只有标题时：出现 (m/w/d) 或德语词基本可以判断是德语招聘；否则不下结论。"""
    return "de" if TITLE_DE.search(raw_title or "") else None


# ─────────────────────────── 德语要求 ───────────────────────────

_S = r"[^.;:!?\n]{0,45}"   # 同一句话内、最多 45 个字符
GERMAN_NONE = re.compile(
    r"no german (?:is )?(?:required|needed|necessary)|german (?:is )?not (?:required|needed|necessary)|"
    r"without (?:any )?german|keine deutschkenntnisse|deutschkenntnisse (?:sind )?nicht (?:erforderlich|notwendig)|"
    r"english[- ]speaking (?:team|company|environment)|our (?:working|company) language is english|"
    r"(?:working|company|office) language(?: is)? english", re.I)
GERMAN_PLUS = re.compile(
    rf"german{_S}(?:is a plus|a plus|plus\b|nice[- ]to[- ]have|an advantage|advantageous|beneficial|preferred|"
    rf"helpful|bonus|welcome)|(?:basic|some) german|"
    rf"deutsch\w*{_S}(?:von vorteil|wünschenswert|wuenschenswert|ein plus|willkommen|hilfreich)", re.I)
GERMAN_REQUIRED = re.compile(
    rf"(?:fluent|fluency|proficient|proficiency|native|excellent|very good|strong|good|business[- ]fluent|"
    rf"full professional|professional working){_S}\bgerman\b|"
    rf"\bgerman\b{_S}(?:\bc1\b|\bc2\b|\bb2\b|fluent|native|mandatory|required|is a must|\bmust\b|essential)|"
    rf"(?:fließend|fliessend|verhandlungssicher|sehr gut|gut|ausgezeichnet|exzellent|muttersprachlich|"
    rf"sicher)\w*{_S}deutsch|"
    rf"deutsch\w*{_S}(?:\bc1\b|\bc2\b|\bb2\b|fließend|fliessend|verhandlungssicher|muttersprach|in wort und schrift)",
    re.I)


def german_requirement(text: str, jd_lang: str | None) -> str:
    """'not_needed' / 'plus' / 'required' / 'unknown'。德语写的 JD 默认需要德语。"""
    t = text or ""
    if GERMAN_NONE.search(t):
        return "not_needed"
    if GERMAN_PLUS.search(t) and not GERMAN_REQUIRED.search(GERMAN_PLUS.sub(" ", t)):
        return "plus"
    if GERMAN_REQUIRED.search(t):
        return "required"
    if jd_lang == "de":
        return "required"
    if jd_lang == "en":
        return "not_needed"   # 英文 JD 且完全没提德语
    return "unknown"


# ─────────────────────────── 工作经验 ───────────────────────────

YEARS = r"(?:years?|yrs?|jahre?n?)"
NUM = r"(\d{1,2})(?:\s*(?:\+|plus))?(?:\s*(?:-|–|to|bis)\s*(\d{1,2}))?\s*\+?"
EXP_AFTER = re.compile(rf"{NUM}\s*{YEARS}{_S}(?:experience|erfahrung|berufserfahrung|praxis)", re.I)
EXP_BEFORE = re.compile(rf"(?:experience|erfahrung|berufserfahrung){_S}?{NUM}\s*{YEARS}", re.I)
EXP_AT_LEAST = re.compile(rf"(?:at least|minimum(?: of)?|min\.?|mindestens|mind\.)\s*{NUM}\s*{YEARS}", re.I)
EXP_ZERO = re.compile(
    r"\b(?:no (?:prior |professional )?experience (?:is )?(?:required|needed|necessary)|"
    r"entry[- ]level|graduates?\b|berufseinsteiger\w*|keine berufserfahrung|"
    r"erste (?:praktische |relevante )?(?:berufs)?erfahrung|first (?:professional |practical )?experience|"
    r"absolvent\w*|quereinsteiger\w*)", re.I)
EXP_SEVERAL = re.compile(r"\b(?:mehrjährige\w*|several years|langjährige\w*|extensive experience|many years)", re.I)
WORD_NUMS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
             "ein": 1, "einem": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "sechs": 6}


def experience_years(text: str) -> int | None:
    """JD 要求的最少工作年限；没写就返回 None。"""
    t = text or ""
    for w, n in WORD_NUMS.items():
        t = re.sub(rf"\b{w}\b(?=\s*\+?\s*(?:years?|jahre?n?)\b)", str(n), t, flags=re.I)
    found = []
    for rx in (EXP_AT_LEAST, EXP_AFTER, EXP_BEFORE):
        for m in rx.finditer(t):
            n = int(m.group(1))
            if n <= 15:
                found.append(n)
    if found:
        return min(found)
    if EXP_ZERO.search(t):
        return 0
    if EXP_SEVERAL.search(t):
        return 3 if not re.search(r"langjährig|extensive|many years", t, re.I) else 5
    return None


# ─────────────────────────── 级别 ───────────────────────────

LEVEL_TITLE = [
    ("student", re.compile(r"werkstudent|working student|praktik|\bintern\b|internship|studentisch|thesis|"
                           r"abschlussarbeit|masterarbeit|bachelorarbeit", re.I)),
    ("lead", re.compile(r"\blead\b|\bhead of\b|principal|teamleit|teamlead|\bleiter|\bleitung\b|director|"
                        r"\bvp\b|chief", re.I)),
    ("senior", re.compile(r"\bsenior\b|\bsr\.?\s|\bexpert\b|\bexperte\b", re.I)),
    ("entry", re.compile(r"\bjunior\b|\bjr\.?\s|trainee|berufseinsteiger|einsteiger|graduate|entry[- ]level|"
                         r"absolvent|\bnachwuchs", re.I)),
    ("associate", re.compile(r"\bassociate\b|\bmid[- ]level\b|\bprofessional\b", re.I)),
]


def level_for(raw_title: str, exp_min: int | None) -> str:
    """student / entry / associate / senior / lead / unknown。标题优先，其次按经验年限推断。"""
    for level, rx in LEVEL_TITLE:
        if rx.search(raw_title or ""):
            return level
    if exp_min is None:
        return "unknown"
    if exp_min <= 1:
        return "entry"
    if exp_min <= 4:
        return "associate"
    return "senior"


def analyze(raw_title: str, text: str | None) -> dict:
    """返回要写进职位记录的字段。"""
    plain = strip_html(text) if text else ""
    lang = detect_lang(plain)
    lang_basis = "jd" if lang else None
    if not lang:
        lang = lang_from_title(raw_title)
        lang_basis = "title" if lang else None
    exp = experience_years(plain) if plain else None
    if exp is None and not plain and re.search(r"junior|einsteiger|graduate|trainee", raw_title or "", re.I):
        exp = 0
    return {
        "jd_lang": lang,
        "jd_lang_basis": lang_basis,
        "german": german_requirement(plain, lang) if (plain or lang) else "unknown",
        "exp_min": exp,
        "level": level_for(raw_title, exp),
        "has_jd": bool(plain),
    }
