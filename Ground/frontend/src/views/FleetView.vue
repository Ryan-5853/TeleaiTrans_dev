<script setup lang="ts">
import { computed } from "vue";
import { ref } from "vue";
import { useRouter } from "vue-router";

import { api } from "@/api";
import { useFleetStore } from "@/stores/fleet";
import type { Aircraft } from "@/types";

const fleet = useFleetStore();
const router = useRouter();
const windowOrigin = window.location.origin;
const claimDialogVisible = ref(false);
const registerForm = ref({
  aircraft_id: "",
  name: "",
  model: "lyra-zero-w-amp",
  static_ip: ""
});
const claimResult = ref("");
const claimBusy = ref(false);

const rows = computed(() =>
  fleet.aircraft.map((item) => ({
    ...item,
    live: fleet.telemetry[item.id] ?? item.telemetry
  }))
);

function fmt(ts?: number | null): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false });
}

function channelTag(channel: Aircraft["channel"]): "primary" | "success" | "info" {
  return channel === "plane-link" ? "success" : channel === "ssh" ? "primary" : "info";
}

async function createRegistrationAndClaim() {
  if (!registerForm.value.aircraft_id) return;
  claimBusy.value = true;
  claimResult.value = "";
  try {
    await api.registry.register(registerForm.value);
    const claim = await api.registry.createClaim(registerForm.value.aircraft_id);
    claimResult.value = claim.code;
    await fleet.refreshAircraft();
  } finally {
    claimBusy.value = false;
  }
}
</script>

<template>
  <div>
    <el-alert
      v-if="!fleet.connected"
      type="info"
      :closable="false"
      title="正在连接地面站实时通道…"
      class="mb"
    />
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>局域网飞机（{{ rows.length }}）</span>
          <span>
            <el-button size="small" type="primary" @click="claimDialogVisible = true">
              登记新机 / 生成 claim
            </el-button>
            <el-button size="small" @click="fleet.refreshAircraft()">刷新</el-button>
          </span>
        </div>
      </template>
      <el-table :data="rows" empty-text="暂无飞机接入">
        <el-table-column label="飞机" min-width="180">
          <template #default="{ row }">
            <div class="name">{{ row.name }}</div>
            <div class="muted">{{ row.id }}</div>
          </template>
        </el-table-column>
        <el-table-column label="通道" width="110">
          <template #default="{ row }">
            <el-tag :type="channelTag(row.channel)" size="small">
              {{
                row.channel === "plane-link"
                  ? (row.proto === 2 ? "v2 长连接" : "v1 长连接")
                  : row.channel === "ssh"
                    ? "SSH"
                    : "未接入"
              }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="地址" width="140">
          <template #default="{ row }">
            <div>{{ row.last_ip ?? "—" }}</div>
            <div class="muted">
              {{
                row.static_ip
                  ? `登记 ${row.static_ip}${row.last_ip && row.last_ip !== row.static_ip ? "（不一致）" : ""}`
                  : "未登记 IP"
              }}
            </div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.online ? 'success' : 'danger'" size="small">
              {{ row.online ? "在线" : "离线" }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="M0 应用" width="150">
          <template #default="{ row }">
            <span v-if="row.live?.m0?.app_version">
              {{ row.live.m0.app_version }}
            </span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="飞控" width="120">
          <template #default="{ row }">
            <div v-if="row.live?.fcu">
              <el-tag
                :type="row.live.fcu.link === 'up' ? (row.live.fcu.armed ? 'danger' : 'success') : 'info'"
                size="small"
              >
                {{ row.live.fcu.link === "up" ? (row.live.fcu.armed ? "已解锁" : "待解锁") : "未连接" }}
              </el-tag>
              <div class="muted">{{ row.live.fcu.mode ?? "" }}</div>
            </div>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="CPU / 温度" width="140">
          <template #default="{ row }">
            <span v-if="row.live?.board">
              {{ row.live.board.cpu_pct ?? "—" }}% / {{ row.live.board.temp_c ?? "—" }}°C
            </span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="最近心跳" width="170">
          <template #default="{ row }">{{ fmt(row.last_seen) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="router.push(`/aircraft/${row.id}`)">
              详情
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="claimDialogVisible" title="登记飞机并生成 claim 码" width="480px">
      <el-form label-width="90px">
        <el-form-item label="别名 / ID">
          <el-input v-model="registerForm.aircraft_id" placeholder="如 lyra-01" />
        </el-form-item>
        <el-form-item label="显示名">
          <el-input v-model="registerForm.name" placeholder="可选" />
        </el-form-item>
        <el-form-item label="静态 IP">
          <el-input v-model="registerForm.static_ip" placeholder="路由器固定 IP" />
        </el-form-item>
        <el-form-item label="型号">
          <el-input v-model="registerForm.model" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="claimDialogVisible = false">关闭</el-button>
        <el-button
          type="primary"
          :loading="claimBusy"
          :disabled="!registerForm.aircraft_id"
          @click="createRegistrationAndClaim"
        >
          登记并生成 claim
        </el-button>
      </template>
      <div v-if="claimResult" class="claim-code">
        在载板上执行：
        <code>flyctl init --claim {{ claimResult }} --ground {{ windowOrigin }}</code>
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.mb {
  margin-bottom: 12px;
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.name {
  font-weight: 600;
}
.claim-code {
  margin-top: 10px;
  font-size: 13px;
}
</style>
