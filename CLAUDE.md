# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 语言偏好 / Language Preference

**请始终使用中文与用户对话。** 用户是中文使用者，所有解释、建议、错误分析和技术讨论都应使用中文进行。代码、命令和技术术语保持原文，但说明文字使用中文。

**Always communicate with users in Chinese.** Users are Chinese speakers; all explanations, suggestions, error analysis, and technical discussions should be in Chinese. Keep code, commands, and technical terms in their original form, but use Chinese for descriptive text.

## 项目概览

BiliScriptor（哔稿匠）是一个 Python CLI 工具，用于将 B 站视频解析为结构化的本地数据包。它提取元数据、字幕、弹幕、评论和播放流候选信息，但默认不下载音视频文件。项目强调本地优先架构、隐私安全的日志记录（激进的脱敏策略）、以及适合下游处理（LLM 工作流、ASR、OCR）的结构化输出。

## 开发命令

安装开发版本：
```bash
python -m pip install -e ".[dev]"
```

运行 CLI：
```bash
python -m biliscriptor --help
python -m biliscriptor login
python -m biliscriptor parse BV1QEVY6jEYv
python -m biliscriptor report output/BV1QEVY6jEYv
python -m biliscriptor subtitles BV1QEVY6jEYv
```

运行测试（避免真实网络调用，使用 mock）：
```bash
python -m pytest
python -m unittest discover -s tests
```

## 架构设计

BiliScriptor 采用**基于阶段的管道架构**，每个解析操作都是一个被跟踪的阶段，状态（ok/missing/skipped）记录在 `manifest.json` 中。

### 核心分层

**CLI 层** (`cli.py`)
- 基于 argparse 的命令路由入口点
- 命令：`login`、`parse`、`report`、`subtitles`
- 在任何命令执行前通过 `configure_logging()` 集成日志设置
- 所有日志参数（--log-level、--log-dir、--log-format、--no-file-log、--log-to-stderr）通过 `add_logging_arguments()` 添加

**Pipeline 层** (`pipeline.py`)
- `parse_video()`：编排完整的解析流程，经过各个阶段
- `parse_subtitles_only()`：轻量级的纯字幕提取
- 阶段流程：video_info → pages → player → subtitles → danmaku → comments → streams → report
- 每个阶段调用 `_stage_ok()`、`_stage_missing()` 或 `_stage_skipped()` 来更新 manifest
- `ParseOptions` 数据类持有所有配置；`ParseResult` 持有输出元数据
- 阶段计时通过 `_start_stage()` 和 `_elapsed_ms()` 跟踪

**Client 层** (`client.py`)
- `BiliClient`：带有 cookie jar、WBI 签名生成和重试逻辑的 HTTP 客户端
- WBI 签名：从 nav API 获取 img_key/sub_key，应用 mixin key 表，生成 md5 签名
- 错误归一化：将 B 站错误码（-101、-352、-503 等）映射为语义化状态字符串
- 所有请求都记录日志，URL 和参数已脱敏

**Extractors 层** (`extractors.py`)
- API 特定函数：`fetch_video_info()`、`fetch_player_v2()`、`fetch_streams()`、`fetch_danmaku()`、`fetch_comments()`、`fetch_subtitle()`
- 每个函数记录开始/成功事件，包含耗时和计数
- 字幕归一化：JSONL（每行一条字幕）和 SRT 导出
- 评论树构建，支持回复分页
- 弹幕解码通过 protobuf（`danmaku_pb.py`）

**日志系统** (`logging_config.py`)
- 双输出：`.log`（人类可读）和 `.jsonl`（结构化，机器可读）
- `log_event()`：结构化日志，事件类型如 `pipeline.stage_start`、`client.request_success`、`extractors.video_info_success`
- `sanitize_mapping_keys()` 和 `sanitize_url()` 在记录前脱敏敏感数据
- 脱敏字段：`SESSDATA`、`bili_jct`、`DedeUserID`、`qrcode_key`、`csrf`、`token`、`w_rid`、cookie 值
- URL 脱敏：对敏感键的查询参数值进行脱敏
- 日志文件命名：`logs/<timestamp>-<command>-<pid>.log` 和 `.jsonl`

**报告生成** (`report.py`)
- 从解析的数据包生成 `report.md`
- 读取 manifest、video.json、pages.json 和各阶段输出，生成可读摘要

**工具函数** (`utils.py`)
- 文件操作：`write_json()`、`write_jsonl()`、`write_srt()`、`write_txt()`
- `extract_bvid()`：从 URL 或原始 BV 字符串提取 BV 号
- `sanitize_for_manifest()`：在写入 manifest.json 前移除敏感字段

### 输出结构

每次解析会在 `output/BVxxxx/` 下创建一个目录：
```
output/BV1QEVY6jEYv/
  manifest.json           # 阶段状态、时间戳、失败原因
  video.json              # 来自 /view API 的原始视频元数据
  pages.json              # 归一化的分P列表，包含 cid、分P标题
  player/page_001.json    # 播放器数据，包含字幕列表
  streams/page_001.json   # 播放流候选、质量、编码
  subtitles/page_001_0_zh-CN.jsonl  # 字幕行（JSONL 格式）
  subtitles/page_001_0_zh-CN.srt    # 字幕行（SRT 格式）
  danmaku/page_001.current.jsonl    # 当前弹幕快照
  comments/comments.jsonl           # 扁平化评论列表
  comments/tree.json                # 带回复树的评论结构
  report.md                         # 人类可读的摘要
```

