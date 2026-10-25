# Feishu Harvest — 架构方案与拓扑

> v2.0 · 2026-10-25 · 重构自 `feishu_crawler.py` (v22 单文件 1983 行) 与 `feishu-kb-client` 原型

---

## 1. 重构动机（旧版痛点 → 新版对策）

| 旧版痛点 | 表现 | 新版对策 |
|---|---|---|
| 巨石脚本 | `feishu_crawler.py` 1983 行，UI/网络/提取/导出全部耦合 | 六层单向依赖架构，每层可独立替换 |
| 串行爬取 | 逐页 goto，99 页 ≈ 50 分钟，99% 时间在等渲染 | 多 Tab 页面池并行 + 全局自适应节流 |
| 无状态 | 中断即全部重来；无增量 | SQLite WAL 任务库，节点级断点续爬 + 内容哈希增量 |
| 固定 sleep | `asyncio.sleep(8)` 硬编码，慢页面丢失、快页面浪费时间 | 条件等待 + AIMD 自适应并发（借 Scrapy AutoThrottle） |
| 单页失败全靠 try | 日志散落 print，无法观察 | 结构化 EventBus → SSE → 前端实时流 |
| 图片处理低效 | 逐张串行下载 + 强制 base64 内嵌（50MB 巨型 md） | URL 去重 + 并发下载池，内嵌改为可选 |

---

## 2. 技术栈

| 层 | 技术 | 理由 |
|---|---|---|
| 运行时 | Python 3.10+ / asyncio | 复用 v22 已验证的 Playwright 提取链路 |
| 浏览器 | Playwright（持久化 context，登录态复用） | 飞书依赖 JS 渲染 + 登录 Cookie |
| 存储 | SQLite (WAL) | 单文件零运维，节点级任务状态机 |
| API | FastAPI + SSE | REST 简单可靠，SSE 单向推送进度足够 |
| 前端 | Vite + React 18 + TypeScript + Tailwind | 组件化 + HMR，Tailwind 无运行时开销 |
| 通信 | `http://127.0.0.1:19527`，Vite 代理 `/api` | 生产模式后端直接托管前端静态文件 |

---

## 3. 分层架构（依赖单向向下，上层不知道下层存在）

```
┌─────────────────────────────────────────────────────────────┐
│  Frontend  (React + Vite + Tailwind)                        │
│  树形选择 · 任务仪表盘 · 实时日志 · 设置面板                  │
└──────────────┬──────────────────────────▲───────────────────┘
          REST │ 操作指令                  │ SSE 事件流
┌──────────────▼──────────────────────────┴───────────────────┐
│  api/   FastAPI 路由 · SSE 出口 · 静态托管        （薄适配层）│
├─────────────────────────────────────────────────────────────┤
│  pipeline/  编排：发现 → 提取 → 写盘 → 导出       （业务流）  │
│      crawler.py  worker 池调度，只面向 EventBus/Store/Transport│
├──────────────┬──────────────────────────────────────────────┤
│  transport/  │  store/          │  core/                    │
│  Playwright  │  SQLite WAL      │  自适应节流 · 熔断 · 重试  │
│  生命周期     │  节点/运行状态    │  令牌桶 · 抖动 · 退避      │
│  PagePool    │  内容哈希/资产    │  (与飞书零耦合, 纯机制)    │
├──────────────┴──────────────────────────────────────────────┤
│  legacy/  从 v22 移植的提取内核（唯一了解飞书 DOM/内部 API 的层）│
│  DOM→MD 转换器 · 条件等待 · bitable 直采 · 文件节点/附件下载  │
└─────────────────────────────────────────────────────────────┘
```

**解耦规则**

