<p align="center">
  <img src="assets/icon.svg" width="96" alt="BiliScriptor logo" />
</p>

<h1 align="center">BiliScriptor（哔稿匠）</h1>

<p align="center">
  把 B 站视频解析成本地可追踪数据包：Markdown 报告、字幕、弹幕、评论、播放流候选与详细排障日志，一次归档，后续随便分析。
</p>

<p align="center">
  <a href="https://github.com/Qziky/BiliScriptor/actions/workflows/tests.yml"><img src="https://img.shields.io/github/actions/workflow/status/Qziky/BiliScriptor/tests.yml?branch=master&style=for-the-badge&label=tests" alt="Tests" /></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.10+" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/Qziky/BiliScriptor?style=for-the-badge" alt="License" /></a>
  <a href="https://github.com/Qziky/BiliScriptor/stargazers"><img src="https://img.shields.io/github/stars/Qziky/BiliScriptor?style=for-the-badge&logo=github" alt="GitHub stars" /></a>
  <a href="https://github.com/Qziky/BiliScriptor/releases"><img src="https://img.shields.io/github/v/release/Qziky/BiliScriptor?style=for-the-badge&label=release" alt="Latest release" /></a>
</p>

<p align="center">
  <img src="assets/hero-generated-v2.png" alt="BiliScriptor hero illustration" />
</p>

<p align="center">
  <code>Bilibili</code> · <code>CLI</code> · <code>Markdown Report</code> · <code>JSONL</code> · <code>SRT</code> · <code>Danmaku</code> · <code>Comments</code> · <code>Local First</code>
</p>

BiliScriptor 面向需要整理视频资料、归档评论弹幕、生成 Markdown 阅读报告的场景。它不会默认下载音视频文件，而是优先保存元数据、分 P、字幕、当前弹幕、评论、播放流候选和阶段 manifest，方便后续接入 ASR、OCR、抽帧或 LLM 分析流程。

如果这个项目刚好解决了你的资料归档、内容研究或 B 站数据整理问题，欢迎 Star 关注后续能力。

## 功能亮点

- 解析 B 站视频链接或 BV 号，生成结构化本地数据包
- 支持扫码登录并保存本地 Cookie
- 导出字幕为 `.jsonl` 和 `.srt`
- 抓取当前弹幕、评论树、播放流候选信息
- 自动生成适合阅读和引用的 `report.md`
- 在 `manifest.json` 中记录每个阶段的状态和失败原因
- 默认生成详细 `.log` 与 `.jsonl` 日志，方便定位网络、限流、缺失字幕和接口异常
- 默认隐藏 Cookie 敏感值，避免写入日志、报告或 manifest

## 适合谁使用

| 场景 | BiliScriptor 能做什么 |
| --- | --- |
| 内容创作者 | 把视频资料整理为可读报告，快速回看分 P、字幕、评论和弹幕线索 |
| 研究与资料归档 | 保存结构化 JSON/JSONL，方便后续检索、统计和二次处理 |
| 自动化工作流 | 将 B 站视频解析结果接入 ASR、OCR、抽帧或 LLM 分析流程 |
| 开发者排障 | 通过详细结构化日志定位 API 请求、阶段状态、重试和文件写入问题 |

## 效果预览

一次 `parse` 会生成完整数据包：

```text
output/BVxxxx/
  manifest.json
  video.json
  pages.json
  player/page_001.json
  streams/page_001.json
  subtitles/page_001_0_zh-CN.jsonl
  subtitles/page_001_0_zh-CN.srt
  danmaku/page_001.current.jsonl
  comments/comments.jsonl
  comments/tree.json
  report.md
```

`report.md` 会把关键信息整理成适合阅读和引用的 Markdown：

```md
# 视频标题

- BV 号：BVxxxx
- 分 P 数：1
- 字幕：已保存
- 当前弹幕：已保存
- 评论：已保存
```

日志 JSONL 适合脚本检索和问题定位：

```json
{"event":"pipeline.stage_success","stage":"comments","status":"ok","elapsed_ms":1234,"count":20}
{"event":"client.request_success","endpoint":"/x/web-interface/view","status":200,"elapsed_ms":321}
```

日志不会写入完整响应正文、字幕正文、评论正文、弹幕正文，也不会写入 Cookie 值或 token。

## 安装

要求 Python 3.10 或更高版本。

```bash
python -m venv .venv
python -m pip install -e .
```

安装后可使用包入口：

```bash
python -m biliscriptor --help
```

也可以使用命令行工具：

```bash
biliscriptor --help
```

## 快速开始

```bash
# 1. 扫码登录，保存本地 Cookie
python -m biliscriptor login

# 2. 解析视频并生成数据包
python -m biliscriptor parse "https://www.bilibili.com/video/BV1QEVY6jEYv/"

# 3. 基于已有数据包重新生成报告
python -m biliscriptor report output/BV1QEVY6jEYv

# 4. 仅抓取字幕
python -m biliscriptor subtitles BV1QEVY6jEYv
```