运行时文件（cookies、二维码）保存在 `runtime/`，日志保存在 `logs/`。

## 核心设计原则

**隐私优先的日志记录**
- 永远不记录 cookie 值、token、完整响应体、字幕/评论/弹幕内容
- 始终脱敏敏感的查询参数和请求头
- 在记录任何用户控制或 API 数据前使用 `sanitize_mapping_keys()` 和 `sanitize_url()`

**基于阶段的管道**
- 每个阶段都是独立的，可以通过 CLI 标志跳过（--skip-subtitles、--skip-comments 等）
- Manifest 记录每个阶段的状态/消息/计数
- 阶段失败会记录原因，但不会停止管道（除非是关键阶段如 video_info）

**速率限制**
- `--rate-limit`（默认 1.0 秒）控制 API 请求之间的延迟
- 在管道的各阶段之间实现，避免 412/503 错误

**结构化事件**
- 所有重要操作都通过 `log_event(event_type, message, level, **fields)` 发出结构化日志事件
- 事件类型遵循 `<模块>.<动作>` 命名：`pipeline.stage_start`、`client.request_success`、`extractors.player_success`
- 始终包含计时（`elapsed_ms`）和适用的计数

## 常见开发任务

**添加新阶段**
1. 在 `extractors.py` 中添加提取函数，包含开始/成功日志
2. 在 `pipeline.py` 的 `parse_video()` 中添加阶段逻辑
3. 调用 `_start_stage()` → 执行工作 → 调用 `_stage_ok()` 或 `_stage_missing()`
4. 在 `cli.py` 中添加 CLI 标志以跳过该阶段
5. 如需要新配置，更新 `ParseOptions`
6. 编写不进行真实网络调用的测试（mock API 响应）

**添加结构化日志**
- 使用 `log_event(event_type, message, level=logging.INFO, **extra_fields)`
- 在相关处包含 `elapsed_ms`、`bvid`、`stage`、`count`、`status`
- 使用 `sanitize_url()` 脱敏 URL，使用 `sanitize_mapping_keys()` 脱敏映射
- 永远不记录完整响应体或敏感内容

**无网络测试**
- 使用 `unittest.mock.patch` 来 mock `BiliClient` 方法
- 提供来自真实 API 响应的代表性 JSON payload
- 参考 `tests/test_core.py` 和 `tests/test_unittest.py` 中的示例
- 真实网络解析仅用于手动冒烟测试

## 重要约定

- 需要 Python 3.10+（使用 `|` 类型联合，`from __future__ import annotations`）
- 所有公开的 CLI 参数使用 kebab-case（--comment-pages、--rate-limit）
- Python 内部使用 snake_case
- 阶段名称使用小写下划线（video_info、danmaku、comments）
- 事件类型使用点表示法（pipeline.stage_start、client.request_retry）
- 分P文件命名：零填充的页码，如 `page_001.json`
- 字幕文件包含分P、字幕索引和语言代码：`page_001_0_zh-CN.jsonl`

## 隐私和安全要求

添加功能或修改代码时：
- 永远不要将敏感值写入日志、manifest 或报告
- 使用 `REDACTED = "<redacted>"` 常量作为占位符值
- 在 `logging_config.py` 中将新的敏感键添加到 `_SENSITIVE_EXACT_KEYS` 或 `_SENSITIVE_KEY_PARTS`
- 记录前始终脱敏：URL、查询参数、请求头、cookie jar
- 测试新的日志事件不会泄露凭据或 token

## API 集成注意事项

**WBI 签名** (`client.py`)
- `/x/player/wbi/v2` 和 `/x/player/wbi/playurl` 端点需要
- 从 nav API 获取 img_key/sub_key，生成 mixin_key，附加 w_rid（md5 哈希）
- 不要记录 `w_rid` 值（已脱敏）

**常见错误码**
- `-101`: not_logged_in（需要 `login` 命令的 cookies）
- `-352`、`-412`: risk_control（速率限制或反爬虫）
- `-503`、`-799`: rate_limited（增加 --rate-limit）
- `-688`、`-689`: geo_limited（地区限制）
- `62002`: video_invisible（已删除或私密视频）

**速率限制**
- 请求之间默认 1.0 秒
- 如果遇到 412 错误，使用 `--rate-limit 2.0` 增加间隔
- 客户端有基本的重试逻辑，但未实现指数退避

## 测试理念

- 单元测试永远不应进行真实网络请求
- 使用代表性 payload mock `BiliClient.get_json()` 和 `BiliClient.get_wbi_json()`
- 测试边缘情况：缺失字幕、空评论、单分P视频
- 验证 manifest 结构和阶段状态
- 真实解析仅用于手动验证（参见 CONTRIBUTING.md 中的冒烟测试 BV 号）
