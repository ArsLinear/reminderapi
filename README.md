# 日程/待办解析中间层

输入自然语言、群通知、邮件或公告文本，服务调用 DeepSeek 解析并返回干净 JSON，供 iPhone Shortcuts 创建 Apple Calendar/Reminders。服务器**不**直接操作 Apple 日历或提醒事项。

## 快速启动

1. 准备环境变量：

```bash
cp .env.example .env
```

在 `.env` 中填入：

- `DEEPSEEK_API_KEY`（必填）
- `DEEPSEEK_BASE_URL`（默认：https://api.deepseek.com）
- `DEEPSEEK_MODEL`（默认：deepseek-v4-pro）
- `DEEPSEEK_DISABLE_THINKING`（默认：true）
- `DEFAULT_TZ`（默认：Asia/Shanghai）
- `API_KEY`（可选，设置后需在请求头传入 `X-API-Key`）

2. 启动服务：

```bash
docker compose up --build
```

默认地址：`http://localhost:8000`

## 公网访问（可选）

如需从外网（如 iPhone Shortcuts）访问本地服务，推荐使用 Cloudflare Tunnel：

1. 在 Cloudflare Zero Trust 中创建 Tunnel，获取 token
2. 在 `docker-compose.yml` 中添加 cloudflared sidecar 容器：

```yaml
  reminder-cloudflared:
    image: cloudflare/cloudflared:latest
    restart: unless-stopped
    command: tunnel --no-autoupdate run
    environment:
      - TUNNEL_TOKEN=<your-tunnel-token>
    depends_on:
      - reminderapi
```

3. `docker compose up -d` 启动后，通过 Cloudflare 分配的域名即可访问 `/parse` 接口。

## 接口

### POST /parse

**请求体**支持两种格式：

1. `text/plain`（推荐）
2. `application/json`：`{"text": "..."}`  

示例：

```bash
curl -X POST http://localhost:8000/parse \
  -H 'Content-Type: text/plain' \
  --data '明天下午三点交材料'
```

或：

```bash
curl -X POST http://localhost:8000/parse \
  -H 'Content-Type: application/json' \
  --data '{"text":"下周五上午十点组会"}'
```

**服务会自动注入当前时间与时区（DEFAULT_TZ）**，用于解析“明天/下周”等相对时间。

## 返回结构

成功示例：

```json
{
  "ok": true,
  "need_confirm": false,
  "question": "",
  "items": [
    {
      "type": "calendar",
      "title": "组会",
      "start": "2026-05-23T10:00:00+08:00",
      "end": "2026-05-23T11:00:00+08:00",
      "alert_minutes": 30
    }
  ]
}
```

信息不足示例：

```json
{
  "ok": false,
  "need_confirm": true,
  "question": "这条通知没有明确截止时间，需要创建提醒吗？",
  "items": []
}
```

### 校验规则（服务端）

- `type` 只能是 `calendar` 或 `reminder`
- `calendar` 必须有 `title`、`start`、`end`
- `reminder` 必须有 `title`、`due`
- 时间必须是 ISO 8601（包含时区）
- `end` 必须晚于 `start`
- `title` 不能为空
- `items` 默认最多返回 3 个
- 模型输出不合法时，返回 `ok=false` 与简短 `question`

## iPhone Shortcuts 调用建议

使用“获取 URL 内容（Get Contents of URL）”：
- 方法：POST
- URL：`http://<你的服务器>:8000/parse`
- 请求体：原始文本（Text）
- Content-Type：`text/plain`
- 返回解析：按 JSON 读取 `ok` 和 `items` 并创建日历或提醒事项
