# Ground：局域网地面站服务

面向 1–5 台 Lyra/RK3506 机群的局域网地面站：WebUI 实时观察飞机状态、读写
参数、执行维护级控制（arm/disarm、电机/舵机测试），并对 M0 应用固件做远程
热更新。v2 起增加飞机登记/claim 身份下发（`/api/v2/aircraft`、`/api/v2/claims`）
与 `/plane/v2/ws` 通道，配合 `Flight/` 载板 agent（743 MAVLink 桥、相机快照、
对接模块占位）使用；图传/图像调试经 Ground 额外 HTTP 转发飞机本地
cam_webui（`/api/v2/aircraft/{id}/camera/...`）。协议见 `docs/protocol.md`
与 `docs/protocol_v2.md`。

仓库结构：

```text
backend/      FastAPI 服务端（REST + 飞机链路 + UI 推送）
frontend/     Vue3 + TS + Element Plus WebUI
simulator/    模拟飞机（协议参考实现，可跑通全链路）
config/       机群静态注册表（真实板 SSH 配置）
docs/         协议文档与真实板冒烟流程
docker/       容器构建配置
```

## 快速开始（本机开发）

```bash
python3 -m venv .venv
.venv/bin/pip install -e './backend[dev]'
.venv/bin/uvicorn app.main:app --app-dir backend --reload --port 8000
```

另开终端启动 3 台模拟飞机：

```bash
.venv/bin/python simulator/plane_sim.py --base ws://127.0.0.1:8000 --count 3
```

WebUI 开发模式：

```bash
cd frontend && npm install && npm run dev
```

浏览器访问 `http://127.0.0.1:5173`（`/api` 与 WebSocket 已代理到 8000）。

## Docker watch 开发/部署（推荐）

```bash
docker compose build        # 仅在依赖（pyproject/package*.json/Dockerfile）变化后需要
docker compose watch        # 后端 uvicorn --reload + 前端 Vite HMR，源码改动即时生效
docker compose --profile sim watch   # 需要 3 台模拟飞机时
```

浏览器访问 `http://<服务器IP>:8081`（前端为 Vite dev server，`/api`、
`/ui/v1/ws`、`/plane/v1|v2/ws` 均代理到后端容器，MJPEG 图传也走该代理）。
数据库与固件产物落在 `./data/`（已 gitignore）。

compose 文件给 `backend`、`frontend` 定义了 `develop.watch`：

- `backend/`：绑定挂载到容器并以 `--reload` 运行，Py 改动自动重载；只有
  `pyproject.toml` 变化才触发镜像重建；
- `frontend/src` 与 `vite.config.ts`：Vite HMR 即时生效；只有
  `package*.json` 变化才触发镜像重建。

如需旧版“构建产物 + nginx”的单体发布方式，可用 `target` 指定 Dockerfile
默认阶段自行打镜像（开发日常不需要）。

真机模式（不带模拟机、飞机主动接入）：

```bash
cp .env.real.example .env.real   # 填写 GROUND_HOST_IP
docker compose -f docker-compose.real.yml --env-file .env.real up -d --build
```

详细说明见 `docs/real_deployment.md`。

## 测试

```bash
cd backend && ../.venv/bin/pytest -q
```

真实 Lyra 板 M0 烧录的冒烟流程见
`docs/real_board_smoke.md`。
