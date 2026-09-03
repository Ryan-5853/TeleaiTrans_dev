# Ground ↔ Flight v2 协议

同步维护自 `Flight/docs/protocol_v2.md`；v1（`docs/protocol.md`）保留作为
兼容通道，新载板 agent 默认走 v2。

## 通道

- 飞机端（`Flight` agent 或模拟器）主动连接
  `ws://<ground>/plane/v2/ws?aircraft_id=<id>`；
- 信封：`{"v":2,"type":"...","ts":<float>,"payload":{}}`；
- 同 ID 重复连接时新连接替换旧连接（4001）；心跳超时规则与 v1 相同。

## hello / 身份与能力

```json
{
  "type": "hello",
  "payload": {
    "aircraft_id": "lyra-01", "name": "实验机 01",
    "model": "lyra-zero-w-amp", "board": "rk3506",
    "agent_version": "0.2.0", "ip": "192.168.8.21",
    "features": ["params", "maintenance", "m0_flash", "camera", "dock"],
    "m0": {"status": "APP1", "app_version": "0.0.1"},
    "fcu": {"link": "down"},
    "camera": [], "dock": {"profile": null}
  }
}
```

Ground 收到后更新 `last_ip/channel/proto`；`static_ip` 与
`claim_state` 由登记/claim 流程维护（Ground 为权威源）。

## 身份登记与 claim（REST）

```http
POST /api/v2/aircraft          # {aircraft_id,name,model,static_ip}
POST /api/v2/claims            # {aircraft_id,ttl_h?} -> {claim_id,code,expires_at}
POST /api/v2/claims/redeem     # {code,hw} -> {identity,ground:{url,plane_ws}}
GET  /api/v2/claims
```

claim 码一次性、默认 24 h 过期；redeem 成功后 `claim_state=claimed`。
板上 CLI：`flyctl init --claim <code> --ground <url>`。

## telemetry / 命令

`telemetry.payload` 增加：

```json
{
  "fcu": {"link": "up", "armed": false, "mode": "SIM",
          "battery": {"v": 16.4, "pct": 87}, "gps": {}, "attitude": {}},
  "peripherals": {
    "camera": {"devices": [{"device": "/dev/video0", "name": "UVC"}]},
    "dock": {"profile": null, "state": "unconfigured"}
  }
}
```

命令 op 扩展：`camera.snapshot`（JPEG ≤180 KiB，base64 回传）、`camera.params.read/write`、
`dock.status`、`dock.command`（dock 未配置 profile 时返回明确失败）。
信封版本为 2，其余异步 result/progress 语义与 v1 相同。

## 图传 / 图像调试转发（额外 HTTP）

浏览器只访问 Ground；Ground 以额外的 HTTP 连接访问飞机本地摄像头服务
（默认 `http://<飞机IP>:8080`，即板上旧版 `cam_webui.py`）。页面与接口
语义与旧版一致，只是路径增加 Ground 前缀：

```http
GET  /api/v2/aircraft/{id}/camera/ui             # 旧版调试页（路径已改写）
GET  /api/v2/aircraft/{id}/camera/stream         # MJPEG multipart 流
GET  /api/v2/aircraft/{id}/camera/api/controls   # 旧 /api/controls
POST /api/v2/aircraft/{id}/camera/api/control    # 旧 /api/control
POST /api/v2/aircraft/{id}/camera/api/camera     # 分辨率设置
GET  /api/v2/aircraft/{id}/camera/api/stats      # 诊断统计
GET  /api/v2/aircraft/{id}/camera/api/xu/status  # 传感器状态
POST /api/v2/aircraft/{id}/camera/api/xu/exposure
POST /api/v2/aircraft/{id}/camera/api/xu/reg
GET  /api/v2/aircraft/{id}/camera/api/process
POST /api/v2/aircraft/{id}/camera/api/process
GET  /api/v2/aircraft/{id}/camera/api/process/frame
```

飞机 IP 取 v2 hello 上报的 `ip` 或登记 `static_ip`；端口由 Ground 配置
`aircraft_camera_port`（默认 8080）控制。`camera/ui` 由 Ground 拉取板上
原始调试 HTML 并把 `/stream`、`/api/*` 改写为上述转发前缀，旧页面 JS
无需改动。

## MAVLink 桥（743）

- M0 手工解析并定型上报 HEARTBEAT；其余白名单
  `SYS_STATUS/GPS_RAW_INT/ATTITUDE/VFR_HUD` 原样桥接，Linux 解码；
- 下行维护命令由 Linux 编码 COMMAND_LONG，经 M0 串口发送；
- 743 未接线/共享内存未就绪时 `fcu.link=down`，命令返回明确错误。

## flash_start

与 v1 相同（url/sha256/size/run）。飞机端本地执行 `m0load`，无 SSH；
Ground 在 v2 plane 通道与 SSH fallback 中选择 plane 通道。
