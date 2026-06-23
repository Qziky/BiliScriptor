# bili-md

本地 B 站视频全解析 CLI。输入一个 B 站视频链接或 BV 号，导出可追溯的数据包：元数据、分 P、字幕、当前弹幕、评论、播放流候选和阅读型报告。

## 使用

```bash
python -m bili_md login
python -m bili_md parse "https://www.bilibili.com/video/BV1QEVY6jEYv/"
python -m bili_md report output/BV1QEVY6jEYv
```

安装为命令行工具后也可以使用：

```bash
bili-md login
bili-md parse BV1QEVY6jEYv
```

默认参数：

- `--cookie-file bilibili_cookies.txt`
- `--output-dir output`
- `--comment-pages 1`
- `--reply-pages 1`
- `--rate-limit 1.0`
- `--all-comments` 显式开启更多评论抓取

默认不会下载视频或音频文件，只保存流候选信息。ASR、OCR、抽帧 LLM 分析保留为后续插件扩展。

## 输出

```text
output/BVxxxx/
  manifest.json
  video.json
  pages.json
  player/page_001.json
  streams/page_001.json
  subtitles/page_001_<index>_<lang>.jsonl
  subtitles/page_001_<index>_<lang>.srt
  danmaku/page_001.current.jsonl
  comments/comments.jsonl
  comments/tree.json
  report.md
```

`manifest.json` 会记录每个阶段的 `ok/missing/skipped/failed` 状态和失败项。Cookie 敏感值不会写入日志、报告或 manifest。

## 测试

```bash
python -m unittest discover -s tests
```
