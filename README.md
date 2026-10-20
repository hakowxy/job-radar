# Job Radar

每天早上自动汇总德国的数据 / BI 职位：去重、分类，生成一个可以点选岗位类型的网页，并把当天的新职位通过 GitHub Issue 发到邮箱。

## 数据来源

只用官方开放的接口，不爬网页：

| 来源 | 说明 | 需要注册 |
|---|---|---|
| [Bundesagentur für Arbeit Jobbörse](https://www.arbeitsagentur.de/jobsuche/) | 德国最大的职位库，很多 StepStone、Indeed 上的岗位也会同步到这里 | 不需要 |
| [Arbeitnow](https://www.arbeitnow.com/) | 偏英文、国际化、可签证的岗位 | 不需要 |
| [Adzuna](https://www.adzuna.de/) | 聚合了很多德国招聘网站，还经常有薪资范围 | 免费注册（可选） |

同一个职位如果在多个来源里出现，会合并成一条，并保留每个来源的链接。

LinkedIn、StepStone、Indeed、XING 的用户协议禁止自动抓取，所以没有接入。建议直接在这些网站上设置"职位提醒"（Job Alert），它们会把新职位发到你的邮箱。

## 第一次设置（约 10 分钟）

1. 在 GitHub 新建一个**公开**仓库，例如 `job-radar`，把这些文件推送到 `main` 分支。
2. 打开仓库的 **Settings → Pages**，把 Source 设为 **GitHub Actions**。
3. 打开 **Actions** 页面，选择 "Daily job radar"，点 **Run workflow** 手动运行第一次。
4. 运行完成后，打开 `https://你的用户名.github.io/job-radar/`。

之后每天德国时间早上 6～7 点会自动运行。

### 每日邮件

每次运行后，如果有新职位，仓库里会新建一个 Issue 列出它们，并 @ 你，GitHub 会把它发到你的邮箱。
如果没收到，检查 GitHub 的 **Settings → Notifications**，确认 "Participating, @mentions and custom" 勾选了 Email。

### 可选：启用 Adzuna

1. 在 [developer.adzuna.com](https://developer.adzuna.com/) 免费注册，获取 `app_id` 和 `app_key`。
2. 打开仓库的 **Settings → Secrets and variables → Actions**，新建两个 secret：`ADZUNA_APP_ID` 和 `ADZUNA_APP_KEY`。

## 改成你想找的工作

所有设置都在 `config.toml` 里，改完推送到 GitHub，就会自动重新运行。

- **岗位类型**：每个 `[[category]]` 就是网页上的一个按钮。`queries` 是拿去搜索的词，`match` 是职位名里必须出现的词（用来过滤掉不相关的结果）。
- **地点**：`where = "München"` 加上 `radius_km = 50` 只看慕尼黑周边；留空就是全德国。
- **排除**：`exclude` 里的词出现在职位名中就丢掉。
- **邮件**：`[notify]` 里设置哪些类型要发邮件，也可以只发英文 JD、不要求德语、或经验要求不超过 N 年的职位。

## 网页上能做什么

- 点岗位类型按钮（可以多选），再按级别、联邦州、发布时间、远程、关键词筛选
- "新职位"标签页只显示你上次打开之后新出现的职位；在"全部"里会有一条线标出"以下是你上次已经看过的"
- **JD 语言**：英文 JD / 德文 JD / 都可以
- **德语要求**：完全不需要德语 / 德语只是加分 / 不限
- **工作经验**：无需经验、最多 1 / 2 / 3 / 5 年，可选择是否显示没写经验要求的职位
- **级别**（可多选）：实习 / Werkstudent、Entry level、Associate、Senior、Lead
- 快捷按钮："只要英文岗位"、"德语英语都可以"、"适合入门"
- 每个职位可以标记 **收藏 / 投递了 / 隐藏**

JD 语言、德语要求、经验和级别是程序从职位描述里自动判断的：统计德语和英语常用词判断语言，识别 "fluent German"、"Deutsch verhandlungssicher"、"German is a plus"、"3+ years of experience"、"erste Berufserfahrung" 这类说法。标题里写了 Junior / Senior 就按标题，没写就按经验年限推断（0–1 年 Entry，2–4 年 Associate，5 年以上 Senior）。判断可能有误，投递前请看原文。

收藏、投递等标记只保存在当前浏览器里，换设备不会同步。你的筛选条件也会被记住。

## 本地运行

只需要 Python 3.11 以上，不用安装任何包：

```bash
python scraper/fetch_jobs.py
mkdir -p dist && cp site/* dist/ && cp data/jobs.json data/meta.json dist/
python -m http.server -d dist      # 打开 http://localhost:8000
```

## 目录

```
config.toml              你要找什么工作
scraper/fetch_jobs.py    抓取、去重、分类、生成摘要（只用标准库）
scraper/enrich.py        从 JD 判断语言、德语要求、经验年限、级别
site/                    网页
data/jobs.json           当前在招职位（每天自动更新并提交）
data/meta.json           每个来源的运行状态
.github/workflows/       每天定时运行
```
