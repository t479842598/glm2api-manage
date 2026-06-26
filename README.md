# ChatGLM 2 API

[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https%3A%2F%2Fgithub.com%2Ft479842598%2Fglm2api-manage&env=ADMIN_KEY&envDescription=%E7%AE%A1%E7%90%86%E9%9D%A2%E6%9D%BF%E7%99%BB%E5%BD%95%E5%AF%86%E9%92%A5%EF%BC%8C%E9%BB%98%E8%AE%A4%E4%B8%BA%20glm2api-admin)

---

## 📢 更新日志

### 2026-06-26 — v0.2.3 修复工具调用流式中断 + keepalive 心跳实际不生效

- **三重修复 GLM 5.2 等推理模型调用工具后流式响应被中断** — 原代码存在三层误判链路：
  1. `_extract_event_error` 第二段检查中 event 级别 status 为空字符串时（工具执行中），part 级别 `status: "error"`（reasoning 段正常结束）被误判返回 `"GLM part status error"` → 已修正为**仅在 event status 明确为 `"error"` 时才检查 part**
  2. **part 级别 `status: "error"` 永远不是致命错误** — 即使 event status 为 `"error"`，part 的 `status: "error"` 也只是内部生命周期状态，不应中断流。现已彻底移除返回 `"GLM part status error"` 的代码，改为 `logger.debug` 日志记录
  3. `_raise_for_event_error` 在 event status 为 `"error"` 但无任何错误负载时仍抛 `"GLM stream request error"` 兜底错误 → 已新增安全阀：**没有具体错误负载时，即使 event status 是 `"error"` 也不抛错**，仅记日志
- **修复 keepalive 心跳 30 秒超时实际未生效** — `sock = getattr(response, "fp", None)` 拿到的是 `HTTPResponse` 对象，该对象没有 `settimeout` 方法；keepalive 超时一直退化到 `urlopen` 的 120 秒默认值。现已通过 `response.fp.fp.raw._sock` 正确穿透到原始 socket，30 秒心跳正常发送，同时兼容 gzip 压缩包装链路
- **修复 socket.settimeout OSError 未捕获** — 防止操作已被关闭的 socket 时抛异常
### 2026-06-26 — v0.2.2 修复推理模型流式超时 + keepalive 心跳

- **修复 GLM 5.2 等推理模型流式请求 45 秒超时** — 推理模型思考阶段不产生任何输出，导致前端 idle-timeout；现在在 SSE 读取层添加 30 秒 keepalive 心跳（`: keepalive` SSE 注释），防止前端因长时间无数据而断开连接
- **流尾兜底，无论上游是否正常结束都送合法 OpenAI 流终止帧** — `_iter_sse_events` 退出后调用 `accumulator.finalize(status="stop")`，确保客户端在断连/IncompleteRead 等异常分支下也能收到 `choices[0].finish_reason="stop"` 与 `data: [DONE]\n\n`，避免解析错误
- **收紧 `GLM part status error` 判定** — 单个 part（如 reasoning 段）以 `status: "error"` 结束不再立即 throw，必须 event 级别状态也是 `error` 才中断整轮（与 v0.2.1 修复一致，本次进一步细化为「event 级别 error 才 fatal」）

### 2026-06-26 — v0.2.1 修复流式响应中途报错

- **修复流式请求中途报 `GLM part status error`** — GLM 的 SSE 流中，单个 part（如 reasoning 段）可能以 `status: "error"` 结束，但 event 级别状态仍为 `finish`，之前会错误地中断整个流，现在只在 event 级别确实为 error 时才中断

### 2026-06-26 — v0.2.0 管理面板大升级

- **修复管理面板白屏** — 抛弃 CDN 依赖，Vue 3 + Naive UI 改为本地加载，登录框用原生 HTML
- **新增 VPS 部署脚本** — 一键上传、配置 systemd 服务 + Nginx 反向代理
- **Token 分页** — 每页 10 条，首页/上一页/下一页/末页导航
- **日志按级别着色** — DEBUG 灰、INFO 绿、WARNING 橙、ERROR 红加粗
- **对话测试改进** — 新增 System Prompt、模型搜索、耗时显示
- **配置页精简** — 只展示前 5 条关键配置
- **静态文件路由** — `/admin/lib/*` 安全提供本地静态资源
- **端口冲突处理** — 8000 被占用可切换 `PORT` 环境变量

