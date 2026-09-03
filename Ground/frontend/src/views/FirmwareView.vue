<script setup lang="ts">
import { ElMessage, ElMessageBox, type UploadRequestOptions } from "element-plus";
import { computed, onMounted, ref } from "vue";

import { api } from "@/api";
import { useFleetStore } from "@/stores/fleet";
import type { Artifact, FlashJob } from "@/types";

const fleet = useFleetStore();
const artifacts = ref<Artifact[]>([]);
const uploading = ref(false);

async function loadArtifacts() {
  artifacts.value = (await api.artifacts.list()).artifacts;
}

async function customUpload(options: UploadRequestOptions) {
  uploading.value = true;
  try {
    const { artifact } = await api.artifacts.upload(options.file as File);
    ElMessage.success(`固件已收录：${artifact.filename}`);
    await loadArtifacts();
  } catch (error) {
    ElMessage.error((error as Error).message);
  } finally {
    uploading.value = false;
  }
}

async function removeArtifact(artifact: Artifact) {
  try {
    await ElMessageBox.confirm(
      `删除固件 ${artifact.filename}（${artifact.id.slice(0, 12)}…）？已发生的烧录历史不会删除。`,
      "删除确认",
      { type: "warning" }
    );
  } catch {
    return;
  }
  try {
    await api.artifacts.remove(artifact.id);
    await loadArtifacts();
    ElMessage.success("已删除");
  } catch (error) {
    ElMessage.error((error as Error).message);
  }
}

const fetchedJobs = ref<FlashJob[]>([]);
const jobs = computed(() => {
  const map = new Map<string, FlashJob>();
  for (const job of fetchedJobs.value) map.set(job.id, job);
  for (const job of Object.values(fleet.jobs)) map.set(job.id, job);
  return [...map.values()].sort((a, b) => (b.started_at ?? 0) - (a.started_at ?? 0));
});

async function loadJobs() {
  fetchedJobs.value = (await api.flash.list()).jobs;
}

function statusType(status: FlashJob["status"]): "success" | "danger" | "primary" {
  if (status === "success") return "success";
  if (status === "failed") return "danger";
  return "primary";
}

function fmt(ts?: number | null): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false });
}

onMounted(() => {
  void loadArtifacts();
  void loadJobs();
});
</script>

<template>
  <div>
    <el-card shadow="never" class="mb">
      <template #header>
        <div class="card-head">
          <span>固件产物（M0AP app.bin）</span>
          <el-upload
            :show-file-list="false"
            :http-request="customUpload"
            accept=".bin"
          >
            <el-button type="primary" :loading="uploading">上传固件</el-button>
          </el-upload>
        </div>
      </template>
      <el-table :data="artifacts" empty-text="暂无固件产物">
        <el-table-column label="文件名" min-width="180">
          <template #default="{ row }">
            <div>{{ row.filename }}</div>
            <div class="muted">{{ row.size }} 字节 · M0AP v{{ row.version }}</div>
          </template>
        </el-table-column>
        <el-table-column label="SHA-256 / ID" min-width="230">
          <template #default="{ row }">
            <code class="hash">{{ row.id }}</code>
          </template>
        </el-table-column>
        <el-table-column label="CRC32" width="130">
          <template #default="{ row }">
            <code>0x{{ row.crc32.toString(16).padStart(8, "0").toUpperCase() }}</code>
          </template>
        </el-table-column>
        <el-table-column label="上传时间" width="170">
          <template #default="{ row }">{{ fmt(row.uploaded_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button link type="danger" @click="removeArtifact(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>烧录作业（全部飞机）</span>
          <el-button size="small" @click="loadJobs()">刷新</el-button>
        </div>
      </template>
      <el-table :data="jobs" empty-text="暂无烧录作业">
        <el-table-column label="作业" width="140">
          <template #default="{ row }">
            <code>{{ row.id.slice(0, 10) }}</code>
          </template>
        </el-table-column>
        <el-table-column label="飞机" prop="aircraft_id" width="140" />
        <el-table-column label="固件" width="140">
          <template #default="{ row }">
            <code>{{ row.artifact_id.slice(0, 10) }}…</code>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="进度" width="170">
          <template #default="{ row }">
            <el-progress
              :percentage="row.percent"
              :status="row.status === 'failed' ? 'exception' : row.status === 'success' ? 'success' : undefined"
              :stroke-width="10"
            />
          </template>
        </el-table-column>
        <el-table-column label="开始" width="170">
          <template #default="{ row }">{{ fmt(row.started_at) }}</template>
        </el-table-column>
        <el-table-column label="日志/错误" min-width="200">
          <template #default="{ row }">
            <span v-if="row.error" class="err">{{ row.error }}</span>
            <span v-else-if="row.log_tail" class="muted">{{ row.log_tail.split("\n").pop() }}</span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
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
.hash {
  font-size: 12px;
  word-break: break-all;
}
.err {
  color: #f56c6c;
  font-size: 12px;
}
</style>
