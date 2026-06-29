<script setup lang="ts">
import { onMounted, ref } from "vue";
import { apiRequest } from "../services/api";

const apiStatus = ref("checking");
const aiToolCount = ref(0);

onMounted(async () => {
  try {
    await apiRequest("/health");
    const tools = await apiRequest<Array<{ name: string }>>("/ai/tools");
    aiToolCount.value = tools.length;
    apiStatus.value = "online";
  } catch {
    apiStatus.value = "offline";
  }
});
</script>

<template>
  <section>
    <div class="page-header">
      <div>
        <h1 class="page-title">Dashboard</h1>
        <div class="page-subtitle">订单、版图、Solver、Validator 和导出状态</div>
      </div>
      <el-tag :type="apiStatus === 'online' ? 'success' : apiStatus === 'offline' ? 'danger' : 'warning'">
        API {{ apiStatus }}
      </el-tag>
    </div>
    <div class="metric-grid">
      <div class="metric">
        <div class="metric-label">待拼版订单</div>
        <div class="metric-value">0</div>
      </div>
      <div class="metric">
        <div class="metric-label">Top-K 方案</div>
        <div class="metric-value">0</div>
      </div>
      <div class="metric">
        <div class="metric-label">Validator 失败</div>
        <div class="metric-value">0</div>
      </div>
      <div class="metric">
        <div class="metric-label">AI 工具</div>
        <div class="metric-value">{{ aiToolCount }}</div>
      </div>
    </div>
    <div class="work-band" style="margin-top: 16px">
      <el-steps :active="2" finish-status="success" align-center>
        <el-step title="文件标准化" />
        <el-step title="规则筛选" />
        <el-step title="Solver 求解" />
        <el-step title="Validator" />
        <el-step title="导出/报告" />
      </el-steps>
    </div>
  </section>
</template>