---

`glm2api` 是一个**零外部依赖**的本地代理服务，把 `chatglm.cn` 的网页接口转换成 OpenAI / Anthropic 兼容接口。直接接入 OpenAI SDK、Cherry Studio、Open WebUI、LobeChat 或任何兼容 OpenAI API 的工具。

**✨ 内置管理面板 + API Key 管理 + VPS 一键部署。**

---

## 支持接口

| 端点 | 说明 |
|------|------|
| `POST /v1/chat/completions` | OpenAI 聊天（流式 + 非流式） |
| `POST /v1/responses` | OpenAI Responses API |
| `POST /v1/messages` | Anthropic Messages API 兼容 |
| `POST /v1/images/generations` | 图片生成 |
| `GET /v1/models` | 模型列表 |
| `GET /health` | 健康检查 |
| `GET /admin` | 🆕 管理面板（概览 / 配置 / Token / 日志 / 请求记录 / API Key / 对话测试） |

---

## 目录

- [快速开始](#快速开始)
- [管理面板](#管理面板)
- [API Key 管理](#api-key-管理)
- [部署方式](#部署方式)
  - [Vercel 一键部署](#vercel-一键部署)
  - [Docker 部署](#docker-部署)
  - [Railway / Render / Zeabur](#railway--render--zeabur)
  - [Windows 服务](#windows-服务)
  - [Linux systemd](#linux-systemd)
- [配置说明](#配置说明)
- [使用示例](#使用示例)
- [常见问题](#常见问题)

---

## 快速开始

### 前置条件

- Python ≥ 3.12
- （可选）智谱清言账号的 `refresh_token`
- **如果不填任何 token，自动走游客模式，开箱即用。**

### 安装与启动

```bash
# 1. 克隆项目
git clone https://github.com/t479842598/glm2api-manage.git
cd glm2api

# 2. 安装（零外部依赖，仅需要 setuptools）
pip install -e .

# 3. 复制配置文件（可选，不复制也能自动创建）
cp .env.example .env

# 4. 启动
python -m glm2api
```

启动后访问：
- API：`http://127.0.0.1:8000/v1/chat/completions`
- 管理面板：`http://127.0.0.1:8000/admin`

> **Windows 用户**：如果遇到编码问题，请设置环境变量 `PYTHONIOENCODING=utf-8`，或直接用 `python -m glm2api` 代替 `python main.py`。

### 验证服务

```bash
curl http://127.0.0.1:8000/health
# → {"status":"ok"}

curl http://127.0.0.1:8000/v1/models
# → 返回 78 个可用模型
```

---

## 管理面板

内置零依赖管理面板，访问 `http://127.0.0.1:8000/admin`。

### 登录

默认管理员密钥：`glm2api-admin`（可在 `.env` 中通过 `ADMIN_KEY` 修改）。

### 功能

| 页面 | 功能 |
|------|------|
| **概览** | 账号数、模型数、并发上限、请求成功率统计 |
| **配置** | 前 5 条关键运行时配置概览 |
| **Token** | 脱敏浏览各账号 token，支持分页（每页 10 条） |
| **日志** | 实时日志流，按级别着色（DEBUG 灰/INFO 绿/WARNING 橙/ERROR 红），支持过滤和自动刷新 |
| **请求记录** | 最近 500 条请求（方法、路径、模型、状态码、耗时） |
| **API Key** | 增删改查 API 密钥，启用/禁用切换 |
| **对话测试** | 选择模型、输入提示词和 System Prompt，查看返回结果 |

### 自定义管理员密钥

```env
# .env
ADMIN_KEY=你的自定义密钥
```

---

## API Key 管理

在管理面板的 **API Key** 标签页中可以添加 API 密钥，用来保护你的 glm2api 接口。

### 工作原理

- **默认状态**：没有 API Key 时，所有接口免认证（向后兼容）
- **添加至少一个启用的 Key 后**：访问 `/v1/chat/completions` 等接口需要携带认证
- **禁用或删除所有 Key 后**：恢复免认证

### 使用 API Key

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="你的-api-key",  # ← 管理面板中创建的 key
)

# 或通过 x-api-key header：
# headers = {"x-api-key": "你的-api-key"}
```

### 环境变量持久化

API Key 会自动保存到 `.env` 文件的 `GLM2API_API_KEYS` 字段（JSON 格式），重启服务后自动恢复。

---

## 部署方式

### Vercel 一键部署

点击下方按钮，30 秒完成部署：

[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https%3A%2F%2Fgithub.com%2Ft479842598%2Fglm2api-manage&env=ADMIN_KEY&envDescription=%E7%AE%A1%E7%90%86%E9%9D%A2%E6%9D%BF%E7%99%BB%E5%BD%95%E5%AF%86%E9%92%A5%EF%BC%8C%E9%BB%98%E8%AE%A4%E4%B8%BA%20glm2api-admin)

部署后，Vercel 会分配一个域名如 `https://你的项目.vercel.app`。

**在客户端中的用法**：

```python
from openai import OpenAI

client = OpenAI(
    base_url="https://你的项目.vercel.app/v1",
    api_key="dummy",
)
```

**Vercel 部署注意事项**：
- 自动启用游客模式（Vercel 环境无持久化存储）
- SSE 流式响应可用，但受 Vercel 函数超时限制（Hobby 10s / Pro 60s）
- 管理面板同样可用，访问 `https://你的项目.vercel.app/admin`
- 如需设置 API Key 认证，在 Vercel 项目 Settings → Environment Variables 中添加 `GLM2API_API_KEYS`

#### 手动 Vercel 部署

```bash
# 安装 Vercel CLI
npm i -g vercel

# 部署
cd glm2api
vercel --prod
```

#### Vercel 环境变量参考

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `ADMIN_KEY` | 管理面板登录密钥 | `glm2api-admin` |
| `GLM2API_API_KEYS` | API Key JSON 数组 | 空（免认证） |
| `GLM_USE_GUEST_REFRESH_TOKEN` | 强制游客模式 | `true`（Vercel 上推荐） |
| `GLM_MAX_CONCURRENCY` | 并发槽位数 | `3` |
| `GLM_REFRESH_TOKEN` | 单账号 token | 空 |
| `GLM_DELETE_CONVERSATION` | 自动删除会话 | `true` |

---

### Docker 部署

```dockerfile
# Dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY . /app

RUN pip install -e .

EXPOSE 8000

CMD ["python", "-m", "glm2api"]
```

```bash
# 构建与运行
docker build -t glm2api .
docker run -d -p 8000:8000 \
  -e GLM_USE_GUEST_REFRESH_TOKEN=true \
  -e ADMIN_KEY=your-secret-key \
  --name glm2api \
  glm2api

# 挂载 token.txt 使用真实账号
docker run -d -p 8000:8000 \
  -v ./token.txt:/app/token.txt \
  -v ./.env:/app/.env \
  --name glm2api \
  glm2api
```

```bash
# docker-compose.yml
version: "3.8"
services:
  glm2api:
    build: .
    ports:
      - "8000:8000"
    environment:
      - GLM_USE_GUEST_REFRESH_TOKEN=true
      - ADMIN_KEY=glm2api-admin
    restart: unless-stopped
```

---

### Railway / Render / Zeabur

这些平台通用部署方式：

1. Fork / Clone 本项目到你的 GitHub
2. 在平台中选择 "Deploy from GitHub"
3. Build command：`pip install -e .`
4. Start command：`python -m glm2api`
5. 添加环境变量（参考上方 Vercel 环境变量表）

**Railway 示例配置**：

```toml
[build]
builder = "nixpacks"
buildCommand = "pip install -e ."

[deploy]
startCommand = "python -m glm2api"

[service]
port = 8000
```

---

### Windows 服务

使用 NSSM (Non-Sucking Service Manager) 注册为 Windows 服务：

```powershell
# 下载 NSSM: https://nssm.cc/download
nssm install glm2api

# 配置：
# Application: C:\Program Files\Python312\python.exe
# Arguments: -m glm2api
# Start directory: C:\path\to\glm2api
# Environment: PYTHONIOENCODING=utf-8

nssm start glm2api
```

---

### VPS 一键部署

适用于 Ubuntu 24.04+ 服务器，自动完成安装、配置 systemd 服务 + Nginx 反向代理：

```bash
# 1. 在本地克隆项目后上传
rsync -avz --exclude='.git' --exclude='__pycache__' -e 'ssh -p 22' ./ root@你的服务器IP:/opt/glm2api/

# 2. 登录服务器，设置环境变量
ssh root@你的服务器IP
cd /opt/glm2api

# 3. 修改监听地址为 0.0.0.0，让 Nginx 可以代理
sed -i 's/^HOST=.*/HOST=0.0.0.0/' .env
sed -i 's/^PORT=.*/PORT=8001/' .env  # 如 8000 被占用

# 4. 创建 systemd 服务
cat > /etc/systemd/system/glm2api.service << 'EOF'
[Unit]
Description=glm2api - GLM to OpenAI API Proxy
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/glm2api
Environment=PYTHONPATH=/opt/glm2api/src
ExecStart=/usr/bin/python3 /opt/glm2api/main.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload && systemctl enable --now glm2api

# 5. 配置 Nginx 反向代理（将域名替换为你的实际域名）
cat > /etc/nginx/sites-available/glm2api.conf << 'NGX'
server {
    listen 80;
    server_name your-domain.com;

    client_max_body_size 100M;

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }
}
NGX

ln -sf /etc/nginx/sites-available/glm2api.conf /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

# 6. 可选：配置 HTTPS
certbot --nginx -d your-domain.com
```

> **注意**：管理面板的 Vue 3 + Naive UI 静态文件已内置在项目 `admin_static/lib/` 目录中，无需额外下载。

---

### Linux systemd

```ini
# /etc/systemd/system/glm2api.service
[Unit]
Description=glm2api - GLM to OpenAI API Proxy
After=network.target

[Service]
Type=simple
User=glm2api
WorkingDirectory=/opt/glm2api
Environment="PYTHONIOENCODING=utf-8"
Environment="GLM_USE_GUEST_REFRESH_TOKEN=true"
ExecStart=/usr/bin/python3 -m glm2api
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now glm2api
sudo systemctl status glm2api
```

---

## 配置说明

### 获取 GLM Refresh Token

1. 打开 `https://chatglm.cn` 并登录
2. 按 `F12` → `Application` → `Local Storage`
3. 找到 `chatglm_refresh_token`
4. 填入 `.env`：`GLM_REFRESH_TOKEN=你的token`

**如果不填**：自动启用游客模式，零配置即可使用。

### 多账号负载均衡

创建 `token.txt`，每行一个 `refresh_token`：

```text
token-account-1
token-account-2
token-account-3
```

程序会自动在多个账号间轮换，某个账号失败时自动切换到下一个。

### 完整配置项

| 变量 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `HOST` | str | `127.0.0.1` | 监听地址，局域网用 `0.0.0.0` |
| `PORT` | int | `8000` | 监听端口 |
| `API_PREFIX` | str | `/v1` | OpenAI 兼容路径前缀 |
| `LOG_LEVEL` | str | `INFO` | 日志级别：DEBUG / INFO / WARNING / ERROR |
| `DEBUG_DUMP_ALL` | bool | `false` | 调试狂暴模式 |
| `REQUEST_TIMEOUT_SECONDS` | int | `120` | 上游请求超时 |
| `GLM_REFRESH_TOKEN` | str | — | 单账号 refresh_token |
| `GLM_TOKEN_FILE` | str | `token.txt` | 多账号 token 文件路径 |
| `GLM_USE_GUEST_REFRESH_TOKEN` | bool | `false` | 强制游客模式 |
| `GLM_GUEST_MAX_RETRIES` | int | `3` | 游客 token 获取失败重试次数 |
| `GLM_MAX_CONCURRENCY` | int | `3` | 上游并发槽位数量 |
| `GLM_QUEUE_WAIT_TIMEOUT_SECONDS` | int | `600` | 队列等待超时 |
| `GLM_BUSY_MAX_RETRIES` | int | `30` | 上游忙碌时重试次数 |
| `GLM_DELETE_CONVERSATION` | bool | `true` | 请求结束后删除 GLM 会话 |
| `GLM_ASSISTANT_ID` | str | `65940acff94777010aa6b796` | 对话 assistant id |
| `GLM_IMAGE_ASSISTANT_ID` | str | `65a232c082ff90a2ad2f15e2` | 绘图 assistant id |
| `BLOCKED_TOOL_NAMES` | str | — | 工具黑名单（逗号分隔） |
| `ADMIN_KEY` | str | `glm2api-admin` | 🆕 管理面板登录密钥 |
| `GLM2API_API_KEYS` | str | — | 🆕 API Key JSON 数组 |
| `SERVER_API_KEYS` | str | — | 旧版 API Key（逗号分隔） |
| `CORS_ALLOW_ORIGIN` | str | `*` | CORS 允许来源 |

---

## 使用示例

### Curl

```bash
# 聊天
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"glm-4-flash","messages":[{"role":"user","content":"你好"}]}'

# 图片生成
curl http://127.0.0.1:8000/v1/images/generations \
  -H "Content-Type: application/json" \
  -d '{"model":"glm-image-1","prompt":"画个枫叶","size":"1024x1024"}'
```

### Python (OpenAI SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="dummy",
)

# 非流式
resp = client.chat.completions.create(
    model="glm-4",
    messages=[{"role": "user", "content": "你好，介绍一下你自己"}],
)
print(resp.choices[0].message.content)

# 流式
stream = client.chat.completions.create(
    model="glm-4",
    messages=[{"role": "user", "content": "写一首七言绝句"}],
    stream=True,
)
for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

### Python (Responses API)

```python
resp = client.responses.create(
    model="glm-4",
    input=[{"role": "user", "content": "你好"}],
)
print(resp.output_text)
```

### Python (图片生成)

```python
image = client.images.generate(
    model="glm-image-1",
    prompt="画个枫叶",
    size="1024x1024",
)
print(image.data[0].url)
```

### Anthropic SDK 兼容

```python
# glm2api 支持 /v1/messages 端点，兼容 Anthropic Messages 格式
import requests

resp = requests.post(
    "http://127.0.0.1:8000/v1/messages",
    headers={"x-api-key": "dummy", "Content-Type": "application/json"},
    json={
        "model": "glm-4",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": "Hello"}],
    },
)
```

---

## 常见问题

### 启动报错 `GLM_REFRESH_TOKEN` 缺失

**新版本默认自动退回游客模式**，无需填写。如果你想使用账号，请检查 `.env` 或 `token.txt`。

### 返回「请等待其他对话生成完毕」

GLM 侧存在并发限制，程序内置了串行队列和自动重试（默认重试 30 次）。可通过 `GLM_BUSY_MAX_RETRIES` 和 `GLM_BUSY_RETRY_INTERVAL_SECONDS` 调整。

### 返回「请登录后继续使用」

账号 token 已失效，需要重新登录 `https://chatglm.cn` 获取新的 `refresh_token`。

### Windows 下启动报 `UnicodeEncodeError`

设置环境变量后启动：
```powershell
$env:PYTHONIOENCODING="utf-8"
python -m glm2api
```

### Vercel 部署后流式响应不工作

Vercel Hobby 计划的函数超时限制为 10 秒，长回答可能会被截断。升级到 Pro 计划（60 秒）或使用本地 / Docker 部署。

---

## 项目架构

```
src/glm2api/
├── __init__.py          # 版本声明
├── __main__.py          # CLI 入口
├── app.py               # 应用生命周期管理
├── config.py            # 配置加载与校验
├── logging_utils.py     # 彩色日志 + 内存缓冲
├── model_variants.py    # 模型变体展开 (think/search)
├── model_profiles.py    # 模型配置档
├── server.py            # HTTP 路由 + SSE 流式 + 请求记录
├── admin.py             # 🆕 管理面板 API + API Key 存储
├── admin_static/
│   └── index.html       # 🆕 Vue 3 + Naive UI 管理面板 SPA
├── services/
│   ├── glm_client.py    # GLM Web API 客户端 + 并发队列
│   ├── glm_auth.py      # 认证管理 (refresh / guest token)
│   ├── translator.py    # GLM → OpenAI 格式转换
│   ├── anthropic_adapter.py  # Anthropic 适配器
│   └── responses_adapter.py  # Responses API 适配器
└── utils/
    ├── tool_parser.py   # 工具调用流式解析器
    └── tool_protocol.py # 工具协议常量
```

**设计原则**：零外部依赖（纯 Python stdlib），`ThreadingHTTPServer` 处理 HTTP，`urllib.request` 发起上游请求。

---

## License

[AGPL-3.0](LICENSE)
