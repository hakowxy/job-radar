"""Job Radar：每天从德国的公开职位接口抓取职位，去重、分类，写入 data/。

只用 Python 标准库，不需要 pip install。

来源（都是官方开放接口，不爬网页）：
  - Bundesagentur für Arbeit Jobbörse   免费，无需注册
  - Arbeitnow                           免费，无需注册，偏英文/国际化岗位
  - Adzuna                              免费注册；设置 ADZUNA_APP_ID / ADZUNA_APP_KEY 后才启用

输出：
  data/jobs.json   当前在招的职位（含历史：首次出现日期、最后出现日期）
  data/meta.json   本次运行信息：每个来源的状态、岗位类型列表
  data/digest.md   今天新增职位的摘要（用于每日 Issue / 邮件）
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CFG = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
SEARCH = CFG["search"]
CATEGORIES = CFG["category"]
TODAY = datetime.now(timezone.utc).date()
UA = "job-radar/1.0 (personal job search tool)"


# ─────────────────────────── HTTP ───────────────────────────

def http_json(url: str, params: dict | None = None, headers: dict | None = None, retries: int = 3):
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json", **(headers or {})})
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code < 500 and e.code != 429:   # 4xx（除限流外）重试也没用
                raise
            last = e
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
        time.sleep(2 ** (attempt + 1))
    raise last


# ─────────────────────────── 文本处理 ───────────────────────────

GENDER = re.compile(r"\(?\b(?:[mwfdx]|all|alle)\s*/\s*[mwfdx](?:\s*/\s*[mwfdx])?\)?|\((?:all genders?|alle geschlechter|gn\*?)\)", re.I)
LEGAL = re.compile(r"\b(gmbh|mbh|ag|se|kg|kgaa|ohg|ug|e\.?\s?v|co|inc|ltd|llc|bv|holding|group|gruppe)\b\.?", re.I)
WORD = "a-z0-9äöüß"


def clean_title(title: str) -> str:
    t = GENDER.sub("", title or "")
    return re.sub(r"\s{2,}", " ", t).strip(" -–|,")


def norm(s: str) -> str:
    return re.sub(rf"[^{WORD}]+", " ", (s or "").lower()).strip()


def keyword_regex(keywords: list[str]) -> re.Pattern:
    """'bi' 只匹配整词；'reporting*' 匹配以 reporting 开头的词。"""
    parts = []
    for k in keywords:
        k = norm(k.rstrip("*")) + ("*" if k.endswith("*") else "")
        body = re.escape(k.rstrip("*")).replace(r"\ ", r"\s+")
        tail = "" if k.endswith("*") else rf"(?![{WORD}])"
        parts.append(rf"(?<![{WORD}]){body}{tail}")
    return re.compile("|".join(parts), re.I)


CAT_RE = {c["id"]: keyword_regex(c["match"]) for c in CATEGORIES}
EXCLUDE_RE = keyword_regex([k + "*" for k in SEARCH.get("exclude", [])]) if SEARCH.get("exclude") else None
LEVELS = [
    ("student", keyword_regex(["werkstudent*", "working student", "praktik*", "intern", "internship", "studentische*", "thesis", "abschlussarbeit*"])),
    ("junior", keyword_regex(["junior", "trainee", "berufseinsteiger*", "einsteiger*", "graduate", "entry level", "absolvent*"])),
    ("senior", keyword_regex(["senior", "lead", "principal", "head of", "teamleit*", "leiter*", "leitung", "manager"])),
]
REMOTE_RE = keyword_regex(["remote", "homeoffice", "home office", "mobiles arbeiten", "hybrid"])


def classify(job: dict) -> dict | None:
    title = norm(job["title"])
    if EXCLUDE_RE and EXCLUDE_RE.search(title):
        return None
    cats = [cid for cid, rx in CAT_RE.items() if rx.search(title)]
    if not cats:
        return None
    job["categories"] = cats
    job["level"] = next((lvl for lvl, rx in LEVELS if rx.search(title)), "regular")
    if job.get("remote") is None:
        job["remote"] = bool(REMOTE_RE.search(title))
    return job


def dedupe_key(job: dict) -> str:
    company = LEGAL.sub(" ", norm(job["company"]))
    city = norm(job["city"]).split(" ")[0] if job["city"] else ""
    raw = "|".join([re.sub(r"\s+", " ", company).strip(), norm(job["title"]), city])
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


# Arbeitnow 只给城市名，用这张表补上联邦州，方便按地区筛选
CITY_REGION = {
    "berlin": "Berlin", "hamburg": "Hamburg", "bremen": "Bremen", "münchen": "Bayern", "munich": "Bayern",
    "nürnberg": "Bayern", "nuremberg": "Bayern", "augsburg": "Bayern", "regensburg": "Bayern", "würzburg": "Bayern",
    "ingolstadt": "Bayern", "erlangen": "Bayern", "stuttgart": "Baden-Württemberg", "karlsruhe": "Baden-Württemberg",
    "mannheim": "Baden-Württemberg", "freiburg": "Baden-Württemberg", "heidelberg": "Baden-Württemberg",
    "ulm": "Baden-Württemberg", "frankfurt": "Hessen", "wiesbaden": "Hessen", "darmstadt": "Hessen", "kassel": "Hessen",
    "köln": "Nordrhein-Westfalen", "cologne": "Nordrhein-Westfalen", "düsseldorf": "Nordrhein-Westfalen",
    "dortmund": "Nordrhein-Westfalen", "essen": "Nordrhein-Westfalen", "duisburg": "Nordrhein-Westfalen",
    "bochum": "Nordrhein-Westfalen", "bonn": "Nordrhein-Westfalen", "münster": "Nordrhein-Westfalen",
    "aachen": "Nordrhein-Westfalen", "bielefeld": "Nordrhein-Westfalen", "hannover": "Niedersachsen",
    "braunschweig": "Niedersachsen", "osnabrück": "Niedersachsen", "oldenburg": "Niedersachsen", "göttingen": "Niedersachsen",
    "wolfsburg": "Niedersachsen", "leipzig": "Sachsen", "dresden": "Sachsen", "chemnitz": "Sachsen",
    "mainz": "Rheinland-Pfalz", "koblenz": "Rheinland-Pfalz", "saarbrücken": "Saarland", "kiel": "Schleswig-Holstein",
    "lübeck": "Schleswig-Holstein", "potsdam": "Brandenburg", "rostock": "Mecklenburg-Vorpommern",
    "magdeburg": "Sachsen-Anhalt", "halle": "Sachsen-Anhalt", "erfurt": "Thüringen", "jena": "Thüringen",
}


def make_job(source, title, company, city, region, posted, url, remote=None, extra=None) -> dict:
    return {
        "title": clean_title(title),
        "company": (company or "").strip() or "—",
        "city": (city or "").strip(),
        "region": (region or "").strip() or CITY_REGION.get(norm(city).split(" ")[0] if city else "", ""),
        "posted": posted,
        "remote": remote,
        "sources": [source],
        "links": {source: url},
        **(extra or {}),
    }


# ─────────────────────────── 来源 1：联邦劳动局 ───────────────────────────

BA_BASE = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service"
BA_PATHS = ["/pc/v4/jobs", "/pc/v6/jobs", "/pc/v4/app/jobs"]   # 接口版本会变，依次尝试
_ba_path = None


def ba_get(params: dict) -> dict:
    global _ba_path
    paths = [_ba_path] if _ba_path else BA_PATHS
    errors = []
    for path in paths:
        try:
            data = http_json(BA_BASE + path, params, headers={"X-API-Key": "jobboerse-jobsuche"})
            _ba_path = path
            return data
        except urllib.error.HTTPError as e:
            errors.append(f"{path}: HTTP {e.code}")
    raise RuntimeError("; ".join(errors))


def fetch_ba(query: str) -> list[dict]:
    params = {"was": query, "angebotsart": 1, "veroeffentlichtseit": SEARCH["days"], "size": 100}
    if SEARCH.get("where"):
        params["wo"] = SEARCH["where"]
        if SEARCH.get("radius_km"):
            params["umkreis"] = SEARCH["radius_km"]
    if SEARCH.get("exclude_agencies"):
        params |= {"zeitarbeit": "false", "pav": "false"}

    jobs = []
    for page in range(1, SEARCH["max_pages"] + 1):
        data = ba_get(params | {"page": page})
        items = data.get("stellenangebote") or []
        for it in items:
            refnr = it.get("refnr") or it.get("referenznummer")
            if not refnr:
                continue
            ort = it.get("arbeitsort") or {}
            jobs.append(make_job(
                "arbeitsagentur",
                it.get("titel") or it.get("stellenangebotsTitel") or it.get("beruf") or "",
                it.get("arbeitgeber"),
                ort.get("ort"),
                ort.get("region"),
                (it.get("aktuelleVeroeffentlichungsdatum") or "")[:10] or None,
                f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{urllib.parse.quote(refnr)}",
            ))
        if len(items) < params["size"]:
            break
        time.sleep(0.5)
    return jobs


# ─────────────────────────── 来源 2：Arbeitnow ───────────────────────────

def fetch_arbeitnow() -> list[dict]:
    """Arbeitnow 不支持关键词搜索：按时间倒序翻页，旧于 days 天就停，再在本地分类过滤。"""
    cutoff = TODAY - timedelta(days=SEARCH["days"])
    jobs = []
    for page in range(1, SEARCH["max_pages"] * 4 + 1):
        data = http_json("https://www.arbeitnow.com/api/job-board-api", {"page": page})
        items = data.get("data") or []
        too_old = False
        for it in items:
            created = it.get("created_at")
            posted = datetime.fromtimestamp(created, timezone.utc).date() if isinstance(created, (int, float)) else None
            if posted and posted < cutoff:
                too_old = True
                continue
            jobs.append(make_job(
                "arbeitnow", it.get("title"), it.get("company_name"), it.get("location"), "",
                posted.isoformat() if posted else None, it.get("url"),
                remote=True if it.get("remote") else None,
            ))
        if not items or too_old or not (data.get("links") or {}).get("next", True):
            break
        time.sleep(0.5)
    return jobs


# ─────────────────────────── 来源 3：Adzuna（可选） ───────────────────────────

def fetch_adzuna(query: str) -> list[dict]:
    app_id, app_key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    jobs = []
    for page in range(1, SEARCH["max_pages"] + 1):
        params = {"app_id": app_id, "app_key": app_key, "what": query, "results_per_page": 50,
                  "max_days_old": SEARCH["days"], "sort_by": "date", "content-type": "application/json"}
        if SEARCH.get("where"):
            params["where"] = SEARCH["where"]
        data = http_json(f"https://api.adzuna.com/v1/api/jobs/de/search/{page}", params)
        items = data.get("results") or []
        for it in items:
            loc = it.get("location") or {}
            area = loc.get("area") or []
            jobs.append(make_job(
                "adzuna", it.get("title"), (it.get("company") or {}).get("display_name"),
                area[-1] if len(area) > 1 else loc.get("display_name"),
                area[1] if len(area) > 1 else "",
                (it.get("created") or "")[:10] or None, it.get("redirect_url"),
                extra={"salary": [it.get("salary_min"), it.get("salary_max")]} if it.get("salary_min") else None,
            ))
        if len(items) < 50:
            break
        time.sleep(1)
    return jobs


# ─────────────────────────── 主流程 ───────────────────────────

def run_sources() -> tuple[list[dict], list[dict]]:
    queries = sorted({q for c in CATEGORIES for q in c["queries"]})
    sources = [("arbeitsagentur", "联邦劳动局 Jobbörse", lambda: [j for q in queries for j in fetch_ba(q)]),
               ("arbeitnow", "Arbeitnow", fetch_arbeitnow)]
    if os.environ.get("ADZUNA_APP_ID") and os.environ.get("ADZUNA_APP_KEY"):
        sources.append(("adzuna", "Adzuna", lambda: [j for q in queries for j in fetch_adzuna(q)]))

    jobs, status = [], []
    for sid, label, fn in sources:
        t0 = time.time()
        try:
            got = fn()
            kept = [j for j in (classify(j) for j in got) if j]
            jobs.extend(kept)
            status.append({"id": sid, "label": label, "ok": True, "fetched": len(got), "kept": len(kept),
                           "seconds": round(time.time() - t0, 1),
                           "note": f"endpoint {_ba_path}" if sid == "arbeitsagentur" else ""})
        except Exception as e:  # 一个来源挂了不影响其他来源
            status.append({"id": sid, "label": label, "ok": False, "fetched": 0, "kept": 0,
                           "seconds": round(time.time() - t0, 1), "note": str(e)[:300]})
        print(f"  {'✓' if status[-1]['ok'] else '✗'} {label}: {status[-1]['fetched']} 抓到 → {status[-1]['kept']} 保留  {status[-1]['note']}")
    if not os.environ.get("ADZUNA_APP_ID"):
        status.append({"id": "adzuna", "label": "Adzuna", "ok": None, "fetched": 0, "kept": 0, "seconds": 0,
                       "note": "未启用：在 GitHub Secrets 里设置 ADZUNA_APP_ID / ADZUNA_APP_KEY"})
    return jobs, status


def merge(store: dict, fetched: list[dict]) -> tuple[dict, list[str]]:
    today = TODAY.isoformat()
    new_ids, seen_now = [], {}
    for job in fetched:
        jid = dedupe_key(job)
        if jid in seen_now:   # 同一次运行里不同来源/关键词抓到同一个职位
            cur = seen_now[jid]
            for s in job["sources"]:
                if s not in cur["sources"]:
                    cur["sources"].append(s)
                    cur["links"][s] = job["links"][s]
            cur["categories"] = sorted(set(cur["categories"]) | set(job["categories"]))
            cur["remote"] = cur["remote"] or job["remote"]
            if not cur["region"] and job["region"]:
                cur["region"] = job["region"]
            continue
        job["id"] = jid
        seen_now[jid] = job

    for jid, job in seen_now.items():
        if jid in store:
            old = store[jid]
            job["first_seen"] = old["first_seen"]
            job["posted"] = job["posted"] or old.get("posted")
        else:
            job["first_seen"] = today
            new_ids.append(jid)
        job["last_seen"] = today
        store[jid] = job

    cutoff = (TODAY - timedelta(days=SEARCH.get("keep_days", 3))).isoformat()
    store = {k: v for k, v in store.items() if v["last_seen"] >= cutoff}
    return store, new_ids


def write_digest(store: dict, new_ids: list[str]) -> None:
    path = DATA / "digest.md"
    notify = CFG.get("notify", {})
    if not notify.get("daily_issue"):
        path.unlink(missing_ok=True)
        return
    wanted = set(notify.get("categories") or [c["id"] for c in CATEGORIES])
    labels = {c["id"]: c["label"] for c in CATEGORIES}
    order = [c["id"] for c in CATEGORIES if c["id"] in wanted]
    picked = []
    for i in new_ids:
        job = store[i]
        cat = next((c for c in order if c in job["categories"]), None)
        if cat:
            picked.append((cat, job))
    if not picked:
        path.unlink(missing_ok=True)
        return
    limit = notify.get("max_items", 40)
    lines = [f"今天新增 **{len(picked)}** 个符合条件的职位。\n"]
    shown = 0
    for cid in order:
        group = [j for c, j in picked if c == cid]
        if not group or shown >= limit:
            continue
        lines.append(f"\n### {labels[cid]}（{len(group)}）\n")
        for j in group[: limit - shown]:
            link = next(iter(j["links"].values()))
            place = j["city"] + ("，远程/混合" if j["remote"] else "")
            lines.append(f"- [{j['title']}]({link}) — {j['company']}，{place}")
            shown += 1
    if len(picked) > shown:
        lines.append(f"\n还有 {len(picked) - shown} 个没有列出，打开网页查看全部。")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    DATA.mkdir(exist_ok=True)
    jobs_path = DATA / "jobs.json"
    store = {j["id"]: j for j in json.loads(jobs_path.read_text(encoding="utf-8"))} if jobs_path.exists() else {}

    print(f"Job Radar {TODAY}")
    fetched, status = run_sources()
    if not any(s["ok"] for s in status):
        sys.exit("所有来源都失败了，保留旧数据，不更新。")

    store, new_ids = merge(store, fetched)
    jobs = sorted(store.values(), key=lambda j: (j["first_seen"], j["posted"] or ""), reverse=True)
    jobs_path.write_text(json.dumps(jobs, ensure_ascii=False, indent=0), encoding="utf-8")

    meta = {
        "run_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "today": TODAY.isoformat(),
        "new_today": len(new_ids),
        "total": len(jobs),
        "sources": status,
        "categories": [{"id": c["id"], "label": c["label"]} for c in CATEGORIES],
        "search": {"days": SEARCH["days"], "where": SEARCH.get("where") or "Deutschland"},
    }
    (DATA / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    write_digest(store, new_ids)
    print(f"  共 {len(jobs)} 个在招职位，今天新增 {len(new_ids)} 个")


if __name__ == "__main__":
    main()