1. `legacy/` 是唯一允许出现 `data-block-type`、`/space/api/...` 字样的层 —— 飞书改版只改这一层。
2. `pipeline/` 不 import Playwright/FastAPI，只面向 `PagePool` 抽象与 `EventBus`。
3. `core/throttle` 是纯机制（不关心飞书），可被任何爬取任务复用。
4. 未来若接入飞书 Open API（企业审批通过后），新增 `transport/openapi.py` 实现
   相同接口即可整体替换浏览器通道 —— 借鉴 [feishu2md](https://github.com/Wsine/feishu2md)
   与 [feishu-pages](https://github.com/longopenfeishu/feishu-pages) 的官方 API 路线。

---

## 4. 运行拓扑

```
┌─────────────── 你的一台电脑 ────────────────┐
│                                             │
│  浏览器 127.0.0.1:19527                     │
│  ┌───────────────────────────────────┐      │
│  │ React 仪表盘                      │      │
│  │  EventSource ◀──── SSE /api/events│      │
│  │  fetch ────────▶ REST /api/*      │      │
│  └────────────┬──────────────────────┘      │
│               ▼                             │
│  ┌───────────────────────────────────┐      │
│  │ FastAPI (uvicorn)                 │      │
│  │  EventBus ─▶ SSE 订阅者           │      │
│  │  CrawlRunner (asyncio.Task)       │      │
│  │   ├─ PagePool (1 context × N tab) │      │
│  │   ├─ Store (SQLite WAL)           │      │
│  │   └─ AdaptiveGate                 │      │
│  └────────────┬──────────────────────┘      │
│               ▼ Playwright CDP               │
│  ┌───────────────────────────────────┐      │
│  │ Chromium (持久化 feishu_profile/) │      │
│  │  Tab1  Tab2  Tab3 …  共享登录态   │      │
│  └────────────┬──────────────────────┘      │
└───────────────┼─────────────────────────────┘
                ▼  HTTPS（全部经页面会话，与真人浏览一致）
        ┌──────────────────┐
        │  feishu.cn       │
        └──────────────────┘
```

单进程模型：uvicorn 一个进程承载 API + 爬取任务；Chromium 是它唯一的子进程。
不引入 Redis/Celery/消息队列 —— 单机单人场景，SQLite + asyncio 原生队列足够，
这也让"开箱即跑"成为可能。

---

## 5. 项目结构

```
feishu-harvest/
├── docs/
│   ├── ARCHITECTURE.md          # 本文档：方案/拓扑/结构/解耦
│   └── STRATEGY.md              # 效率·稳定性·速度策略 + 开源方案对照
├── backend/
│   ├── requirements.txt
│   └── harvest/
│       ├── __main__.py          # python -m harvest [--port 19527]
│       ├── config.py            # 运行配置（JSON 持久化 + 环境变量覆盖）
│       ├── events.py            # EventBus：发布/订阅 + 环形缓冲 + SSE 流
│       ├── textutil.py          # sfn 文件名规范化 / 零宽字符清洗
│       ├── core/
│       │   └── throttle.py      # AdaptiveGate(AIMD) · TokenBucket · CircuitBreaker
│       ├── store/
│       │   └── db.py            # SQLite WAL：runs / nodes / assets 状态机
│       ├── transport/
│       │   └── browser.py       # Playwright 生命周期 + PagePool + 登录检测
│       ├── legacy/              # v22 提取内核（已验证代码，行为对齐）
│       │   ├── mdconvert.py     # DOM → Markdown 转换器（convert_* 全家桶）
│       │   ├── extract.py       # 条件等待 · 视口拉伸 · blob 捕获 · extract_page
│       │   ├── bitable.py       # 多维表格 clientvars/records 直采
│       │   └── files.py         # folder_manager / file_node / 附件下载
│       ├── pipeline/
│       │   ├── discover.py      # BFS + 虚拟滚动深度展开通道
│       │   ├── crawler.py       # 三阶段编排 + worker 池 + 断点续爬
│       │   ├── writers.py       # 层级写盘 · README 索引 · 报告
│       │   └── pdf.py           # MD → 合并 PDF（Chromium 打印）
│       └── api/
│           ├── app.py           # FastAPI 工厂 + 前端静态托管
│           └── routes.py        # 任务/树/登录/配置/事件 路由
├── frontend/
│   ├── package.json  vite.config.ts  tailwind.config.js  tsconfig.json
│   ├── index.html
│   └── src/
│       ├── main.tsx  App.tsx  api.ts  types.ts
│       ├── store.ts             # SSE 订阅 → useSyncExternalStore
│       └── components/          # Sidebar/Tree/Progress/Log/Settings/StatCard
├── 启动.bat                      # Windows 一键启动（自动装依赖 + 起后端）
└── README.md
```

---

## 6. 关键数据流

### 6.1 爬取主流程（三阶段）

```
POST /api/crawl/start {url, tabs, format…}
   │
   ▼
Crawler.run()
   ├─ ① DISCOVER  单 tab：滚动展开虚拟列表 → 采集 data-node-uid
   │              → 增量合并（store 中已有节点直接复用，不重复 goto）
   ├─ ② EXTRACT   PagePool(N tab) worker 并发
   │              每个 worker: AdaptiveGate.wait() → goto → 条件等待
   │              → 路由到 extractor(doc/bitable/folder/file) → 写盘
   │              每页完成即更新 store（崩溃可续）+ 发布事件
   └─ ③ EXPORT    README 索引 + 报告 +（可选）合并 PDF +（可选）图片内嵌
```

### 6.2 状态机（nodes 表，断点续爬的核心）

```
pending ──▶ running ──▶ done
    │           │
    │           └──▶ failed ──(重跑)──▶ pending
    └──(重跑)──▶ running
```

`content_hash`（对 md 正文取 MD5）命中且 `--fresh` 未开启时直接跳过 →
二次爬取只处理变化/失败节点，这就是"增量同步"。

---

## 7. 与旧版的行为兼容性

| 能力 | 保留 | 改进 |
|---|---|---|
| DOM→MD 全类型块转换 | ✅ 逐函数移植（heading/code/image/list/todo/table/callout/bookmark/grid/file/formula） | 代码块恢复、导航壳剔除逻辑原样保留 |
| 多维表格直采 | ✅ clientvars + records 分页 | 请求全部过 AdaptiveGate |
| 文件管理页 / 文件节点 | ✅ 分页列举 + 真实下载 + 预览流兜底 | 并发下载池 + URL 去重 |
| 登录态 | ✅ 持久化 profile + 三态检测 | 连续失败自动熔断并提示重新扫码 |
| 层级输出目录 | ✅ 目录树对应导航树 | `_intro.md` 章节简介同样保留 |
| Markdown / PDF 导出 | ✅ | PDF 样式沿用 v22 模板 |

---

## 8. 免责与边界（沿袭 v22）

- 仅抓取当前登录用户**有权查看**的内容，不绕过任何权限控制；
- 不做代理池 / IP 轮换 / 指纹伪装 —— 事实上"像人一样慢"就是最稳的反封锁策略，
  具体节奏控制见 [STRATEGY.md](./STRATEGY.md)；
- 仅供个人学习，内容版权归原作者，禁止大规模批量爬取。
