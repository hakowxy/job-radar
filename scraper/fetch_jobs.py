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
IN_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"


def annotate(level: str, msg: str) -> None:
    """在 GitHub Actions 里显示为运行页面上的提示（notice / warning / error）。"""
    if IN_ACTIONS:
        print(f"::{level}::" + msg.replace("%", "%25").replace("\r", "").replace("\n", "%0A"))


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
            if e.code == 429:   # 被限流：按服务器要求的时间等待，至少 20 秒
                wait = e.headers.get("Retry-After") if e.headers else None
                time.sleep(min(int(wait) if wait and wait.isdigit() else 20 * (attempt + 1), 120))
                continue
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


GERMANY = {"germany", "deutschland"}
EXTRA_CITIES = {
    "bavaria": "Bayern", "bayern": "Bayern", "hesse": "Hessen", "hessen": "Hessen", "saxony": "Sachsen",
    "garching": "Bayern", "unterföhring": "Bayern", "walldorf": "Baden-Württemberg", "eschborn": "Hessen",
    "offenbach": "Hessen", "neuss": "Nordrhein-Westfalen", "gütersloh": "Nordrhein-Westfalen",
    "paderborn": "Nordrhein-Westfalen", "wuppertal": "Nordrhein-Westfalen", "krefeld": "Nordrhein-Westfalen",
    "gelsenkirchen": "Nordrhein-Westfalen", "mönchengladbach": "Nordrhein-Westfalen", "leverkusen": "Nordrhein-Westfalen",
    "siegen": "Nordrhein-Westfalen", "heilbronn": "Baden-Württemberg", "pforzheim": "Baden-Württemberg",
    "tübingen": "Baden-Württemberg", "konstanz": "Baden-Württemberg", "reutlingen": "Baden-Württemberg",
    "ludwigshafen": "Rheinland-Pfalz", "kaiserslautern": "Rheinland-Pfalz", "trier": "Rheinland-Pfalz",
    "bamberg": "Bayern", "bayreuth": "Bayern", "passau": "Bayern", "rosenheim": "Bayern", "fürth": "Bayern",
    "landshut": "Bayern", "flensburg": "Schleswig-Holstein", "schwerin": "Mecklenburg-Vorpommern",
    "greifswald": "Mecklenburg-Vorpommern", "cottbus": "Brandenburg", "weimar": "Thüringen", "zwickau": "Sachsen",
    "hildesheim": "Niedersachsen", "lüneburg": "Niedersachsen", "salzgitter": "Niedersachsen", "wilhelmshaven": "Niedersachsen",
}
CITY_REGION.update(EXTRA_CITIES)


def region_for(location: str) -> str | None:
    """返回联邦州；看起来在德国但认不出城市时返回 ""；不在德国时返回 None。"""
    tokens = norm(location).split()
    for t in tokens:
        if t in CITY_REGION:
            return CITY_REGION[t]
    return "" if GERMANY & set(tokens) else None


def make_job(source, title, company, city, region, posted, url, remote=None, extra=None) -> dict:
    return {
        "title": clean_title(title),
        "company": (company or "").strip() or "—",
        "city": (city or "").strip(),
        "region": (region or "").strip() or (region_for(city or "") or ""),
        "posted": posted,
        "remote": remote,
        "sources": [source],
        "links": {source: url},
        **(extra or {}),
    }


def pick(d: dict, *keys):
    """接口版本之间字段名会变：按顺序取第一个有值的字段。"""
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


# ─────────────────────────── 来源 1：联邦劳动局 ───────────────────────────

BA_BASE = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service"
BA_PATHS = ["/pc/v6/jobs", "/pc/v4/app/jobs", "/pc/v4/jobs"]   # 接口版本会变，依次尝试
_ba_path = None
_ba_logged = False


def ba_items(data) -> list | None:
    """在返回的 JSON 里找到职位列表；格式认不出来时返回 None。"""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return None
    for k in ("stellenangebote", "ergebnisliste", "ergebnisse", "stellen", "jobs", "content", "results"):
        if isinstance(data.get(k), list):
            return data[k]
    for v in data.values():   # 嵌套一层，例如 {"_embedded": {"jobs": [...]}}
        if isinstance(v, dict):
            for v2 in v.values():
                if isinstance(v2, list) and (not v2 or isinstance(v2[0], dict)):
                    return v2
    return None


