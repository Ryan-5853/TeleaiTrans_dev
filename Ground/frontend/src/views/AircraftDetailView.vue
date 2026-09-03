<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api } from "@/api";
import { useFleetStore } from "@/stores/fleet";
import type { Artifact, FlashJob } from "@/types";

const fleet = useFleetStore();
const route = useRoute();
const router = useRouter();

const aircraftId = computed(() => String(route.params.id || ""));
const aircraft = computed(() =>
  fleet.aircraft.find((item) => item.id === aircraftId.value)
);
const live = computed(
  () => fleet.telemetry[aircraftId.value] ?? aircraft.value?.telemetry
);

const canMaintenance = computed(
  () =>
    aircraft.value?.online === true &&
    aircraft.value.channel === "plane-link" &&
    (aircraft.value.capabilities ?? []).includes("maintenance")
);
const canFlash = computed(() => {
  const item = aircraft.value;
  if (!item) return false;
  return (
    item.channel === "ssh" ||
    (item.online && (item.capabilities ?? []).includes("m0_flash"))
  );
});

const paramsText = ref("{}");
const paramsLoading = ref(false);

async function loadParams() {
  paramsLoading.value = true;
  try {
    const data = await api.aircraft.params(aircraftId.value);
    paramsText.value = JSON.stringify(data.params, null, 2);
  } catch (error) {
    ElMessage.error((error as Error).message);
  } finally {
    paramsLoading.value = false;
  }
}

async function applyParams() {
  let values: Record<string, unknown>;
  try {
    values = JSON.parse(paramsText.value);
  } catch {
    ElMessage.error("参数必须是合法 JSON 对象");
    return;
  }
  if (!values || Array.isArray(values) || typeof values !== "object") {
    ElMessage.error("参数必须是 JSON 对象");
    return;
  }
  try {
    const data = await api.aircraft.writeParams(aircraftId.value, values);
    paramsText.value = JSON.stringify(data.params, null, 2);
    ElMessage.success("参数已写入并确认");
  } catch (error) {
    ElMessage.error((error as Error).message);
  }
}

async function sendCommand(op: string, label: string, params: Record<string, unknown> = {}) {
  try {
    await ElMessageBox.confirm(
      `确认对 ${aircraft.value?.name ?? aircraftId.value} 执行“${label}”？`,
      "操作确认",
      { type: "warning", confirmButtonText: "确认执行", cancelButtonText: "取消" }
    );
  } catch {
    return;
  }
  try {
    await api.aircraft.command(aircraftId.value, op, params);
    ElMessage.success(`已下发 ${label}（结果将实时推送）`);
  } catch (error) {
    ElMessage.error((error as Error).message);
  }
}

const artifacts = ref<Artifact[]>([]);
const selectedArtifact = ref("");
const jobs = ref<FlashJob[]>([]);
const artifactsLoading = ref(false);

async function loadArtifacts() {
  artifactsLoading.value = true;
  try {
    artifacts.value = (await api.artifacts.list()).artifacts;
  } catch (error) {
    ElMessage.error((error as Error).message);
  } finally {
    artifactsLoading.value = false;
  }
}

async function loadJobs() {
  try {
    jobs.value = (await api.flash.list(aircraftId.value)).jobs;
  } catch (error) {
    ElMessage.error((error as Error).message);
  }
}

const displayJobs = computed(() => {
  const map = new Map<string, FlashJob>();
  for (const job of jobs.value) map.set(job.id, job);
  for (const job of Object.values(fleet.jobs)) {
    if (job.aircraft_id === aircraftId.value) map.set(job.id, job);
  }
  return [...map.values()].sort((a, b) => (b.started_at ?? 0) - (a.started_at ?? 0));
});

async function pollJob(jobId: string) {
  try {
    const { job } = await api.flash.get(jobId);
    fleet.upsertJob(job);
    if (job.status === "running" || job.status === "queued") {
      window.setTimeout(() => void pollJob(jobId), 800);
    }
  } catch {
    /* job may not be visible yet; one retry later */
    window.setTimeout(() => void pollJob(jobId), 1500);
  }
}

