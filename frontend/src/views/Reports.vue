<script setup lang="ts">
import { ref } from "vue";
import { apiRequest } from "../services/api";
import { useAppStore } from "../stores/app";

const solutionId = ref(useAppStore().lastSolutionId);
const report = ref<Record<string, unknown> | null>(null);

async function load() {
  report.value = await apiRequest(`/solutions/${solutionId.value}/report`);
}
</script>

<template>
  <section>
    <div class="page-header">
      <div>
        <h1 class="page-title">利用率 / 成本报告</h1>
        <div class="page-subtitle">Solver 输出、Validator 报告、成本指标</div>
      </div>
      <el-button type="primary" @click="load">生成</el-button>
    </div>
    <div class="work-band">
      <el-input v-model="solutionId" placeholder="solution_id" />
    </div>
    <div class="work-band"><pre class="pre-wrap">{{ report }}</pre></div>
  </section>
</template>

