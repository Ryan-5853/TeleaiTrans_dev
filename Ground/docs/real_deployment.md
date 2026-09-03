# 真机部署（服务器端，飞机主动接入）

Ground 采用“服务器先启动、飞机后接入”的模型，服务器**不需要**预登记
飞机。两架真机各自运行飞机端 agent（按 `docs/protocol.md` 实现），
主动连接：

```text
飞机 agent ──> ws://<服务器IP>:8080/plane/v1/ws?aircraft_id=<别名>
                 └─ hello {name, model, agent_version, features, m0}
```

`aircraft_id` 就是别名（例如 `lyra-a`、`lyra-b`），由飞机端 agent 在连接
参数中声明；服务器收到 `hello` 后自动注册并保持在线状态。

## 服务器部署步骤

1. 在 Ground 目录复制环境文件并填写服务器局域网 IP：

   ```bash
   cp .env.real.example .env.real
   # 编辑 .env.real：GROUND_HOST_IP=你的服务器 IP
   ```

2. 以真机模式启动（只启动 backend + frontend，不启动模拟机）：

   ```bash
   docker compose -f docker-compose.real.yml --env-file .env.real up -d --build
   ```

3. 验证服务：

   ```bash
   curl http://<服务器IP>:8080/api/health
   curl http://<服务器IP>:8080/api/aircraft
   ```

4. 打开 `http://<服务器IP>:8080`，等两架真机的 agent 上线后即可看到
   别名/状态；`GROUND_PUBLIC_BASE` 会自动被 compose 设为服务器局域网
   地址，供飞机端 `m0_flash` 回连下载固件。

## 数据与升级

- 数据库与固件产物存放在命名卷 `ground-real_ground_data`（`docker volume
  ls` 可见），删除容器不会丢数据；备份用
  `docker run --rm -v ground-real_ground_data:/data -v $PWD:/backup alpine tar czf /backup/ground-data.tar.gz /data`。
- 改代码或 `config/aircraft.yaml` 后执行
  `docker compose -f docker-compose.real.yml --env-file .env.real up -d --build`。
- 停服：`docker compose -f docker-compose.real.yml --env-file .env.real down`。

## 当前缺口

Ground 仓库目前只有模拟飞机（`simulator/plane_sim.py`），**两架真机上
还需要飞机端 agent** 才能真正上线。agent 应按 `docs/protocol.md`
实现 `hello/telemetry/params/maintenance/m0_flash`，建议直接在板端以
init 脚本/systemd 方式常驻运行，而不是把 Docker 装到 RK3506 上；服务器
镜像只负责 Ground 本身。
