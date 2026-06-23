# 贡献指南

感谢你愿意改进 BiliScriptor。这个项目优先保持本地优先、可排障、可二次处理和隐私友好。

## 本地开发

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
python -m biliscriptor --help
```

常用开发命令：

```bash
python -m biliscriptor parse BV1QEVY6jEYv
python -m biliscriptor report output/BV1QEVY6jEYv
python -m biliscriptor login
```

真实网络解析只作为手动烟测，不应写入自动化测试：

```bash
python -m biliscriptor parse "https://www.bilibili.com/video/BV11kVt6sEWA?"
```

## 测试

提交前请至少运行：

```bash
python -m pytest
python -m unittest discover -s tests
```

测试应避免真实网络调用。涉及 Bilibili API 的逻辑请使用 mock、fake opener 或代表性 payload。

## 日志与隐私

开发新功能或修改关键流程时，应同步补充结构化日志事件，方便定位阶段状态、耗时、计数、请求和文件写入问题。

日志可以记录：

- URL 路径和查询参数键
- 状态码、阶段状态、耗时、计数
- 文件路径、输出字节数、脱敏后的异常栈
- cookie 名称

日志不能记录：

- cookie 值、`SESSDATA`、`bili_jct`、`DedeUserID`
- `qrcode_key`、`csrf`、`token`、`w_rid`
- WBI 签名、完整响应体
- 字幕正文、评论正文、弹幕正文

## Pull Request

PR 请说明：

- 修改目的和主要行为变化
- 受影响的 CLI 命令
- 测试结果
- 是否影响日志、manifest、report 或隐私承诺

不要提交 `runtime/`、`logs/`、`output/`、cookie 文件或真实解析产物。
