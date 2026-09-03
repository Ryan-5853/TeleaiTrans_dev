<script setup lang="ts">
import { onBeforeUnmount, onMounted } from "vue";

import { useFleetStore } from "@/stores/fleet";

const fleet = useFleetStore();

onMounted(() => {
  fleet.connect();
});
onBeforeUnmount(() => {
  fleet.disconnect();
});
</script>

<template>
  <el-container class="layout">
    <el-header class="header">
      <div class="brand">
        <el-icon :size="22"><Aim /></el-icon>
        <span>Ground 地面站</span>
      </div>
      <el-menu
        mode="horizontal"
        :default-active="$route.path"
        router
        class="menu"
        :ellipsis="false"
      >
        <el-menu-item index="/">飞机总览</el-menu-item>
        <el-menu-item index="/firmware">固件与烧录</el-menu-item>
        <el-menu-item index="/events">事件记录</el-menu-item>
      </el-menu>
      <div class="conn">
        <el-tag :type="fleet.connected ? 'success' : 'info'" size="small">
          {{ fleet.connected ? "实时连接" : "连接断开" }}
        </el-tag>
      </div>
    </el-header>
    <el-main>
      <router-view />
    </el-main>
    <el-footer class="footer">
      <span class="muted">局域网地面站 · 仅限可信网络使用，请勿暴露到公网</span>
    </el-footer>
  </el-container>
</template>

<style scoped>
.layout {
  min-height: 100vh;
}
.header {
  display: flex;
  align-items: center;
  gap: 28px;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
}
.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  white-space: nowrap;
}
.menu {
  flex: 1;
  border-bottom: none;
}
.footer {
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>
