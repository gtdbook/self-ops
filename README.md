# 📡 self-ops — 自运营情报中枢

一套跑在 GitHub Actions 上的**全自动信息体系**：自动抓取 → 自动处理 → 自动部署，
失败自动告警、恢复自动关闭，不需要任何服务器和人工干预。

- 🌐 在线站点：<https://gtdbook.github.io/self-ops/>
- 📊 运行面板：见仓库 issue「📊 self-ops 运行面板」，每日自检报告沉淀于此
- 🚨 告警闭环：异常自动开 issue（label `self-ops-alert`），恢复后自动评论并关闭

## 体系架构

```
┌─────────────────── pipeline.yml（每小时，UTC 整点）───────────────────┐
│                                                                      │
│  抓取 fetch.py                  处理 process.py            部署        │
│  ├─ Hacker News 热榜    ──►  ├─ 按热度/时间排序     ──►  build_site.py │
│  ├─ GitHub 新星榜(搜索)       ├─ 每板块取 Top N          生成静态站     │
│  ├─ Product Hunt 新品         ├─ 生成日报 JSON+MD     ──►  GitHub Pages │
│  └─ 博客 RSS × 6              ├─ 更新总索引                (自动部署)    │
│       同日多次运行按 id       └─ 清理 60 天前过期数据                   │
│       增量合并去重，数据全部提交回本仓库                                 │
│                                                                      │
│  任一阶段失败 ──► alert.py 立即开告警 issue                            │
└──────────────────────────────────────────────────────────────────────┘

┌─────────────────── heartbeat.yml（每日北京时间 06:30）────────────────┐
│  三项健康检查：流水线活性(≤26h) / 数据新鲜度(≤2天) / Pages 可达性       │
│  不健康 ──► 更新告警 issue     健康 ──► 自动关闭告警                    │
│  无论结果如何，都向「运行面板」issue 追加一条当日状态                    │
└──────────────────────────────────────────────────────────────────────┘

┌─────────────────── evolve.yml（每日北京时间 07:10）────────────────────┐
│  ① 发布昨日「每日情报」issue（编号条目 + 隐藏投票映射，自带邮件通知）     │
│  ② 学习该 issue 下的 like/dislike 评论投票                              │
│  ③ 更新 data/prefs.json（板块权重 + 关键词权重）并提交回仓库            │
└──────────────────────────────────────────────────────────────────────┘

### 日报的三个合成板块

| 板块 | 机制 |
|---|---|
| 🔗 多源共振 | 跨源标题相似度聚类（Jaccard ≥ 0.4），≥2 个源报道同一事件才入选 |
| 📈 飙升榜 | HN 条目逐小时记录分数轨迹（24h 窗口），按涨分斜率排序 |
| 口味加权 | `data/prefs.json` 的板块权重决定板块顺序；关键词权重给 HN/GitHub 条目加分 |
```

## 目录结构

```
config.json                  # 数据源、evolve 参数、保留期、时区等全部配置
scripts/
  fetch.py                   # 抓取：HN / GitHub Search / RSS / PH（含分数轨迹记录）
  process.py                 # 处理：多源共振、飙升榜、口味加权、日报与索引、清理
  build_site.py              # 建站：生成 _site/ 静态站点 + feed.xml（RSS 输出）
  heartbeat.py               # 自检：三项健康检查 + issue 闭环
  daily_issue.py             # 演化：发布每日情报 issue（投票载体）
  learn.py                   # 演化：学习投票 → data/prefs.json → 提交回仓库
  alert.py                   # 流水线失败即时告警
  common.py                  # 共用工具（纯标准库，无第三方依赖）
data/
  raw/<日期>/<源>.json        # 原始抓取结果（同日增量合并去重 + 分数轨迹）
  digest/<日期>.json|.md      # 加工后的每日情报
  prefs.json                 # 学习到的口味偏好（板块权重 + 关键词权重）
  index.json                 # 总索引（天数、条数、各源统计）
.github/workflows/
  pipeline.yml               # 小时级流水线（抓取→处理→提交→部署）
  heartbeat.yml              # 每日自检
  evolve.yml                 # 每日演化（日报 issue + 口味学习）
```

## 数据源

| 源 | 内容 | 说明 |
|---|---|---|
| Hacker News | 当前热榜 Top 25 | 官方 Firebase API |
| GitHub Search | 近 7 天新建且 ⭐>30 的仓库 | 按 star 排序，等价轻量 Trending |
| Product Hunt | 当前策展新品 + AI 分类 | 官方 Atom feed，展示 tagline 与发布日 |
| 博客 RSS | OpenAI / DeepMind / Google AI / Hugging Face / TechCrunch AI / Ars Technica | 标题+摘要+链接 |

在 `config.json` 中即可增删源：加 RSS 只需在 `feeds` 里追加一行；
`aihot` 源已内置但默认关闭——AIHOT 条款（<https://aihot.news/terms>）规定
公开镜像/批量公开再分发需书面授权，仅个人非商业场景建议启用。

## 日常使用

- **看情报**：直接访问 [在线站点](https://gtdbook.github.io/self-ops/)（含 [RSS 订阅](https://gtdbook.github.io/self-ops/feed.xml)），或在仓库里翻 `data/digest/*.md`
- **投口味票**：每天早晨的「📰 每日情报」issue 下回复 `like 3 7` / `dislike 5`（或 `赞`/`踩`），
  体系每天学习一次，板块排序和条目权重会逐渐贴合你的口味；偏好带时间衰减，长期不投票会回归中性
- **手动触发**：Actions → pipeline / heartbeat / evolve → Run workflow
- **改频率**：调整对应 workflow 里的 `cron` 表达式（UTC 时区）
- **加数据源**：`scripts/fetch.py` 注册一个新的 `fetch_xxx` 函数 + `config.json` 加开关

## 设计要点

- **零依赖**：全部脚本只用 Python 标准库，`setup-python` 之外不装任何包，没有供应链漂移问题
- **零密钥**：流水线只用 Actions 内置 `GITHUB_TOKEN`，不存在过期问题；本仓库不含任何凭据
- **自保鲜**：每小时的数据提交让仓库始终活跃，不会触发 GitHub「60 天不活跃自动停用定时任务」机制
- **自愈**：单源失败不影响整体（错误记录进数据并在站点上提示）；全线失败立即告警；
  每日自检兜底，恢复自动闭环
- **自清理**：raw 数据保留 60 天、索引保留 120 天、站点展示 60 天，仓库体积可控

## 许可

代码 MIT。抓取内容的版权归原作者所有，站点仅收录标题、摘要与链接。