async function startFlash() {
  if (!selectedArtifact.value) {
    ElMessage.warning("请先选择要烧录的 M0 固件产物");
    return;
  }
  try {
    const result = await api.flash.start(aircraftId.value, selectedArtifact.value);
    fleet.upsertJob(result.job);
    void pollJob(result.job.id);
    ElMessage.success("烧录作业已启动");
  } catch (error) {
    ElMessage.error((error as Error).message);
  }
}

function jobStatusType(status: FlashJob["status"]): "success" | "danger" | "primary" {
  if (status === "success") return "success";
  if (status === "failed") return "danger";
  return "primary";
}

function fmt(ts?: number | null): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false });
}

function openCameraDebug() {
  window.open(
    `/api/v2/aircraft/${encodeURIComponent(aircraftId.value)}/camera/ui`,
    "_blank",
    "noopener"
  );
}

onMounted(() => {
  void fleet.refreshAircraft();
  void loadArtifacts();
  void loadJobs();
});
</script>

<template>
  <div v-if="aircraft">
    <el-page-header @back="router.push('/')">
      <template #content>
        <span class="title">{{ aircraft.name }}</span>
        <el-tag size="small" class="ml">
          {{ aircraft.id }}
        </el-tag>
        <el-tag
          size="small"
          class="ml"
          :type="aircraft.online ? 'success' : 'danger'"
        >
          {{ aircraft.online ? "在线" : "离线" }}
        </el-tag>
        <el-button
          class="ml"
          size="small"
          type="primary"
          plain
          @click="openCameraDebug"
        >
          图传 / 图像调试
        </el-button>
      </template>
    </el-page-header>

    <el-row :gutter="12" class="top-cards">
      <el-col :span="6">
        <el-card shadow="never">
          <template #header>板级状态</template>
          <el-descriptions :column="1" size="small">
            <el-descriptions-item label="CPU">
              {{ live?.board?.cpu_pct ?? "—" }}%
            </el-descriptions-item>
            <el-descriptions-item label="内存">
              {{ live?.board?.mem_pct ?? "—" }}%
            </el-descriptions-item>
            <el-descriptions-item label="温度">
              {{ live?.board?.temp_c ?? "—" }}°C
            </el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never">
          <template #header>M0</template>
          <el-descriptions :column="1" size="small">
            <el-descriptions-item label="状态">
              {{ live?.m0?.status ?? aircraft.m0?.status ?? "—" }}
            </el-descriptions-item>
            <el-descriptions-item label="App 版本">
              {{ live?.m0?.app_version ?? aircraft.m0?.app_version ?? "—" }}
            </el-descriptions-item>
            <el-descriptions-item label="armed">
              {{ live?.m0?.armed ?? "—" }}
            </el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never">
          <template #header>通道</template>
          <el-descriptions :column="1" size="small">
            <el-descriptions-item label="类型">
              {{
                aircraft.channel === "plane-link"
                  ? "飞机长连接"
                  : aircraft.channel === "ssh"
                    ? "SSH-only"
                    : "未接入"
              }}
            </el-descriptions-item>
            <el-descriptions-item label="Agent">
              {{ aircraft.agent_version ?? "—" }}
            </el-descriptions-item>
            <el-descriptions-item label="能力">
              {{ aircraft.capabilities.length ? aircraft.capabilities.join(", ") : "—" }}
            </el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never">
          <template #header>连接</template>
          <el-descriptions :column="1" size="small">
            <el-descriptions-item label="SSH 主机">
              {{ aircraft.ssh?.host ?? "—" }}
            </el-descriptions-item>
            <el-descriptions-item label="最近心跳">
              {{ aircraft.last_seen ? fmt(aircraft.last_seen) : "—" }}
            </el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="12">
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>
            <div class="card-head">
              <span>维护控制</span>
              <span v-if="!canMaintenance" class="muted">需要 plane-link + maintenance 能力</span>
            </div>
          </template>
          <div class="controls">
            <el-button type="warning" :disabled="!canMaintenance" @click="sendCommand('arm', 'arm')">
              Arm
            </el-button>
            <el-button type="danger" plain :disabled="!canMaintenance" @click="sendCommand('disarm', 'disarm')">
              Disarm
            </el-button>
            <el-button :disabled="!canMaintenance" @click="sendCommand('motor_test', '电机测试', { duration_s: 2 })">
              电机测试
            </el-button>
            <el-button :disabled="!canMaintenance" @click="sendCommand('servo_test', '舵机测试', { duration_s: 2 })">
              舵机测试
            </el-button>
            <el-button :disabled="!canMaintenance" @click="sendCommand('stop_test', '停止测试')">
              停止测试
            </el-button>
          </div>
        </el-card>

        <el-card shadow="never" class="mt">
          <template #header>
            <div class="card-head">
              <span>参数（JSON）</span>
              <div>
                <el-button size="small" :loading="paramsLoading" @click="loadParams">
                  读取
                </el-button>
                <el-button size="small" type="primary" @click="applyParams">
                  写入并确认
                </el-button>
              </div>
            </div>
          </template>
          <el-input
            v-model="paramsText"
            type="textarea"
            :rows="10"
            spellcheck="false"
            class="mono"
          />
        </el-card>
      </el-col>

      <el-col :span="12">
        <el-card shadow="never">
          <template #header>
            <div class="card-head">
              <span>M0 固件远程烧录</span>
              <el-button
                size="small"
                type="primary"
                :disabled="!canFlash || !selectedArtifact"
                @click="startFlash"
              >
                开始烧录
              </el-button>
            </div>
          </template>
          <el-select
            v-model="selectedArtifact"
            placeholder="选择固件产物"
            filterable
            :loading="artifactsLoading"
            class="full"
          >
            <el-option
              v-for="artifact in artifacts"
              :key="artifact.id"
              :value="artifact.id"
              :label="`${artifact.filename} · ${artifact.id.slice(0, 12)}… (${artifact.size} B)`"
            />
          </el-select>
          <el-alert
            v-if="aircraft.channel === 'ssh'"
            class="mt"
            type="info"
            :closable="false"
            title="SSH-only 真机通道：将经 SSH 调用板上 m0load 完成 M0 热更新"
          />

          <template v-for="job in displayJobs" :key="job.id">
            <el-divider content-position="left">作业 {{ job.id.slice(0, 8) }}</el-divider>
            <el-progress
              :percentage="job.percent"
              :status="job.status === 'failed' ? 'exception' : job.status === 'success' ? 'success' : undefined"
            />
            <div class="job-meta">
              <el-tag :type="jobStatusType(job.status)" size="small">
                {{ job.status }} · {{ job.phase }}
              </el-tag>
              <span class="muted">{{ fmt(job.started_at) }}</span>
            </div>
            <pre v-if="job.log_tail || job.error" class="log">{{
              job.error ? `${job.error}\n${job.log_tail}` : job.log_tail
            }}</pre>
          </template>
          <el-empty
            v-if="displayJobs.length === 0"
            description="暂无烧录记录"
            :image-size="60"
          />
        </el-card>
      </el-col>
    </el-row>
  </div>
  <el-result
    v-else
    icon="warning"
    title="飞机不存在或尚未注册"
    sub-title="请返回飞机总览后重试"
  >
    <template #extra>
      <el-button type="primary" @click="router.push('/')">返回总览</el-button>
    </template>
  </el-result>
</template>

<style scoped>
.title {
  font-size: 18px;
  font-weight: 600;
  margin-right: 6px;
}
.ml {
  margin-left: 6px;
}
.top-cards {
  margin-top: 16px;
}
.mt {
  margin-top: 12px;
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.controls {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.full {
  width: 100%;
}
.mono :deep(textarea),
.log {
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  font-size: 12px;
}
.job-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 6px 0;
}
.log {
  max-height: 220px;
  overflow: auto;
  margin: 0;
  padding: 8px;
  background: #0d1117;
  color: #c9d1d9;
  border-radius: 6px;
  white-space: pre-wrap;
}
</style>