常用参数：

```bash
python -m biliscriptor parse BV1QEVY6jEYv \
  --output-dir output \
  --comment-pages 1 \
  --reply-pages 1 \
  --rate-limit 1.0 \
  --page 1
```

默认运行产物会分目录保存：登录 Cookie 和二维码在 `runtime/`，详细日志写入 `logs/`，解析数据输出到 `output/`。

## 命令说明

| 命令 | 作用 |
| --- | --- |
| `login` | 扫描 B 站二维码并保存 Cookie |
| `parse` | 解析视频并导出完整数据包 |
| `report` | 从已有输出目录生成 `report.md` |
| `subtitles` | 仅抓取指定视频的字幕 |

`parse` 支持按需跳过部分阶段：

```bash
python -m biliscriptor parse BV1QEVY6jEYv --skip-comments --skip-streams
```

如需扩大评论抓取范围，可显式开启：

```bash
python -m biliscriptor parse BV1QEVY6jEYv --all-comments --comment-pages 5 --reply-pages 3
```

## 日志系统

所有 CLI 命令默认都会生成详细日志，文件名形如：

```text
logs/20260624-010203-parse-12345.log
logs/20260624-010203-parse-12345.jsonl
```

- `.log` 适合直接阅读，`.jsonl` 适合用脚本检索和分析。
- 默认日志级别是 `DEBUG`，会记录命令启动/结束、阶段状态、HTTP 请求、重试、限速等待、文件写入、报告生成、登录轮询等事件。
- 控制台仍保持简洁；命令结束时会打印本次日志路径。
- 日志只记录 URL 路径和查询参数名、响应字节数、状态码、耗时、计数和文件路径，不记录完整响应正文、字幕正文、评论正文或弹幕正文。
- Cookie 值、`SESSDATA`、`bili_jct`、`DedeUserID`、`qrcode_key`、`csrf`、`token`、`w_rid` 等敏感字段会写成 `<redacted>`。

常用日志参数：

```bash
python -m biliscriptor parse BV1QEVY6jEYv --log-level INFO
python -m biliscriptor parse BV1QEVY6jEYv --log-format jsonl
python -m biliscriptor parse BV1QEVY6jEYv --log-dir my_logs
python -m biliscriptor parse BV1QEVY6jEYv --log-to-stderr
python -m biliscriptor parse BV1QEVY6jEYv --no-file-log
```

排障时优先查看 `.jsonl` 中的结构化事件，例如按 `event`、`stage`、`request_id` 或 `elapsed_ms` 过滤；控制台只保留命令摘要和本次日志路径。

真实解析烟测示例：

```bash
python -m biliscriptor parse "https://www.bilibili.com/video/BV11kVt6sEWA?"
```

该命令已用正式接口验证通过，结束摘要为 `Failures: 0`，输出包生成在 `output/BV11kVt6sEWA/`。本次运行会生成一组 `logs/<timestamp>-parse-<pid>.log` 和 `logs/<timestamp>-parse-<pid>.jsonl`；抽检 JSONL 可正常解析，Cookie 只记录名称，不记录值。

## 为什么 Star

- 项目专注“本地优先”的 B 站资料整理，不默认下载音视频文件，适合轻量归档。
- 输出格式面向二次处理：Markdown 给人读，JSON/JSONL 给脚本和 LLM 工作流读。
- 日志系统默认开启且严格脱敏，方便排障，也尽量降低隐私风险。
- 如果你需要更多解析阶段、批量处理或报告模板，Star 能帮助我判断优先级。

## Roadmap

- 批量解析视频列表和收藏夹
- 更丰富的报告模板与导出视图
- 面向 ASR/OCR/LLM 的数据包适配示例
- 更细粒度的评论、弹幕统计摘要
- 可选的媒体下载或外部下载器集成说明

## 本地开发

运行测试：

```bash
python -m unittest discover -s tests
python -m pytest
```

项目结构：

```text
biliscriptor/
  cli.py              # 命令行入口与参数
  pipeline.py         # 解析流程编排
  client.py           # B 站接口请求
  extractors.py       # API 数据归一化
  logging_config.py   # 文本/JSONL 日志与脱敏
  report.py           # Markdown 报告生成
  utils.py            # 文件与通用工具
tests/
  test_core.py
  test_unittest.py
```

贡献前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。开发新功能或修改关键流程时，请同步补充结构化日志事件，并遵守脱敏规则。

## Star 曲线

<p align="center">
  <a href="https://star-history.com/#Qziky/BiliScriptor&Date">
    <img src="https://api.star-history.com/svg?repos=Qziky/BiliScriptor&type=Date" alt="BiliScriptor star history curve" />
  </a>
</p>