def ba_get(params: dict) -> list:
    global _ba_path, _ba_logged
    paths = [_ba_path] if _ba_path else BA_PATHS
    errors = []
    for path in paths:
        try:
            data = http_json(BA_BASE + path, params, headers={"X-API-Key": "jobboerse-jobsuche"})
        except urllib.error.HTTPError as e:
            errors.append(f"{path}: HTTP {e.code}")
            continue
        items = ba_items(data)
        if not _ba_logged:   # 把真实格式打印到 Actions 日志里，方便排查
            keys = list(data)[:15] if isinstance(data, dict) else type(data).__name__
            sample = sorted(items[0]) if items else None
            line = f"[BA {path}] top-level keys: {keys}; items: {None if items is None else len(items)}; item keys: {sample}"
            if items:
                line += f"; first item: {json.dumps(items[0], ensure_ascii=False)[:2500]}"
            elif items is None or not items:
                line += f"; raw: {json.dumps(data, ensure_ascii=False)[:500]}"
            print("    " + line)
            annotate("notice", line)
        if items is None:
            errors.append(f"{path}: 返回格式无法识别")
            continue
        if not items and not _ba_path and path != paths[-1]:
            # 第一次请求就是空的：可能只是这个版本不对，再试下一个
            errors.append(f"{path}: 0 条")
            continue
        _ba_logged = True
        _ba_path = path
        return items
    if errors and all(e.endswith("0 条") for e in errors):
        return []
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
        items = ba_get(params | {"page": page})
        for it in items:
            refnr = pick(it, "refnr", "referenznummer", "refNr")
            employer = pick(it, "arbeitgeber", "arbeitgeberName", "firma")
            if isinstance(employer, dict):
                employer = pick(employer, "name", "anzeigeName")
            ort = pick(it, "arbeitsort", "arbeitsorte", "ort") or {}
            if isinstance(ort, list):
                ort = ort[0] if ort else {}
            if isinstance(ort, str):
                ort = {"ort": ort}
            url = (f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{urllib.parse.quote(str(refnr))}"
                   if refnr else pick(it, "externeUrl"))
            if not url:
                continue
            jobs.append(make_job(
                "arbeitsagentur",
                pick(it, "titel", "stellenangebotsTitel", "stellentitel", "titelDerStelle", "beruf") or "",
                employer,
                pick(ort, "ort", "stadt"),
                pick(ort, "region", "bundesland"),
                str(pick(it, "aktuelleVeroeffentlichungsdatum", "veroeffentlichungsdatum",
                         "datumErsteVeroeffentlichung", "eintrittsdatum") or "")[:10] or None,
                url,
            ))
        if len(items) < params["size"]:
            break
        time.sleep(0.5)
    return jobs


# ─────────────────────────── 来源 2：Arbeitnow ───────────────────────────

def fetch_arbeitnow() -> list[dict]:
    """Arbeitnow 不支持关键词搜索：按时间倒序翻页，旧于 days 天就停，再在本地分类过滤。
    它也有德国以外的职位，所以只保留能认出是德国城市（或写了 Germany）的。"""
    cutoff = TODAY - timedelta(days=SEARCH["days"])
    jobs = []
    for page in range(1, SEARCH["max_pages"] * 2 + 1):
        data = http_json("https://www.arbeitnow.com/api/job-board-api", {"page": page})
        items = data.get("data") or []
        too_old = 0
        for it in items:
            created = it.get("created_at")
            posted = datetime.fromtimestamp(created, timezone.utc).date() if isinstance(created, (int, float)) else None
            if posted and posted < cutoff:
                too_old += 1
                continue
            region = region_for(it.get("location") or "")
            if region is None:
                continue
            jobs.append(make_job(
                "arbeitnow", it.get("title"), it.get("company_name"), it.get("location"), region,
                posted.isoformat() if posted else None, it.get("url"),
                remote=True if it.get("remote") else None,
            ))
        if not items or too_old > len(items) / 2 or not (data.get("links") or {}).get("next", True):
            break
        time.sleep(2)   # Arbeitnow 限流比较严格
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
        line = f"{'✓' if status[-1]['ok'] else '✗'} {label}: {status[-1]['fetched']} 抓到 → {status[-1]['kept']} 保留  {status[-1]['note']}"
        print("  " + line)
        annotate("notice" if status[-1]["ok"] else "warning", line)
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
        annotate("error", "所有来源都失败了，保留旧数据，不更新。")
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
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        tb = traceback.extract_tb(e.__traceback__)[-1]
        annotate("error", f"{type(e).__name__}: {e} (line {tb.lineno}: {tb.line})")
        raise
