# Ground ↔ 飞机 v1 协议

## 通道

- 飞机端 agent（或模拟器）主动发起 WebSocket 连接：
  `ws://<ground>/plane/v1/ws?aircraft_id=<id>`
- `aircraft_id` 规则：`[A-Za-z0-9][A-Za-z0-9._-]{0,63}`。
- 同 ID 重复连接时新连接替换旧连接（旧连接以 4001 关闭）。
- Ground 侧心跳超时 30 s；飞机应每 10 s 内至少有一个消息（推荐 1 Hz 遥测）。
- 浏览器订阅 UI：`ws://<ground>/ui/v1/ws`（只读推送 + 初始 snapshot）。

## 消息信封

所有消息均为 UTF-8 JSON：

```json
{"v": 1, "type": "hello", "ts": 1720000000.0, "payload": {}}
```

飞机→Ground：`hello`、`telemetry`、`event`、`result`、`progress`、`error`
Ground→飞机：`ping`、`command`、`flash_start`、`error`

## hello（首条消息）

```json
{
  "type": "hello",
  "payload": {
    "aircraft_id": "lyra-01",
    "name": "实验机 01",
    "model": "lyra-zero-w-amp",
    "board": "rk3506",
    "agent_version": "0.1.0",
    "features": ["params", "maintenance", "m0_flash"],
    "m0": {"status": "APP1", "app_version": "0.0.1"}
  }
}
```

`features` 控制 WebUI 中可用功能：

- `params`：支持 `params.read` / `params.write`；
- `maintenance`：支持维护命令；
- `m0_flash`：支持 `flash_start`（无此能力时真机可走 SSH 烧录通道）。

## telemetry

```json
{
  "type": "telemetry",
  "payload": {
    "uptime_s": 123.4,
    "board": {"cpu_pct": 5.2, "mem_pct": 20.1, "temp_c": 45.0},
    "m0": {"status": "APP1", "app_version": "0.0.1", "armed": true}
  }
}
```

默认 1 Hz。Ground 对 UI 推送节流为 5 Hz，不落历史库。

## 命令与结果

Ground 下发：

```json
{
  "type": "command",
  "payload": {
    "request_id": "…",
    "op": "motor_test",
    "params": {"duration_s": 2.0}
  }
}
```

飞机异步回执（必须带相同 `request_id`）：

```json
{
  "type": "result",
  "payload": {
    "request_id": "…",
    "ok": true,
    "error": null,
    "data": {}
  }
}
```

v1 op 集合：`ping`、`arm`、`disarm`、`motor_test`、`servo_test`、
`stop_test`、`params.read`、`params.write`。`simulator/plane_sim.py` 是
参考实现；真机 agent 把 op 映射到实际控制器/板卡接口。

参数消息：`params.read` 的 `params.keys` 为空表示全量；`params.write` 携带
`params.values`（仅 bool/int/float/str）。成功 result 的 `data.params` 返回
写入或读取后的参数对象。

## M0 热更新（flash_start，可选能力）

Ground 先通过 REST 产物库管理 app.bin（自动校验 M0AP 头与 CRC），
然后向具备 `m0_flash` 能力的飞机下发：

```json
{
  "type": "flash_start",
  "payload": {
    "request_id": "job-id",
    "artifact_id": "<sha256>",
    "url": "http://…/api/artifacts/<sha256>/download",
    "sha256": "…",
    "size": 29696,
    "run": true
  }
}
```

飞机下载 → 校验 sha256/M0AP → 通过板上 `m0load`/`m0loadd` 写入 M0，
期间用 `progress` 回报 `phase/percent/message`，结束用 `result` 回报。
没有 `m0_flash` 的真机由 Ground 走 SSH 通道执行
`m0load /tmp/m0load_ground_upload.bin --run`。
