# Feishu Cloud Docs Extraction — 飞书知识库采撷

将你有权访问的飞书知识库/文档导出为本地 **Markdown / PDF**。

> **v2 全新重构版位于 [`feishu-harvest/`](feishu-harvest/)** —— 六层解耦架构、多 Tab 并行、
> AIMD 自适应节流、SQLite 断点续爬、实时仪表盘。已通过 691 节点 / 5 层深度的知识库实测。
>
> 旧版 v22 单文件脚本已归档至 `_archive_v22/`（本地保留，不入库）。

## 快速开始

```bash
cd feishu-harvest
# Windows: 双击 启动.bat   macOS/Linux: ./start.sh
```

详见 **[feishu-harvest/README.md](feishu-harvest/README.md)** 与设计文档
[docs/ARCHITECTURE.md](feishu-harvest/docs/ARCHITECTURE.md) ·
[docs/STRATEGY.md](feishu-harvest/docs/STRATEGY.md)。

## 免责声明

- 仅抓取当前登录账号**有权查看**的内容，不绕过任何权限控制；
- 不使用代理池 / IP 轮换 / 指纹伪装，仅采用礼貌爬取（限速 + 退避 + 增量）；
- 仅供个人学习使用，内容版权归原作者；禁止大规模批量爬取或商用传播。
