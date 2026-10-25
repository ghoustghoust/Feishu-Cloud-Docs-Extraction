# 效率 · 风控 · 稳定性 · 速度 —— 策略与开源方案对照

> 回答四个问题：**怎么更快？怎么不被限流/风控盯上？怎么更稳？怎么传得更快？**
> 核心立场：不搞对抗（代理池、指纹伪造、IP 轮换），用**开源界验证过的"礼貌爬取"工程**把单通道利用率做到最高。

---

## 1. 总纲：被风控的本质与解法

风控模型盯的是**异常特征**，不是"你爬了这个量"：

| 风控特征 | 机器爬虫的样子 | 本工具的样子 |
|---|---|---|
| 请求节奏 | 恒定间隔 / 爆发脉冲 | 随机抖动 + AIMD 自适应，节奏像人（快慢不均） |
| 并发特征 | 几十连接并发 | 单登录会话 2-4 个 Tab，与重度用户一致 |
| 失败特征 | 撞 429 后换 IP 硬撞 | 收到 429 立即退避 + 降速，尊重 `Retry-After` |
| 请求量 | 全量重复爬 | 内容哈希增量同步 —— **少发请求本身就是最强隐身** |
| 来源 | 数据中心 IP | 你自己家的 IP + 真实登录态 + 真实浏览器指纹 |

结论：**效率的提升不来自并发爆炸，而来自消灭空等**。旧版串行爬 99 页 50 分钟，
其中约 95% 的时间在 `sleep` 等渲染 —— 多 Tab 并行把这段等待重叠起来，请求总量不变。

---

## 2. 效率策略（在不惊动任何人的前提下提速）

### 2.1 多 Tab 页面池 —— 消灭渲染等待（预期 2-4× 提速）

- 一个持久化 BrowserContext 开 **N 个 Tab（默认 3，可调 1-6）**，共享登录态与 Cookie；
- 每个页面的耗时 = 导航 + 渲染等待 + DOM 提取，全是 I/O 等待 → N 路重叠后
  吞吐 ≈ N×，而**对服务器的瞬时压力只多 2-3 个并发连接**（重度用户本来就有多个 Tab）；
- Tab 之间通过全局 `AdaptiveGate` 协调节奏，避免同瞬并发导航。

### 2.2 AIMD 自适应节流 —— 学 Scrapy AutoThrottle

