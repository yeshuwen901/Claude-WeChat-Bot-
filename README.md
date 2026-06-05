# Claude WeChat Bot

基于腾讯 ilink 官方 API 的微信 AI 机器人。扫码登录，支持 DeepSeek / Anthropic / DashScope 等多种模型，带有 Web 管理面板。

## 功能一览

| 模块 | 功能 |
|------|------|
| **AI 对话** | 多轮记忆、自定义人设、每用户独立 Prompt、URL 内容抓取 |
| **主动聊天** | 定时检测不活跃联系人，AI 自动发起话题（可配置冷却时间、最大静默次数、空闲阈值） |
| **主动分享** | 定时随机选题 + 联网搜索，向随机联系人分享趣事 |
| **Bot 情绪** | 0-100 情绪值，用户夸奖/批评自动调整，随时间缓慢衰减，影响回复语气 |
| **个人经历** | 可配置故事库 + 触发关键词，对话触及话题时自然融入 |
| **话题延续** | 记住上次主动聊天的主题，下次可自然衔接 |
| **表情包** | 本地图片 CDN 上传发送、6 种情绪自动匹配、关键词+AI 双引擎检测、文件名精确命中 |
| **语音回复** | Edge TTS 语音合成、CDN 加密上传、语音/文件双模式 |
| **管理面板** | Web UI 配置（http://localhost:8080）、API Key 在线管理、表情包管理、启动自动打开浏览器 |
| **联网搜索** | AI 可主动搜索互联网（DuckDuckGo）获取最新信息 |
| **定时消息** | Cron 定时广播、动态增删改 |
| **休息时间** | 多时间段 + 按星期过滤 |
| **安全控制** | 白名单（用户/群聊）、频率限制、auto-reply 开关 |
| **运维** | 定时重启、记忆评分裁剪、自动重登录、API Key 加密存储 |
| **输出控制** | 用户可配置回复字数上限，prompt + max_tokens 双保险 |

## 快速开始

### 环境要求

- Python 3.11+
- Windows / macOS / Linux

### 一键部署

**Windows：** 双击 `setup.bat`
**macOS/Linux：** `./setup.sh`

脚本会自动完成：Python 版本检查 -> 虚拟环境 -> 依赖安装 -> 配置模板 -> 示例表情包生成。

### 手动部署

```bash
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # 编辑 .env 填入 API Key
python src/main.py
```

### 启动

```bash
./start.sh    # macOS/Linux
start.bat     # Windows
```

### 扫码登录

启动后终端会打印登录链接，用微信扫码确认授权。登录凭证自动保存，下次启动无需重新扫码。

### 管理面板

浏览器打开 `http://localhost:8080`：
- 填写 API Key（支持 Anthropic / DashScope 双 Key）
- 编辑机器人人设
- 管理表情包和情绪标签
- 设置定时消息、休息时间
- 配置 Bot 情绪、主动聊天、主动分享、个人经历
- AI 对话测试（无需发微信消息）
- 获取登录二维码

## 表情包

将图片放入 `data/stickers/{情绪}/` 目录：

```
data/stickers/
├── happy/        # 开心
├── sad/          # 伤心
├── angry/        # 生气
├── love/         # 爱
├── surprised/    # 惊讶
└── neutral/      # 中性
```

支持 PNG / JPG / GIF / WebP。

**情绪检测（双引擎）：**
1. 关键词匹配 —— 快速路径，命中即返回
2. DeepSeek AI 兜底 —— 理解反讽、安慰、上下文，处理复杂情绪

**表情包选择：**
- 文件名精确匹配：bot 回复中的词汇与文件名（无扩展名）匹配时，优先发送该表情包
- 最长匹配优先：多个命中时选最精确的
- 随机兜底：无命中时从情绪文件夹随机抽取

## 项目结构

```
src/
├── main.py             # 入口
├── wechat_bot.py       # 核心：消息处理、情绪检测、表情包
├── wechat_api.py       # ilink API：登录、轮询、发送
├── ai_client.py        # AI 对话客户端（含情绪分类、主动聊天、主动分享）
├── voice.py            # CDN 上传、语音/图片消息
├── config.py           # 环境变量配置
├── config_service.py   # 动态配置服务（1秒缓存，运行时热更新）
├── crypto_utils.py     # API Key 加密/解密
├── database.py         # SQLite 数据层（对话、配置、表情包、主动聊天状态）
├── conversation.py     # 对话管理
├── middleware.py        # 白名单、限流
├── scheduler.py        # 定时任务（表情衰减、主动聊天、主动分享、定时重启）
├── memory_scorer.py    # 记忆评分裁剪
├── url_fetcher.py      # URL 内容抓取
├── web_search.py       # DuckDuckGo 联网搜索
├── admin_api.py        # 管理 API（FastAPI）
└── static/admin.html   # 管理面板 UI
data/
├── conversations.db    # SQLite 数据库
├── bot.log             # 运行日志
├── stickers/           # 本地表情包目录
└── REQUIREMENTS_*.md   # 需求文档
```

