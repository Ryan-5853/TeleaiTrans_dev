# 真实 Lyra 板 M0 热更新冒烟

前置条件（板上）：

- root SSH 可用，私钥在 Ground 服务器的 `config/aircraft.yaml` 中登记；
- `/usr/bin/m0load`、`/usr/bin/m0loadd` 已安装且 `m0loadd` 已启动
  （S99m0loadd），内核具备 `/sys/rk_amp/m0_reset`；
- 一个已知良好的 `app.bin`（M0AP 格式）。

步骤：

1. 在 `config/aircraft.yaml` 登记板子，例如：

   ```yaml
   aircraft:
     - id: lyra-01
       name: 实验机 01
       model: lyra-zero-w-amp
       ssh:
         host: 192.168.8.21
         user: root
         key_path: ~/.ssh/id_ed25519
   ```

2. 启动 Ground（compose 或本机 uvicorn），WebUI 应能看到 `lyra-01`
   （通道 `ssh`）。
3. 在“固件”页上传 `app.bin`，服务端会先做 M0AP 头/CRC 校验。
4. 对 `lyra-01` 发起烧录，观察作业从 `uploading → flashing → ready →
   launching` 到 `success`。
5. 板侧复核：

   ```bash
   ssh root@<板IP> m0load status
   ```

   REG11 应为 `APP1`（0x41505031），与烧录前保持一致行为。

通过标准：WebUI 作业 `success`、日志尾部含 `RESULT OK`、板侧 `m0load
status` 回到 `APP1`。失败时先查作业日志尾与
`/var/log/m0loadd.log`，再按 lyra-firmware 文档的已知错误排查
（内核 reset 路径缺失、bootloader 未 PING、CRC 失败等）。
