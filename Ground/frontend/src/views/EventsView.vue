<script setup lang="ts">
import { computed, onMounted } from "vue";

import { api } from "@/api";
import { useFleetStore } from "@/stores/fleet";

const fleet = useFleetStore();

const events = computed(() => {
  const all = [...fleet.events];
  return all.sort((a, b) => b.ts - a.ts).slice(0, 300);
});

function tagType(level: string): "success" | "warning" | "danger" | "info" {
  if (level === "info") return "info";
  if (level === "warn") return "warning";
  if (level === "success") return "success";
  return "danger";
}

function fmt(ts: number): string {
  return new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false });
}

onMounted(async () => {
  try {
    const data = await api.events.list();
    for (const event of data.events) fleet.pushEvent(event);
  } catch {
    /* live socket will catch up */
  }
});
</script>

<template>
  <el-card shadow="never">
    <template #header>
      <span>事件与操作记录（{{ events.length }}）</span>
    </template>
    <el-table :data="events" empty-text="暂无事件">
      <el-table-column label="时间" width="180">
        <template #default="{ row }">{{ fmt(row.ts) }}</template>
      </el-table-column>
      <el-table-column label="级别" width="90">
        <template #default="{ row }">
          <el-tag :type="tagType(row.level)" size="small">{{ row.level }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="飞机" width="140">
        <template #default="{ row }">
          {{ row.aircraft_id ?? "系统" }}
        </template>
      </el-table-column>
      <el-table-column label="代码" width="200">
        <template #default="{ row }">
          <code>{{ row.code }}</code>
        </template>
      </el-table-column>
      <el-table-column label="内容" min-width="260">
        <template #default="{ row }">{{ row.message }}</template>
      </el-table-column>
    </el-table>
  </el-card>
</template>
