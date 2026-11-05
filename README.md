# 📡 self-ops — 自运营情报中枢

一套跑在 GitHub Actions 上的**全自动信息体系**：自动抓取 → 自动处理 → 自动部署 → 自动合成 → 自动进化，
失败自动告警、漏跑自动补触发，不需要任何服务器和人工干预。

- 🌐 在线站点：<https://gtdbook.github.io/self-ops/>
- 🧬 进化面板：<https://gtdbook.github.io/self-ops/evolution.html>（适应度、偏好词、知识花园、进化日志）
- 🗓 每周洞察：<https://gtdbook.github.io/self-ops/weekly.html>（周报 + 月度回顾）
- 📊 运行面板：见仓库 issue「📊 self-ops 运行面板」，每日自检报告沉淀于此
- 🚨 告警闭环：异常自动开 issue（label `self-ops-alert`），恢复后自动评论并关闭

## 体系架构：四条自循环

```
┌──────────── pipeline.yml（每小时，UTC 整点）────────────┐
│  抓取 fetch.py ──► 处理 process.py ──► 部署 Pages      │
│  · 进化禁用的源自动跳过（探针日自动重试）               │
│  · 板块按「口味权重 × 进化适应度」排序                 │
│  · 同日多次运行增量合并去重，数据全部提交回本仓库       │
└────────────────────────────────────────────────────────┘
┌──────────── heartbeat.yml（每日北京时间 06:30）─────────┐
│  五项自检：流水线活性 / 数据新鲜度 / Pages 可达性        │
│           / 周报环新鲜度 / 进化环活性                   │
│  异常 ──► 自动开告警 issue；恢复 ──► 自动关闭           │
│  自愈 ──► 流水线漏跑 >26h 或进化环停摆时，              │
│           通过 workflow_dispatch 自动补触发对应 workflow │
└────────────────────────────────────────────────────────┘
┌──────────── evolve.yml（每日北京时间 07:10）────────────┐
│  ① 发布昨日「每日情报」issue（编号条目 + 隐藏投票映射）  │
│  ② learn.py：学习日报/周报下的 like/dislike 投票        │
│  ③ self_evolve.py 内生进化（不需要任何人投票）：        │
│     · 源适应度：按 7 日产量/在场率/报错率评估，         │
│       低迷源自动降权 → 持续低迷自动禁用 → 到期探针复活   │
│     · 主题自学：共振/飙升板块的高频主题词提升关键词权重  │
│     · 自然衰减：所有偏好逐日回归中性，避免一次学习永久生效│
│     · 进化账本：每一次自动决策留痕 data/evolution.json   │
└────────────────────────────────────────────────────────┘
┌──────────── weekly.yml（每周一北京时间 07:40）──────────┐
│  weekly.py 周度合成：                                  │
│  · 主题聚类 + 本周 Top 条目 + 源产量环比               │
│  · data/garden.json 知识花园生长：跨周主题追踪，        │
│    反复出现的主题越来越"常青"，8 周不见自动凋零         │
│  · 每月首个周一自动合成上月「月度回顾」                 │
│  · 发布周报 issue（也带投票映射，口味学习吃得到）       │
│  · 8 天前的日报 issue / 35 天前的周报 issue 自动归档关闭│
└────────────────────────────────────────────────────────┘
```

进化闭环链路：**适应度/主题 → prefs.json → 次日日报排序生效 → 周报记录变化 → 进化账本可审计**，
体系不依赖任何人投票也会持续演化；人工投票（日报/周报下回复 `like 3` / `踩 5`）叠加在内生进化之上。

## 目录结构