## 环境变量配置

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `ANTHROPIC_API_KEY` | (可选) | DeepSeek 或 Anthropic API Key，可留空通过管理面板设置 |
| `DASHSCOPE_API_KEY` | (可选) | DashScope API Key，可留空通过管理面板设置 |
| `ANTHROPIC_MODEL` | deepseek-v4-pro | 模型名称 |
| `ANTHROPIC_MAX_TOKENS` | 4096 | 最大输出 token 数 |
| `ANTHROPIC_TEMPERATURE` | 0.7 | 生成温度 |
| `ANTHROPIC_BASE_URL` | (自动适配) | API 基础 URL，可覆盖模型默认值 |
| `ALLOWED_USERS` | * | 允许的用户 ID，逗号分隔 |
| `ALLOWED_ROOMS` | * | 允许的群聊 ID，逗号分隔 |
| `AUTO_REPLY_ENABLED` | true | 是否自动回复 |
| `AUTO_REPLY_COOLDOWN` | 5 | 回复冷却时间（秒） |
| `GROUP_MENTION_ONLY` | true | 群聊仅被 @ 时回复 |
| `CONVERSATION_MAX_TURNS` | 20 | 最大对话轮数 |
| `CONVERSATION_TTL_MINUTES` | 60 | 对话过期时间（分钟） |
| `BOT_NAME` | Claude | 机器人名称 |
| `ADMIN_HOST` | 0.0.0.0 | 管理面板监听地址 |
| `ADMIN_PORT` | 8080 | 管理面板端口 |
| `LOG_LEVEL` | INFO | 日志级别 |
| `DATA_DIR` | ./data | 数据存储目录 |

## 管理面板动态配置

以下配置项可在管理面板中实时修改，无需重启：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `api_key` | (空) | Anthropic/DeepSeek API Key（加密存储） |
| `dashscope_api_key` | (空) | DashScope API Key（加密存储） |
| `sticker_probability` | 0.3 | 表情包发送概率（0-1） |
| `reply_interval` | 0 | 回复前延迟（秒），模拟真人打字 |
| `reply_max_chars` | (空) | 回复字数上限，留空不限制 |
| `memory_mode` | 开启 | 多轮对话记忆开关 |
| `deep_thinking` | 关闭 | 深度思考模式 |
| `web_search` | 关闭 | 联网搜索开关 |
| `active_chat_enabled` | 开启 | 主动聊天开关 |
| `active_chat_cooldown_minutes` | 60 | 主动聊天冷却时间 |
| `active_chat_max_silent` | 3 | 主动聊天最大静默触发次数 |
| `active_chat_idle_minutes` | 15 | 主动聊天空闲判断阈值 |
| `scheduled_chat_idle_minutes` | 5 | 定时消息空闲判断阈值 |
| `bot_mood_enabled` | 开启 | Bot 情绪系统开关 |
| `bot_mood_value` | 50 | 当前情绪值（0-100，只读） |
| `personal_stories_enabled` | 关闭 | 个人经历融入开关 |
| `personal_stories` | [] | 个人经历列表（文本 + 触发关键词） |
| `proactive_sharing_enabled` | 关闭 | 主动分享开关 |
| `proactive_sharing_interval_minutes` | 180 | 主动分享间隔 |
| `proactive_sharing_topics` | [] | 主动分享话题池 |
| `rest_time_ranges` | [] | 休息时间段（HH:MM + 星期过滤） |
| `scheduled_restart` | (空) | 定时重启时间（HH:MM） |

## 技术栈

- **协议：** 腾讯 ilink (`ilinkai.weixin.qq.com`)
- **AI：** Anthropic SDK 兼容 API（DeepSeek / Claude / DashScope）
- **Web：** FastAPI + 原生 HTML/CSS/JS（无框架）
- **存储：** SQLite（对话 + 配置 + 表情包）
- **调度：** APScheduler
- **TTS：** Microsoft Edge TTS
- **加密：** Fernet 对称加密（API Key 存储）
- **搜索：** DuckDuckGo（联网搜索）
