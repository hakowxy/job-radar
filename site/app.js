/* Job Radar page: filters run in the browser over data/jobs.json */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const store = {
    get(k, fallback) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : fallback; } catch (e) { return fallback; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* storage unavailable */ } },
  };

  const LEVELS = [["student", "实习 / Werkstudent"], ["entry", "Entry level"], ["associate", "Associate"], ["senior", "Senior"], ["lead", "Lead / 管理"], ["unknown", "未标明"]];
  const LEVEL_NAMES = Object.fromEntries(LEVELS);
  const LANGS = [["any", "都可以"], ["en", "英文 JD"], ["de", "德文 JD"]];
  const GERMAN_TAG = { required: ["需要德语", "warn"], plus: ["德语加分", ""], not_needed: ["不需德语", "en"] };
  const SOURCE_NAMES = { arbeitsagentur: "劳动局", arbeitnow: "Arbeitnow", adzuna: "Adzuna" };
  const TABS = [["new", "新职位"], ["all", "全部"], ["saved", "收藏"], ["applied", "已投递"], ["hidden", "已隐藏"]];
  const PAGE = 50;

  let JOBS = [], META = null, shown = PAGE;
  const DEFAULTS = { cats: [], levels: [], lang: "any", german: "any", exp: "any", expUnknown: true, region: "", posted: "0", remote: false, q: "", tab: "new" };
  const prefs = Object.assign({}, DEFAULTS, store.get("jr.prefs", {}));
  delete prefs.level;   // 旧版本的单选级别
  const marks = store.get("jr.marks", {});   // id -> "saved" | "applied" | "hidden"
  let prevDay = "";                          // jobs first seen after this day count as new

  function rememberVisit(today) {
    let prev = null;
    try { prev = sessionStorage.getItem("jr.prevDay"); } catch (e) { /* ignore */ }
    if (prev === null) {
      prev = store.get("jr.lastDay", "");
      try { sessionStorage.setItem("jr.prevDay", prev); } catch (e) { /* ignore */ }
    }
    store.set("jr.lastDay", today);
    // first visit ever: treat today's jobs as new
    prevDay = prev && prev < today ? prev : prev === today ? prev : dayBefore(today);
  }
  const dayBefore = (d) => { const t = new Date(d + "T00:00:00Z"); t.setUTCDate(t.getUTCDate() - 1); return t.toISOString().slice(0, 10); };
  const isNew = (j) => j.first_seen > prevDay;
  function daysAgo(d) {
    if (!d) return null;
    return Math.round((Date.parse(META.today + "T00:00:00Z") - Date.parse(d + "T00:00:00Z")) / 864e5);
  }
  const ageText = (n) => (n === null ? "" : n <= 0 ? "今天发布" : `${n} 天前发布`);

  /* ---------- filtering ---------- */
  function passes(j, { ignoreCats = false, ignoreTab = false, ignoreLevels = false } = {}) {
    const mark = marks[j.id];
    if (!ignoreTab) {
      if (prefs.tab === "hidden") { if (mark !== "hidden") return false; }
      else if (mark === "hidden") return false;
      if (prefs.tab === "saved" && mark !== "saved") return false;
      if (prefs.tab === "applied" && mark !== "applied") return false;
      if (prefs.tab === "new" && !isNew(j)) return false;
    } else if (mark === "hidden") return false;
    if (!ignoreCats && prefs.cats.length && !j.categories.some((c) => prefs.cats.includes(c))) return false;
    if (!ignoreLevels && prefs.levels.length && !prefs.levels.includes(j.level || "unknown")) return false;
    if (prefs.lang !== "any" && j.jd_lang !== prefs.lang) return false;
    if (prefs.german === "none" && j.german !== "not_needed") return false;
    if (prefs.german === "plus" && !["not_needed", "plus"].includes(j.german)) return false;
    if (prefs.exp !== "any") {
      if (j.exp_min === null || j.exp_min === undefined) { if (!prefs.expUnknown) return false; }
      else if (j.exp_min > +prefs.exp) return false;
    }
    if (prefs.region && j.region !== prefs.region) return false;
    if (prefs.remote && !j.remote) return false;
    if (+prefs.posted) {
      const n = daysAgo(j.posted || j.first_seen);
      if (n === null || n >= +prefs.posted) return false;
    }
    if (prefs.q) {
      const hay = `${j.title} ${j.company} ${j.city} ${j.region}`.toLowerCase();
      if (!prefs.q.toLowerCase().split(/\s+/).every((w) => hay.includes(w))) return false;
    }
    return true;
  }

  /* ---------- rendering ---------- */
  function renderCats() {
    $("cats").innerHTML = META.categories.map((c) => {
      const n = JOBS.filter((j) => j.categories.includes(c.id) && passes(j, { ignoreCats: true })).length;
      return `<button type="button" class="cat" data-cat="${esc(c.id)}" aria-pressed="${prefs.cats.includes(c.id)}">${esc(c.label)}<span class="n">${n}</span></button>`;
    }).join("");
  }

  function renderLevels() {
    $("levels").innerHTML = LEVELS.map(([k, v]) => {
      const n = JOBS.filter((j) => (j.level || "unknown") === k && passes(j, { ignoreLevels: true })).length;
      return `<button type="button" data-level="${k}" aria-pressed="${prefs.levels.includes(k)}">${v}<span class="n">${n}</span></button>`;
    }).join("");
    $("lang").innerHTML = LANGS.map(([k, v]) => `<button type="button" data-lang="${k}" aria-pressed="${prefs.lang === k}">${v}</button>`).join("");
    $("exp-unknown-wrap").hidden = prefs.exp === "any";
  }

  function renderRegions() {
    const regions = [...new Set(JOBS.map((j) => j.region).filter(Boolean))].sort((a, b) => a.localeCompare(b, "de"));
    if (prefs.region && !regions.includes(prefs.region)) prefs.region = "";
    $("region").innerHTML = `<option value="">全德国</option>` + regions.map((r) => `<option${r === prefs.region ? " selected" : ""}>${esc(r)}</option>`).join("");
  }

  function renderTabs() {
    $("tabs").innerHTML = TABS.map(([k, v]) => {
      const n = JOBS.filter((j) => {
        const m = marks[j.id];
        if (k === "hidden") return m === "hidden";
        if (m === "hidden") return false;
        if (k === "saved") return m === "saved";
        if (k === "applied") return m === "applied";
        if (k === "new") return isNew(j);
        return true;
      }).length;
      return `<button type="button" role="tab" data-tab="${k}" aria-selected="${prefs.tab === k}">${v}<span class="count">${n}</span></button>`;
    }).join("");
  }

  function jobRow(j) {
    const mark = marks[j.id];
    const tags = [];
    if (isNew(j)) tags.push(`<span class="tag new">新</span>`);
    if (j.remote) tags.push(`<span class="tag remote">远程 / 混合</span>`);
    if (j.jd_lang) tags.push(`<span class="tag${j.jd_lang === "en" ? " en" : ""}" title="${j.jd_lang_basis === "title" ? "根据职位名判断" : "根据 JD 正文判断"}">${j.jd_lang === "en" ? "英文 JD" : "德文 JD"}</span>`);
    if (GERMAN_TAG[j.german]) tags.push(`<span class="tag ${GERMAN_TAG[j.german][1]}">${GERMAN_TAG[j.german][0]}</span>`);
    if (j.exp_min === 0) tags.push(`<span class="tag">无需经验</span>`);
    else if (j.exp_min) tags.push(`<span class="tag">经验 ${j.exp_min}+ 年</span>`);
    if (j.level && j.level !== "unknown") tags.push(`<span class="tag lvl">${esc(LEVEL_NAMES[j.level] || j.level)}</span>`);
    const catLabels = Object.fromEntries(META.categories.map((c) => [c.id, c.label]));
    j.categories.forEach((c) => tags.push(`<span class="tag">${esc(catLabels[c] || c)}</span>`));
    if (j.salary && j.salary[0]) tags.push(`<span class="tag">€${Math.round(j.salary[0] / 1000)}k–${Math.round((j.salary[1] || j.salary[0]) / 1000)}k</span>`);
    Object.entries(j.links).forEach(([s, url]) => tags.push(`<span class="tag"><a href="${esc(url)}" target="_blank" rel="noopener">${esc(SOURCE_NAMES[s] || s)}</a></span>`));
    const mainLink = j.links.arbeitsagentur || Object.values(j.links)[0];
    const place = [j.city, j.region && j.region !== j.city ? j.region : ""].filter(Boolean).join("，");
    const btn = (act, label, on) => `<button type="button" data-act="${act}" data-id="${esc(j.id)}" aria-pressed="${on}">${label}</button>`;
    return `<li class="job${isNew(j) ? " is-new" : ""}${mark ? " is-" + mark : ""}">
      <h3><a href="${esc(mainLink)}" target="_blank" rel="noopener">${esc(j.title)}</a></h3>
      <div class="where">${esc(j.company)}${place ? "，" + esc(place) : ""}</div>
      <div class="tags">${tags.join("")}</div>
      <div class="side">
        <span class="age">${ageText(daysAgo(j.posted || j.first_seen))}</span>
        <span class="acts">${btn("saved", mark === "saved" ? "已收藏" : "收藏", mark === "saved")}${btn("applied", mark === "applied" ? "已投递" : "投递了", mark === "applied")}${btn("hidden", mark === "hidden" ? "取消隐藏" : "隐藏", mark === "hidden")}</span>
      </div>
    </li>`;
  }

  function renderList() {
    const rows = JOBS.filter((j) => passes(j));
    const visible = rows.slice(0, shown);
    let html = "", markerDone = prefs.tab !== "all";
    visible.forEach((j) => {
      if (!markerDone && !isNew(j)) {
        if (html) html += `<li class="marker" aria-hidden="true">以下是你上次已经看过的</li>`;
        markerDone = true;
      }
      html += jobRow(j);
    });
    $("list").innerHTML = html;
    $("more").hidden = rows.length <= shown;
    $("more").textContent = `显示更多（还有 ${rows.length - shown} 个）`;

    const hasFilter = prefs.cats.length || prefs.levels.length || prefs.lang !== "any" || prefs.german !== "any" || prefs.exp !== "any" ||
      prefs.region || prefs.remote || +prefs.posted || prefs.q;
    let msg = `${rows.length} 个职位`;
    if (!rows.length) {
      msg = prefs.tab === "new" ? "自上次查看以来没有符合条件的新职位。" :
        prefs.tab === "saved" ? "还没有收藏。点职位右侧的“收藏”就会出现在这里。" :
        prefs.tab === "applied" ? "还没有标记投递过的职位。" :
        prefs.tab === "hidden" ? "没有隐藏的职位。" : "没有符合条件的职位。";
    }
    $("summary").innerHTML = esc(msg) + (hasFilter ? `　<button type="button" id="reset">清除筛选</button>` : "");
    if (!rows.length) $("list").innerHTML = "";
  }

  function renderHeader() {
    const run = new Date(META.run_at);
    const when = run.toLocaleString("zh-CN", { timeZone: "Europe/Berlin", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
    const failed = META.sources.filter((s) => s.ok === false);
    $("run-line").innerHTML = `更新于 ${esc(when)}（德国时间），范围：${esc(META.search.where)}，最近 ${META.search.days} 天发布` +
      (failed.length ? `，<span class="warn">${failed.length} 个来源今天失败</span>` : "");
    const nNew = JOBS.filter((j) => isNew(j) && marks[j.id] !== "hidden").length;
    $("today-line").innerHTML = `<b>${nNew}</b> 个新职位，自你上次查看`;

    $("sources").innerHTML = META.sources.map((s) => {
      const state = s.ok === true ? `<span class="ok">正常</span>` : s.ok === false ? `<span class="err">失败</span>` : "未启用";
      return `<tr><td>${esc(s.label)}</td><td>${state}</td><td>${s.ok ? `抓到 ${s.fetched}，保留 ${s.kept}` : ""}</td><td>${esc(s.note)}</td></tr>`;
    }).join("");
  }

  function renderAll() {
    renderHeader();
    renderCats();
    renderLevels();
    renderTabs();
    renderList();
  }
  function update() {
    shown = PAGE;
    store.set("jr.prefs", prefs);
    renderCats();
    renderLevels();
    renderTabs();
    renderList();
  }

  /* ---------- events ---------- */
  $("cats").addEventListener("click", (e) => {
    const b = e.target.closest("[data-cat]"); if (!b) return;
    const id = b.dataset.cat;
    prefs.cats = prefs.cats.includes(id) ? prefs.cats.filter((c) => c !== id) : [...prefs.cats, id];
    update();
  });
  $("levels").addEventListener("click", (e) => {
    const b = e.target.closest("[data-level]"); if (!b) return;
    const k = b.dataset.level;
    prefs.levels = prefs.levels.includes(k) ? prefs.levels.filter((x) => x !== k) : [...prefs.levels, k];
    update();
  });
  $("lang").addEventListener("click", (e) => {
    const b = e.target.closest("[data-lang]"); if (!b) return;
    prefs.lang = b.dataset.lang; update();
  });
  $("german").addEventListener("change", (e) => { prefs.german = e.target.value; update(); });
  $("exp").addEventListener("change", (e) => { prefs.exp = e.target.value; update(); });
  $("exp-unknown").addEventListener("change", (e) => { prefs.expUnknown = e.target.checked; update(); });
  document.querySelector(".presets").addEventListener("click", (e) => {
    const b = e.target.closest("[data-preset]"); if (!b) return;
    if (b.dataset.preset === "english") Object.assign(prefs, { lang: "en", german: "plus" });
    if (b.dataset.preset === "both") Object.assign(prefs, { lang: "any", german: "any" });
    if (b.dataset.preset === "starter") Object.assign(prefs, { exp: "2", expUnknown: true, levels: prefs.levels.filter((l) => !["senior", "lead"].includes(l)) });
    syncInputs(); update();
  });
  $("tabs").addEventListener("click", (e) => {
    const b = e.target.closest("[data-tab]"); if (!b) return;
    prefs.tab = b.dataset.tab; update();
  });
  $("region").addEventListener("change", (e) => { prefs.region = e.target.value; update(); });
  $("posted").addEventListener("change", (e) => { prefs.posted = e.target.value; update(); });
  $("remote").addEventListener("change", (e) => { prefs.remote = e.target.checked; update(); });
  let qt;
  $("q").addEventListener("input", (e) => { clearTimeout(qt); qt = setTimeout(() => { prefs.q = e.target.value.trim(); update(); }, 150); });
  $("more").addEventListener("click", () => { shown += PAGE; renderList(); });
  $("summary").addEventListener("click", (e) => {
    if (e.target.id !== "reset") return;
    Object.assign(prefs, { ...DEFAULTS, tab: prefs.tab });
    syncInputs(); update();
  });
  $("list").addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]"); if (!b) return;
    const { id, act } = b.dataset;
    if (marks[id] === act) delete marks[id]; else marks[id] = act;
    store.set("jr.marks", marks);
    const keep = shown;
    renderHeader(); renderCats(); renderTabs(); renderList();
    shown = keep;
  });

  function syncInputs() {
    $("posted").value = prefs.posted;
    $("german").value = prefs.german;
    $("exp").value = prefs.exp;
    $("exp-unknown").checked = prefs.expUnknown;
    $("remote").checked = prefs.remote;
    $("q").value = prefs.q;
    renderRegions();
  }

  Promise.all([fetch("jobs.json").then((r) => r.json()), fetch("meta.json").then((r) => r.json())])
    .then(([jobs, meta]) => {
      JOBS = jobs; META = meta;
      rememberVisit(meta.today);
      syncInputs();
      renderAll();
    })
    .catch(() => {
      $("summary").textContent = "还没有数据。等 GitHub Actions 第一次运行完成后刷新页面。";
    });
})();