```
config.json                  # 数据源、evolve 参数（含适应度阈值）、weekly 参数、保留期、时区
scripts/
  fetch.py                   # 抓取：HN / GitHub Search / RSS / PH（含分数轨迹；自动跳过进化禁用源）
  process.py                 # 处理：多源共振、飙升榜、口味×适应度排序、日报与索引、清理
  build_site.py              # 建站：首页 + 每日归档 + 进化面板 + 周报页 + feed.xml
  heartbeat.py               # 自检 + 自愈：五项健康检查、漏跑自动补触发、issue 闭环
  daily_issue.py             # 演化：发布每日情报 issue（投票载体）
  learn.py                   # 演化：学习日报/周报投票 → data/prefs.json → 提交回仓库
  self_evolve.py             # 内生进化：源适应度 + 主题自学 + 自然衰减 + 进化账本
  weekly.py                  # 周度合成：周报 + 知识花园 + 月度回顾 + issue 归档
  alert.py                   # 流水线失败即时告警
  common.py                  # 共用工具（纯标准库，无第三方依赖）
data/
  raw/<日期>/<源>.json        # 原始抓取结果（同日增量合并去重 + 分数轨迹）
  digest/<日期>.json|.md      # 加工后的每日情报
  weekly/<周键>.json|.md      # 每周洞察（主题、Top 条目、源环比、进化回顾）
  monthly/<月份>.json|.md     # 月度回顾（每月首个周一自动合成）
  garden.json                 # 知识花园：跨周主题追踪（常青/凋零）
  prefs.json                  # 口味偏好（投票学习 + 主题自学，逐日衰减）
  meta/fitness.json           # 各源适应度与自动决策状态（降权/禁用/探针）
  meta/evolution.json         # 进化账本：每一次自动决策的留痕（kind/target/detail）
  index.json                  # 总索引（天数、条数、各源统计）
.github/workflows/
  pipeline.yml               # 小时级流水线（抓取→处理→提交→部署）
  heartbeat.yml              # 每日自检 + 自愈看门狗
  evolve.yml                 # 每日演化（日报 issue + 投票学习 + 内生进化）
  weekly.yml                 # 每周合成（周报 + 知识花园 + 月度回顾 + issue 归档）
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
- **手动触发**：Actions → pipeline / heartbeat / evolve / weekly → Run workflow
- **改频率**：调整对应 workflow 里的 `cron` 表达式（UTC 时区）
- **加数据源**：`scripts/fetch.py` 注册一个新的 `fetch_xxx` 函数 + `config.json` 加开关

## 设计要点

- **零依赖**：全部脚本只用 Python 标准库，`setup-python` 之外不装任何包，没有供应链漂移问题
- **零密钥**：所有工作流只用 Actions 内置 `GITHUB_TOKEN`，不存在过期问题；本仓库不含任何凭据
- **自保鲜**：每小时的数据提交让仓库始终活跃，不会触发 GitHub「60 天不活跃自动停用定时任务」机制
- **自愈**：单源失败不影响整体（错误记录进数据并在站点上提示）；全线失败立即告警；
  每日五项自检兜底，恢复自动闭环；定时任务漏跑时看门狗自动补触发
- **自适应**：数据源的生死去留由 7 日适应度自动裁决——产量低迷先降权、持续低迷自动禁用、
  到期探针复活、恢复健康逐步回权；决策全部留痕进化账本，随时可审计
- **自清理**：raw 数据保留 60 天、索引保留 120 天、站点展示 60 天、
  日报 issue 8 天后自动归档、周报 issue 35 天后自动归档，仓库体积与 issue 列表均可控

## 许可

代码 MIT。抓取内容的版权归原作者所有，站点仅收录标题、摘要与链接。

<!-- sediment:start -->
**🌿 沉淀库**（自动维护区，勿手改）· 更新于 2026-11-05
- 沉淀条目 **12** 条（未归类 8 · AI 与智能体 4）
- 新芽候选 0 个 · 双链 48 条
- 沉淀园种子：74 篇 · 兴趣词 18 个（[sediment-garden](https://sediment-garden.pages.dev/)）
- 知识版图见在线站点 [🌿 沉淀库](https://gtdbook.github.io/self-ops/sediment.html)
<!-- sediment:end -->
