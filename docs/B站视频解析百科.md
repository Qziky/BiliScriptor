# B站视频解析

> 本文档是一篇关于**哔哩哔哩（Bilibili）视频解析技术**的综合性百科条目，涵盖平台背景、技术原理、协议细节、工具生态与工程实践。

---

## 目录

1. [概述](#1-概述)
2. [平台背景](#2-平台背景)
3. [核心标识体系](#3-核心标识体系)
4. [鉴权与签名机制](#4-鉴权与签名机制)
5. [视频流协议](#5-视频流协议)
6. [弹幕系统](#6-弹幕系统)
7. [字幕体系](#7-字幕体系)
8. [评论系统](#8-评论系统)
9. [语音转写与OCR](#9-语音转写与ocr)
10. [工具生态](#10-工具生态)
11. [错误处理与风控](#11-错误处理与风控)
12. [合规边界](#12-合规边界)
13. [参考资料](#13-参考资料)

---

## 1. 概述

**B站视频解析**是指通过技术手段，从哔哩哔哩（Bilibili）平台提取视频相关结构化数据的过程。这一过程不仅限于下载视频文件本身，更完整的目标是：输入一个 B 站视频链接，尽可能结构化地提取视频的**元数据**、**分 P 信息**、**字幕**、**弹幕**、**评论**、**音频转写**和**画面文字**，并导出为可复用的数据文件或阅读型文档。

### 1.1 解析范围

一次完整的 B 站视频解析通常包含以下数据层次：

| 数据层次 | 内容 | 典型格式 |
|---|---|---|
| 元数据层 | 标题、UP主、播放量、标签、分区等 | JSON |
| 分 P 信息 | 多 P 视频的分集列表、CID、时长 | JSON |
| 字幕层 | 人工字幕、AI 字幕、Whisper 转写 | JSONL / SRT / ASS |
| 弹幕层 | 当前弹幕、历史弹幕 | JSONL / XML / Protobuf |
| 评论层 | 主评论、热评、楼中楼 | JSONL / JSON 树 |
| 媒体层 | 视频流、音频流 | MP4 / DASH / MP3 |
| 增强层 | OCR 画面文字、章节看点 | JSONL |

### 1.2 推荐输出结构

```
output/BVxxxx/
  manifest.json              # 本次解析状态、失败项、版本、配置
  video.json                 # 视频级元数据
  pages.json                 # 分 P、cid、标题、时长
  subtitles/page_1.jsonl     # 字幕事件流
  danmaku/page_1.jsonl       # 弹幕标准化事件流
  comments/comments.jsonl    # 扁平评论
  comments/tree.json         # 楼层树
  ocr/page_1.jsonl           # 画面文字事件
  report.md                  # 面向阅读的 Markdown
  report.docx                # 办公文档导出
```

**核心原则**：每一条内容都要能追溯来源、时间点、分 P、`cid` 和失败状态。

---

## 2. 平台背景

### 2.1 哔哩哔哩简介

**哔哩哔哩**（英语：Bilibili，NASDAQ: BILI，港交所: 9626）是中国大陆一个以 ACG（动画、漫画、游戏）内容起家的弹幕视频分享网站，通称 **B站**。网站于 2009 年 6 月 26 日由徐逸创建，初始名为 **Mikufans**，2010 年 1 月 24 日更名为 "bilibili"，名称源自动漫《魔法禁书目录》中角色御坂美琴的昵称。

截至 2025 年，B站已发展为涵盖 7000 多个兴趣圈层的多元文化社区，月活跃用户超过 3 亿。2018 年 3 月 28 日在纳斯达克上市，2021 年 3 月 29 日在香港联交所二次上市。

### 2.2 技术架构特点

B站视频系统具有以下技术特点，直接影响解析方案的设计：

- **弹幕系统**：B站标志性的实时评论系统，支持滚动、顶部、底部等多种模式，现采用 Protobuf 二进制格式传输
- **多 P 架构**：单个视频稿件可包含多个分 P（分集），每个分 P 拥有独立的 `cid`
- **多清晰度流**：支持从 360P 到 8K、HDR、杜比视界的多种视频流
- **Wbi 风控签名**：Web 端查询接口采用动态签名机制防止滥用
- **内容分级**：部分高清内容需要登录或大会员权限

### 2.3 关键时间线

| 时间 | 事件 |
|---|---|
| 2009-06-26 | 徐逸创建 Mikufans |
| 2010-01-24 | 更名为 bilibili |
| 2011 | 引入 UP 主制度 |
| 2013-05-20 | 改为注册答题制 |
| 2014 | 启动正版化转型 |
| 2018-03-28 | 纳斯达克上市 |
| 2020-09-15 | 发射"哔哩哔哩视频卫星" |
| 2021-03-29 | 港交所二次上市 |
| 2023-03 | Web 端开始采用 Wbi 签名 |
| ~2023 | 弹幕格式从 XML 迁移至 Protobuf |

---

## 3. 核心标识体系

B站视频系统使用多层级标识符体系，理解这一体系是正确解析的基础。

### 3.1 bvid（BV 号）

`bvid` 是 B站当前主流的视频稿件标识符，格式为 `BV` 开头的字符串（如 `BV1uv411q7Mv`）。BV 号于 2020 年 3 月引入，用于替代原有的纯数字 AV 号，具有更好的混淆性和防爬性。

BV 号与 AV 号之间存在确定性的互转算法，`bilibili-api` 等工具库均提供此功能。

### 3.2 aid（AV 号）

`aid` 是 B站早期使用的视频稿件 ID，为纯数字格式。虽然新视频不再使用 AV 号作为主标识，但大量接口仍然兼容 aid，且许多内部 API 仍以 aid 作为主键。

### 3.3 cid（内容 ID）

`cid` 是分 P 或具体播放单元的标识符，是获取字幕、弹幕、播放地址等数据的**核心依赖**。一个视频稿件（bvid/aid）可以对应多个 cid：

- 单 P 视频：1 个 bvid → 1 个 cid
- 多 P 视频：1 个 bvid → 多个 cid（每个分 P 各一个）
- 互动视频：可能有额外的剧情图版本标识

> **重要**：解析时不能只保存 BV 号。多 P 视频、合集、番剧或活动页都可能让"一个链接等于一个播放单元"的假设失效。

### 3.4 其他标识

| 标识 | 说明 |
|---|---|
| `mid` | 用户 ID（UP 主编号） |
| `ep_id` | 番剧剧集 ID |
| `ss_id` | 番剧季度 ID |
| `oid` | 弹幕/评论对象 ID（通常等于 cid 或 aid） |
| `rpid` | 评论 ID |

---

## 4. 鉴权与签名机制

B站接口采用多种鉴权机制，不同接口、不同时期的鉴权方式有所不同。

### 4.1 鉴权体系概览

| 鉴权类型 | 适用场景 | 特点 |
|---|---|---|
| Cookie 鉴权 | Web 端大部分接口 | 依赖 SESSDATA 等 Cookie 字段 |
| Wbi 签名 | Web 端查询类接口 | 动态签名，每日更替 key |
| App 签名 | 旧版开放 API | appkey + ts + sign，已较少使用 |
| 设备标识 | 风控辅助 | buvid3/buvid4 等 |

### 4.2 Wbi 签名

自 2023 年 3 月起，B站 Web 端部分接口开始采用 **WBI 签名鉴权**。这是目前最主要的 Web 端风控手段，表现为请求参数中添加 `w_rid` 和 `wts` 字段。

#### 4.2.1 签名算法

Wbi 签名的核心流程如下：

```
1. 获取实时口令 img_key、sub_key
2. 拼接并打乱重排获得 mixin_key（32 位）
3. 添加 wts（当前 Unix 秒级时间戳）
4. 参数按 key 升序排序
5. URL 编码后拼接 mixin_key
6. 计算 MD5 得到 w_rid
```

#### 4.2.2 获取实时口令

从 `https://api.bilibili.com/x/web-interface/nav` 接口返回的 `wbi_img` 字段中获取：

```json
{
  "data": {
    "wbi_img": {
      "img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
      "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png"
    }
  }
}
```

截取文件名部分（去掉路径和扩展名）即为 `img_key` 和 `sub_key`。注意这些 URL 实际上是伪装成图片的 Token，无需也不能访问。

**关键特性**：
- `img_key` 和 `sub_key` 全站统一使用
- 观测表明为**每日更替**
- 未登录状态也可获取
- 建议做好缓存和定时刷新

#### 4.2.3 生成 mixin_key

将 `sub_key` 拼接在 `img_key` 后面，通过固定的重排映射表（64 位）打乱字符顺序，截取前 32 位：

```python
mixinKeyEncTab = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52
]

def getMixinKey(orig: str) -> str:
    return ''.join(orig[i] for i in mixinKeyEncTab)[:32]
```

#### 4.2.4 计算签名

```python
import time, hashlib, urllib.parse

def encWbi(params: dict, img_key: str, sub_key: str) -> dict:
    mixin_key = getMixinKey(img_key + sub_key)
    params['wts'] = round(time.time())
    params = dict(sorted(params.items()))
    # 过滤 value 中的 "!'()*" 字符
    params = {k: ''.join(c for c in str(v) if c not in "!'()*") for k, v in params.items()}
    query = urllib.parse.urlencode(params)
    w_rid = hashlib.md5((query + mixin_key).encode()).hexdigest()
    params['w_rid'] = w_rid
    return params
```

#### 4.2.5 注意事项

- 参数值含中文或特殊字符时，编码字母应当**大写**，空格应编码为 `%20`（不是 `+`）
- 缺失或错误的 Wbi 参数可能返回 `v_voucher`，这应被识别为签名/风控问题
- Wbi 签名独立于 Cookie 登录态，两者不可混淆

### 4.3 Cookie 鉴权

B站 Web 端大量功能依赖 Cookie 鉴权，关键 Cookie 字段包括：

| Cookie 字段 | 作用 | 安全等级 |
|---|---|---|
| `SESSDATA` | 登录会话标识 | 高（可访问登录态功能） |
| `bili_jct` | CSRF Token | 高（仅写操作需要） |
| `DedeUserID` | 用户唯一标识 | 中 |
| `buvid3` | 设备指纹标识 | 低 |
| `buvid4` | 新版设备指纹 | 低 |
| `bili_ticket` | Web ticket | 低（可降低风控概率） |

**安全原则**：`SESSDATA`、`bili_jct`、`DedeUserID` 等敏感字段永不进入日志、报告或错误输出。

### 4.4 设备标识与风控

B站使用设备指纹标识（buvid3、buvid4）进行风控辅助。获取方式：

1. **buvid3**：可从 `x/frontend/finger/spi` 接口返回，或由浏览器 Cookie 自然携带
2. **buvid4**：同上接口返回，为新版标识
3. **bili_ticket**：从相关接口获取，可降低部分 Web 请求的风控概率

这些标识可作为稳定性辅助，但不能当作绕过权限的手段。工程上建议模拟"正常浏览器会自然携带的信息"。

### 4.5 旧版 App 签名

早期开放 API 使用 `appkey + ts + sign` 机制：将参数按名称排序、URL 编码后拼接 App Secret，再做 MD5。这套机制在当前 Web 端已不常用，但在理解历史接口时仍有参考价值。

---

## 5. 视频流协议

### 5.1 流格式演进

B站视频流格式经历了多次演进：

| 时期 | 格式 | 状态 |
|---|---|---|
| 早期 | FLV | 已下线，不应作为新实现主路径 |
| 过渡期 | MP4（durl） | 低分辨率或老视频仍保留 |
| 当前主流 | DASH | 新视频的高分辨率通常走 DASH |

### 5.2 DASH 协议

**DASH**（Dynamic Adaptive Streaming over HTTP）是 B站当前主流的视频流格式。与传统 MP4 不同，DASH 模式下视频流和音频流是分离的，需要分别下载后合并。

#### 5.2.1 请求接口

```
GET https://api.bilibili.com/x/player/wbi/playurl
  ?bvid=BVxxxx
  &cid=123456
  &fnval=4048        # 请求 DASH 格式
  &fnver=0
  &fourk=1           # 允许 4K
  &qn=80             # 期望清晰度
  &wts=...
  &w_rid=...
```

#### 5.2.2 关键参数

| 参数 | 作用 | 典型值 | 说明 |
|---|---|---|---|
| `bvid` / `avid` | 视频标识 | BV1xx411c7mD | 二选一 |
| `cid` | 分 P 标识 | 123456 | 必填 |
| `qn` | 期望清晰度 | 16/32/64/80/112/116/120 | DASH 模式下影响返回流列表 |
| `fnval` | 流格式能力位 | 4048 | 组合标志位，拉取更多 DASH 候选 |
| `fnver` | 格式版本 | 0 | 通常为 0 |
| `fourk` | 是否允许 4K | 0/1 | 需要高分辨率时设为 1 |
| `platform` | 平台标识 | pc/html5 | 影响返回流类型 |

#### 5.2.3 清晰度枚举

| qn 值 | 含义 |
|---|---|
| 16 | 流畅 360P |
| 32 | 清晰 480P |
| 64 | 高清 720P |
| 80 | 高清 1080P |
| 112 | 高码率 1080P+ |
| 116 | 高帧率 1080P60 |
| 120 | 超高清 4K |
| 125 | HDR |
| 126 | Dolby Vision |
| 127 | 8K |

**注意**：获取 720P 及以上、高帧率、高码率、HDR、杜比、8K 等资源可能需要登录或大会员。

#### 5.2.4 编码格式

| codecid | 编码 | 特点 |
|---|---|---|
| 7 | AVC / H.264 | 兼容性最好 |
| 12 | HEVC / H.265 | 同清晰度体积更小 |
| 13 | AV1 | 压缩效率最高 |

#### 5.2.5 返回结构

DASH 响应包含分离的视频流和音频流：

```json
{
  "data": {
    "dash": {
      "video": [
        {
          "id": 80,
          "codecid": 7,
          "baseUrl": "...",
          "backupUrl": [],
          "bandwidth": 2000000,
          "width": 1920,
          "height": 1080
        }
      ],
      "audio": [
        {
          "id": 30280,
          "baseUrl": "...",
          "bandwidth": 320000
        }
      ]
    }
  }
}
```

**重要限制**：
- 视频流 URL 有有效期（约 120 分钟），不能长期缓存
- 多 P 视频换 P 时必须用对应 `cid` 重新获取流地址
- Web 端取流接口需要 Wbi 签名

### 5.3 播放器信息接口

`x/player/wbi/v2` 接口提供播放器运行时状态信息：

```
GET https://api.bilibili.com/x/player/wbi/v2?bvid=BVxxxx&cid=123456
```

高价值字段包括：

| 字段 | 价值 |
|---|---|
| `subtitle.subtitles[]` | 字幕文件列表 |
| `view_points[]` | 分段章节、看点信息 |
| `dm_mask.mask_url` | 智能防挡弹幕资源 |
| `interaction.graph_version` | 互动视频剧情图版本 |

---

## 6. 弹幕系统

### 6.1 弹幕概述

**弹幕**（danmaku）是 B站标志性的实时评论系统，用户发送的文字会以滚动、固定等方式叠加在视频画面上。B站弹幕系统经历了从 XML 到 Protobuf 的格式迁移。

### 6.2 弹幕格式

| 时期 | 格式 | 接口 |
|---|---|---|
| 旧版 | XML | `https://comment.bilibili.com/{cid}.xml` |
| 当前 | Protobuf | `https://api.bilibili.com/x/v2/dm/web/seg.so` |

#### 6.2.1 XML 格式（旧版）

旧版弹幕以 XML 格式存储，每条弹幕包含以下属性：

```xml
<d p="65.68300,1,25,16777215,1710000000,0,xxxxxxxx,123456">弹幕内容</d>
```

`p` 属性格式：`出现时间,模式,字号,颜色,发送时间,池,用户Hash,弹幕ID`

#### 6.2.2 Protobuf 格式（当前）

B站于约 2023 年将弹幕格式从 XML 迁移至 Protobuf。Protocol Buffers 是 Google 开发的二进制序列化格式，相比 XML 更小、更快。

请求接口：

```
GET https://api.bilibili.com/x/v2/dm/web/seg.so
  ?type=1
  &oid={cid}
  &pid={avid}
  &segment_index=1
```

返回数据需要使用 `.proto` 定义文件反序列化，核心消息结构：

```protobuf
message DmSegMobileReply {
  repeated DanmakuElem elems = 1;
}

message DanmakuElem {
  int64 id = 1;
  int32 progress = 2;      // 弹幕出现时间（毫秒）
  int32 mode = 3;          // 弹幕模式
  int32 fontsize = 4;      // 字号
  int32 color = 5;         // 颜色（十进制 RGB）
  string content = 6;      // 弹幕文本
  int64 ctime = 7;         // 发送时间戳
  // ... 其他字段
}
```

### 6.3 弹幕模式

| mode 值 | 含义 |
|---|---|
| 1 | 普通滚动弹幕 |
| 4 | 底部弹幕 |
| 5 | 顶部弹幕 |
| 6 | 逆向弹幕 |
| 7 | 特殊/高级弹幕 |
| 9 | 高级或 BAS 相关弹幕 |

### 6.4 当前弹幕与历史弹幕

- **当前弹幕**：当前视频挂载的弹幕，按 cid 获取
- **历史弹幕**：历史日期的弹幕快照，通常需要登录态，按日期遍历获取

历史弹幕接口示例：
```
GET https://api.bilibili.com/x/v2/dm/history/index?type=1&oid={cid}&month=2024-01
```

### 6.5 弹幕导出

弹幕可以额外导出为 ASS 格式用于播放器展示或烧录视频。`yt-dlp-danmaku` 插件提供了此功能：

```bash
yt-dlp --embed-subs --use-postprocessor danmaku --remux-video mkv <URL>
```

但 ASS 是展示格式，不适合作为主数据格式存储。

---

## 7. 字幕体系

### 7.1 字幕来源

B站视频的字幕有多种来源：

| 优先级 | 来源 | 说明 |
|---|---|---|
| 1 | 人工字幕 | UP 主或用户上传，质量最高 |
| 2 | B站 AI 字幕 | 平台自动生成，覆盖较广 |
| 3 | 本地 ASR 转写 | 使用 Whisper 等工具从音频生成 |

### 7.2 字幕获取

字幕信息通过 `x/player/wbi/v2` 接口的 `subtitle.subtitles[]` 字段获取：

```json
{
  "subtitle": {
    "list": [
      {
        "lan": "zh-CN",
        "lan_doc": "中文（中国）",
        "subtitle_url": "https://i0.hdslb.com/bfs/subtitle/xxx.json",
        "type": 0,
        "ai_type": 0,
        "ai_status": 0
      }
    ]
  }
}
```

字段说明：
- `ai_type` / `ai_status`：标识是否为 AI 生成字幕
- `type`：0 为普通字幕
- `subtitle_url`：字幕文件的实际 URL

### 7.3 字幕状态

不要把"接口返回空"直接当成"视频没有字幕"。更完整的状态标记：

| 状态 | 含义 |
|---|---|
| `subtitle_found` | 成功获取字幕 |
| `subtitle_missing` | 视频确实没有字幕 |
| `login_required` | 需要登录才能获取字幕 |
| `fallback_asr` | 回退到语音转写 |
| `subtitle_fetch_failed` | 获取字幕失败 |

### 7.4 字幕事件模型

每条字幕片段建议保存以下结构：

```json
{
  "source": "official | ai | whisper",
  "page_index": 1,
  "cid": 123456,
  "start_ms": 1000,
  "end_ms": 5200,
  "text": "字幕内容",
  "language": "zh-CN",
  "confidence": "high"
}
```

---

## 8. 评论系统

### 8.1 评论层级

B站评论系统包含多个层级：

- **主评论**：视频下的顶级评论
- **热评**：按热度排序的评论
- **楼中楼**：主评论下的回复（二级评论）
- **评论回复的回复**：更深层级

### 8.2 评论接口

```
GET https://api.bilibili.com/x/v2/reply
  ?oid={aid}
  &type=1
  &pn={page}
  &sort=2
```

### 8.3 评论对象模型

```json
{
  "rpid": "评论 ID",
  "root": "根评论 ID",
  "parent": "父评论 ID",
  "mid": 10000,
  "uname": "用户昵称",
  "message": "评论内容",
  "like": 10,
  "ctime": 1710000000,
  "floor": 12,
  "reply_count": 3,
  "state": "normal"
}
```

### 8.4 评论状态

评论可能处于多种状态：

| 状态 | 含义 |
|---|---|
| `normal` | 正常显示 |
| `hidden_by_up` | 被 UP 主隐藏 |
| `deleted_by_admin` | 被管理员删除 |
| `deleted_by_report` | 被举报删除 |

### 8.5 导出建议

建议同时导出两种结构：

1. **`comments.jsonl`**：一行一条评论，便于检索、统计、词云、情感分析
2. **`tree.json`**：保留楼层树，便于还原评论区上下文

---

## 9. 语音转写与OCR

### 9.1 语音转写（ASR）

当视频没有字幕或字幕质量不佳时，可通过语音识别生成转写文本。

#### 9.1.1 推荐流程

```
视频链接 → yt-dlp 获取音频 → FFmpeg 转 wav/mp3 → Whisper 转写 → 统一字幕轨
```

#### 9.1.2 Whisper 模型选择

| 模型 | 参数量 | 适用场景 |
|---|---|---|
| `base` | 74M | 快速测试 |
| `small` | 244M | 一般用途 |
| `medium` | 769M | 中文内容，质量较好 |
| `large-v3` | 1550M | 最高质量 |
| `turbo` | 809M | 速度与质量平衡 |

Whisper 是 OpenAI 开发的自动语音识别系统，基于 68 万小时多语言数据训练，在口音、背景噪音及专业术语方面具有较好的稳健性。

### 9.2 画面文字识别（OCR）

对于知识类、教程类、PPT 类、代码演示类视频，画面文字是重要的信息源。

#### 9.2.1 推荐流程

```
视频文件 → FFmpeg 抽帧 → PaddleOCR 识别 → 相邻帧去重 → OCR 时间轴
```

#### 9.2.2 抽帧策略

| 模式 | 间隔 | 适用场景 |
|---|---|---|
| 快速模式 | 每 3~5 秒 | 一般视频 |
| 精细模式 | 固定 + 场景切换补样 | 内容变化频繁的视频 |
| PPT/教程模式 | 较长间隔 | 需配合文本去重 |

#### 9.2.3 PaddleOCR

**PaddleOCR** 是百度开源的智能文档解析与文字识别工具，支持多语言识别与手写体识别。其特点包括：

- 支持 80+ 语言的文字识别
- 提供轻量级模型，适合端侧部署
- 可转换为 ONNX 格式，推理速度提升 4~5 倍

**关键要求**：OCR 必须做去重，否则同一页 PPT 会在几十帧里重复出现，导致文档严重膨胀。

---

## 10. 工具生态

### 10.1 核心工具一览

| 工具 | 类型 | 语言 | 主要用途 |
|---|---|---|---|
| [bilibili-api](https://github.com/Nemo2011/bilibili-api) | SDK | Python | API 调用底座，覆盖视频、直播、用户等 |
| [bilibili-API-collect](https://github.com/SocialSisterYi/bilibili-API-collect) | 文档 | - | 野生 API 接口文档 |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | 下载器 | Python | 视频/音频下载，站点兼容回退 |
| [yt-dlp-danmaku](https://github.com/UlyssesZh/yt-dlp-danmaku) | 插件 | Python | 弹幕转 ASS |
| [FFmpeg](https://ffmpeg.org/) | 媒体处理 | C | 抽音频、抽帧、格式转换 |
| [Whisper](https://github.com/openai/whisper) | ASR | Python | 语音识别转写 |
| [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) | OCR | Python | 画面文字识别 |
| [Bilidown](https://github.com/iuroc/bilidown) | 桌面工具 | Go | 支持 8K、杜比视界的下载工具 |

### 10.2 bilibili-api

`bilibili-api`（包名 `bilibili-api-python`）是目前最活跃的 B站 Python SDK，截至 2026 年 6 月版本为 v17.4.2。

**历史**：模块最早由 @MoyuScript 于 2020 年创建，2022 年停止维护。现仓库由 Nemo2011 fork 并持续开发，遵循 GPL v3 协议。

**特性**：
- 全部异步操作
- 支持 `aiohttp` / `httpx` / `curl_cffi` 三种请求库
- 全面支持 BV 号和 AV 号
- 内置 Credential 管理、Cookie 刷新
- 覆盖视频、音频、直播、动态、专栏、用户、番剧等模块

**安装**：
```bash
pip install bilibili-api-python    # 稳定版
pip install bilibili-api-dev       # 开发版
```

**基本用法**：
```python
import asyncio
from bilibili_api import video

async def main():
    v = video.Video(bvid="BV1uv411q7Mv")
    info = await v.get_info()
    print(info)

asyncio.run(main())
```

**请求库切换**：
```python
from bilibili_api import select_client
select_client("curl_cffi")   # 支持伪装浏览器 TLS/JA3
select_client("aiohttp")     # 默认
select_client("httpx")       # 不支持 WebSocket
```

### 10.3 bilibili-API-collect

`SocialSisterYi/bilibili-API-collect` 是社区维护的 B站野生 API 文档仓库，截至 2025 年已获得超过 19,000 Stars。文档涵盖：

- 登录与鉴权（Cookie、Wbi、App 签名）
- 视频信息与流地址
- 弹幕协议（XML 与 Protobuf）
- 评论接口
- 直播、番剧、用户等模块
- 设备标识与风控机制

此外，`pskdje/bilibili-API-collect` 是上游仓库的归档增强版，可作为备份源和快照源使用。

### 10.4 yt-dlp

`yt-dlp` 是功能强大的命令行媒体下载器，支持包括 B站在内的数百个网站。其 B站支持包括：

- 视频/音频下载
- 字幕提取（需登录）
- 弹幕下载（XML 格式）
- 自动 Wbi 签名
- 多清晰度选择

```bash
# 下载最佳质量视频
yt-dlp -f "bestvideo+bestaudio" https://www.bilibili.com/video/BVxxxx

# 下载并嵌入弹幕（需安装 yt-dlp-danmaku 插件）
yt-dlp --embed-subs --use-postprocessor danmaku --remux-video mkv <URL>

# 仅下载音频
yt-dlp -f "bestaudio" -x --audio-format mp3 <URL>
```

### 10.5 工具选择建议

| 场景 | 推荐工具 | 原因 |
|---|---|---|
| Python 解析器开发 | bilibili-api | 已封装常用能力，减少维护成本 |
| 核对接口参数 | bilibili-API-collect | 接口覆盖面广 |
| 快速下载视频 | yt-dlp | 成熟稳定，支持多站点 |
| 无字幕视频转写 | yt-dlp + Whisper | 音频获取 + 语音识别 |
| 教程视频 OCR | FFmpeg + PaddleOCR | 抽帧 + 文字识别 |

---

## 11. 错误处理与风控

### 11.1 错误码体系

B站接口的错误通过 JSON 响应中的 `code` 字段返回。常见错误码：

| 归一化状态 | 常见 code | 含义 | 处理建议 |
|---|---|---|---|
| `not_logged_in` | `-101` | 未登录 | 提示需要 Cookie |
| `csrf_failed` | `-111` | CSRF 验证失败 | 检查是否误调用 POST |
| `bad_request` | `-400` | 请求参数错误 | 检查 bvid/cid/签名 |
| `permission_denied` | `-403` | 权限不足 | 不重试，标记权限不足 |
| `not_found` | `-404` | 资源不存在 | 标记资源已失效 |
| `risk_control` | `-352`, `-412` | 风控拦截 | 降频、刷新签名 |
| `rate_limited` | `-503`, `-799` | 请求过快 | 指数退避重试 |
| `geo_limited` | `-688`, `-689` | 地区/版权限制 | 不尝试绕过 |
| `video_invisible` | `62002` 等 | 稿件不可见 | 标记审核中或仅 UP 可见 |

### 11.2 v_voucher 响应

当 Wbi 签名缺失或错误时，接口可能返回 `v_voucher`：

```json
{"code": 0, "message": "0", "data": {"v_voucher": "voucher_xxxxx"}}
```

这应被识别为签名/风控问题，排查方向：刷新 Wbi key、检查 User-Agent/Referer/Cookie。

### 11.3 限速策略

B站接口可能因请求过快触发风控（HTTP 412 Precondition Failed）。建议：

- 全局最小请求间隔（建议 0.5~1 秒）
- 评论分页使用更慢的节奏
- 历史弹幕按日期遍历时必须限速
- 并发只用于互不相关的任务

即使使用异步，也不能把异步理解成无限并发。推荐"异步 + 队列 + 令牌桶限速"模式。

### 11.4 重试策略

**只对幂等读取请求重试**。以下情况不应盲目重试：

- 未登录（`-101`）
- 权限不足（`-403`）
- 会员限制
- 地区限制
- 视频不存在
- 私密视频

### 11.5 部分成功

解析器应允许部分成功：字幕失败不影响元数据和评论，评论失败不影响弹幕，某个分 P 失败不影响其他分 P。所有失败应写入 `manifest.json`。

---

## 12. 合规边界

### 12.1 基本原则

B站视频解析工具应定位为**个人研究、个人归档、内部分析工具**，必须明确以下边界：

**禁止行为**：
- 不绕过付费、会员、地区、版权、私密限制
- 不做密码登录、短信登录
- 不保存明文 Cookie 到日志或报告
- 不默认站点级批量抓取
- 不提供规避验证码、破解风控、绕过权限的能力
- 不将下载的视频、字幕、评论用于未授权再分发

### 12.2 工程约束

| 约束 | 说明 |
|---|---|
| 默认低频 | 请求间隔不应低于 0.5 秒 |
| 默认本地 | 数据存储在本地，不上传第三方 |
| 默认脱敏 | 敏感字段不出现在日志和报告 |
| 默认不批量 | 单视频解析，非站点级爬取 |

---

## 13. 参考资料

### 13.1 官方与社区文档

| 资料 | URL | 说明 |
|---|---|---|
| bilibili-API-collect | https://github.com/SocialSisterYi/bilibili-API-collect | 社区 API 文档（主仓库） |
| bilibili-API-collect (归档) | https://github.com/pskdje/bilibili-API-collect | 归档增强版 |
| BAC Wbi 签名文档 | https://github.com/SocialSisterYi/bilibili-API-collect/blob/master/docs/misc/sign/wbi.md | Wbi 签名详解 |
| BAC 视频流文档 | https://github.com/SocialSisterYi/bilibili-API-collect/blob/master/docs/video/videostream_url.md | 视频流 URL |
| BAC 公共错误码 | https://github.com/SocialSisterYi/bilibili-API-collect/blob/master/docs/misc/errcode.md | 错误码参考 |
| BAC 设备标识文档 | https://github.com/SocialSisterYi/bilibili-API-collect/blob/master/docs/misc/buvid3_4.md | buvid3/buvid4 |

### 13.2 工具仓库

| 工具 | URL |
|---|---|
| bilibili-api (Python SDK) | https://github.com/Nemo2011/bilibili-api |
| yt-dlp | https://github.com/yt-dlp/yt-dlp |
| yt-dlp-danmaku | https://github.com/UlyssesZh/yt-dlp-danmaku |
| Bilidown | https://github.com/iuroc/bilidown |
| PaddleOCR | https://github.com/PaddlePaddle/PaddleOCR |
| Whisper | https://github.com/openai/whisper |

### 13.3 旧版文档

| 资料 | URL | 用途 |
|---|---|---|
| fython/BilibiliAPIDocs | https://github.com/fython/BilibiliAPIDocs | 旧版开放接口文档，历史字段参考 |
| BilibiliAPIDocs 视频信息 | https://github.com/fython/BilibiliAPIDocs/blob/master/API.view.md | 旧版视频接口 |
| BilibiliAPIDocs 弹幕 | https://github.com/fython/BilibiliAPIDocs/blob/master/API.comment.md | 旧版弹幕字段 |

### 13.4 百科参考

- [哔哩哔哩 - 维基百科](https://zh.wikipedia.org/zh-hans/%E5%93%94%E5%93%A9%E5%93%94%E5%93%A9)
- [Bilibili - Wikipedia](https://en.wikipedia.org/wiki/Bilibili)
- [Bilibili - MBA智库百科](https://wiki.mbalib.com/wiki/Bilibili)

---

> **最后更新**：2026 年 6 月
>
> 本文档仅供学习和研究使用，请遵守相关法律法规和平台服务条款。