> 参考：[Scrapy AutoThrottle 扩展](https://docs.scrapy.org/en/latest/topics/autothrottle.html)，
> 其思想源自 TCP 拥塞控制（加性增、乘性减）。

```
每次导航前:  wait = base_interval × jitter(0.7~1.3)
成功一批:    wait 缓慢下降（×0.95，加性增），下限 floor
观测到 429 / 超时率升高 / 延迟变长:  wait 翻倍（乘性减），上限 ceiling
连续失败:    CircuitBreaker OPEN → 冷却 30s → HALF-OPEN 单发探测
```

- 慢启动（start low）：首次以最保守间隔爬前 10 页，无异常再进入自适应区间；
- 对 429 响应**尊重 `Retry-After` 头**，没有则按指数退避 + 全抖动
  （AWS 架构博客的 [Exponential Backoff and Jitter](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/) 结论：full jitter 折衷冲突最优）。

### 2.3 增量同步 —— 最便宜的提速是"不爬"

- `nodes` 表持久化每个节点 `content_hash` + 状态机（见 ARCHITECTURE §6.2）；
- 重跑时 `done` 且哈希未变的节点直接跳过：第二次全量爬取 ≈ 只爬新/改页面；
- 发现阶段同样增量：上次落盘的树作为种子，只扩展缺失子树（继承 v22 增强 BFS）。

### 2.4 减少一次 goto 的浪费

- bitable / folder_manager / file_node 走**页面内 fetch 的内部 API**（已在当前页上下文），
  不做多余导航；
- 提取用**条件等待**（innerText 长度连续 3 轮稳定）替代固定 `sleep(8)`，
  快页面立走、慢页面兜底 25s —— 单页平均等待显著下降。

---

## 3. 传输速度

| 手段 | 说明 |
|---|---|
| 连接复用 | 图片/附件经 `context.request`（浏览器网络栈，HTTP/2 + keep-alive），不再每图新建连接 |
| 下载并发池 | 图片/附件独立 `Semaphore(6)` + 令牌桶限速，与页面提取互不阻塞 |
| 去重 | 图片 URL → MD5 命名，同图全库只下载一次；`assets` 表记录 |
| 大文件分段 | 附件先 HEAD 探测 `Accept-Ranges`，支持则 4MB 分块并行（IDM 风格），失败回退单流 |
| 内嵌可关 | base64 内嵌默认关闭（速度优先、文件小）；需要单文件迁移时勾选"自包含导出" |
| 流式落盘 | 附件边收边写临时文件，完成后原子 rename —— 大文件不吃内存 |

> FIXME-RANGE 沿袭 v22：飞书 `download/all` 的 Range 支持需实测，探测失败自动回退。

---

## 4. 稳定性

### 4.1 持久化状态机（崩溃恢复）

SQLite WAL 模式：进程被杀/断电后重开，`running` 状态节点回退为 `pending`，从未完成的
地方继续 —— 旧版"中断即重来"不复存在。

### 4.2 熔断与降级（借 pybreaker / resilience4j 的状态机）

```
CLOSED ──连续≥5次网络失败──▶ OPEN ──冷却30s──▶ HALF-OPEN ──成功──▶ CLOSED
                                │                    └──失败──▶ OPEN
登录连续3页失败 ──▶ 判定 Cookie 过期，发布 need_login 事件（前端弹提示），任务暂停而非空跑
```

### 4.3 幂等与原子写

- 文件写入一律 **临时文件 + `os.replace` 原子落盘**，中断不会留下半截 md；
- 同名文件避让 `{name}_2.md`；节点级任务可重复执行不产生副作用。

### 4.4 可观测性

- 全链路事件走 `EventBus`（阶段/页名/字数/错误），环形缓冲保留最近 500 条，
  SSE 推送前端 + 同时写 `logs/harvest.log`；
- 后台任务持强引用（防 asyncio GC 回收 —— v22 客户端实测踩过的坑）；
- 单页异常只记 `failed` 不中断整体，跑完统一在报告里列出。

### 4.5 长任务保护

- 发现阶段硬上限 20 轮、连续 2 轮零新增收敛（继承 v22 增强版 BFS）；
- 提取阶段条件等待兜底超时；下载兜底三级降级（原文件 → page.request → 预览 PDF 流）。

---

## 5. 开源方案对照表（本设计站在谁的肩膀上）

| 开源项目 | 借鉴点 | 落在本项目哪里 |
|---|---|---|
| [Scrapy](https://github.com/scrapy/scrapy) | AutoThrottle（AIMD）、礼貌延迟、重试中间件、去重 | `core/throttle.AdaptiveGate`、重试退避 |
| [Crawl4AI](https://github.com/unclecode/crawl4ai) | Playwright 复用 + 异步调度器、按负载调整并发 | `transport.PagePool`、worker 池 |
| [tenacity](https://github.com/jd/tenacity) | 指数退避 + full jitter 的标准实现 | throttle 内置重试策略 |
| [feishu2md](https://github.com/Wsine/feishu2md) / [feishu-pages](https://github.com/longopenfeishu/feishu-pages) | 官方 Open API `export_tasks` 异步导出模式 | 预留 `transport/openapi.py` 演进方向 |
| [IDM/wget2](https://www.gnu.org/software/wget/) | Range 分块并行下载 + 探测回退 | 附件分段下载（FIXME-RANGE） |
| [pybreaker](https://github.com/niwinz/pybreaker) | 熔断器三态状态机 | `core/throttle.CircuitBreaker` |
| SQLite WAL + outbox 模式 | 任务持久化、断点续传 | `store/db.py` |

---

## 6. 配置建议（默认即安全）

全部 14 项参数均可在控制台「设置」抽屉可视化调节（`data/config.json` 持久化），核心几项：

| 参数 | 默认 | 说明 |
|---|---|---|
| `tabs` | 3 | 并发 Tab 数；网络差/被限流时降到 1-2 |
| `min_interval` | 1.2s | 同 Tab 导航最小间隔 ×抖动 |
| `ceiling` | 30s | AIMD 退避上限：被限流时间隔最大退避到这 |
| `adaptive` | on | 开启 AIMD；关掉则恒定间隔（不推荐） |
| `nav_timeout` / `render_timeout` | 40s / 25s | 导航超时 / 渲染条件等待上限（超时计入退避） |
| `extract_retries` | 2 | 文档页过短/异常时重新导航重试次数 |
| `image_concurrency` | 4 | 单页图片并行下载数 |
| `embed_images` | off | 关=速度与体积优先；开=单文件自包含 |
| `download_attachments` | on | 文件节点/附件真实下载，失败自动降级为链接 |
| `clean` | off | 开=启动前清空输出目录；关=增量保留 |
| `fresh`（单次） | off | 任务卡片勾选：本次忽略缓存全量重爬 |

**夜间跑**是免费的最优解：任务挂上后错峰执行，负载感知 + 低峰带宽双收益。
