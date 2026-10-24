# N.E.K.O. FF14 猫娘陪伴插件

这是 N.E.K.O. 的 Python 插件，负责把本机 FF14 Bridge 提供的区域、战斗和触发器信息交给 N.E.K.O.。插件只访问本机回环地址，不读取或修改游戏进程；ACT / OverlayPlugin 数据由独立的 .NET 8 FF14 Bridge 接收和整理。

## 当前功能

- 检查本机 Bridge：请求 `GET /health`，查看 Bridge 和 OverlayPlugin 的连接状态。
- 查询区域与战斗状态：请求 `GET /v1/state`。
- 查询最近战斗摘要：请求 `GET /v1/encounters/latest/summary`。
- 轮询 Bridge 事件：按游标请求 `GET /v1/events/recent`，可选处理高优先级 Triggernometry 提示或区域问候。
- 通过 N.E.K.O. 支持的 `push_message` v2 接口发送消息。
- 用户调用状态、区域或战斗查询时，插件会把简短中文结果提交到当前 N.E.K.O. 对话，并同时返回结构化数据。

自动主动播报和区域问候默认关闭，可在插件配置中分别开启。查询结果和手动消息会提交到当前对话，但提交成功不代表 N.E.K.O. 一定生成语音或播放音频；实际语音取决于当前运行的 N.E.K.O. 语音会话。

## 配置

在 N.E.K.O. 插件配置中设置 `companion.bridge_base_url`：

```toml
[companion]
bridge_base_url = "http://127.0.0.1:17891"
request_timeout_seconds = 3
proactive_speech_enabled = false
zone_greeting_enabled = false
```

Bridge 地址仅允许 `127.0.0.1`、`localhost` 或 `::1`。请求超时时间可设为 0.2–10 秒；插件会拒绝非本机 URL、凭据、查询参数和片段，并将响应大小限制为 256 KiB。

## Bridge API

- `GET /health`：返回 Bridge 状态、OverlayPlugin 连接状态、最近 ACT 事件时间和重连次数。
- `GET /v1/state`：返回区域编号/名称、战斗状态及数据源状态；副本名称尚未映射。
- `GET /v1/encounters/latest/summary`：返回聚合战斗指标；没有可用摘要时返回 `{ "available": false }`。不会返回逐个战斗单位的原始数据或角色名称。
- `GET /v1/events/recent?after=<sequence>`：按事件序号增量获取事件，例如 `{ "events": [], "nextCursor": 12, "dropped": false }`。

## 安装与验证

### 插件中心安装包

在 N.E.K.O. 插件中心导入 `dist/ff14_companion-0.1.1.neko-plugin`，刷新插件列表后启动 `ff14_companion`。若插件已运行旧版本，先停止旧版本，再导入新版并启动。

### 从源码安装

将本目录复制到 `N.E.K.O./plugin/plugins/ff14_companion/`（文件夹名需与插件 ID 一致），然后在 N.E.K.O. 环境中执行：

```powershell
uv run neko-plugin check ff14_companion
```

刷新插件页并手动启动插件，然后调用“检查 FF14 Bridge”。SDK 依赖不会 vendoring 到此仓库；清单遵循公开插件格式，验证和打包时应使用目标 N.E.K.O. 安装目录提供的 SDK。
