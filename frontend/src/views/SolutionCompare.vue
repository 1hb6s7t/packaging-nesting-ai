<script setup lang="ts">
import ModulePage from "../components/ModulePage.vue";

const rows = [
  { name: "最高利用率方案", status: "SolutionScore 排序", owner: "Scoring" },
  { name: "最低生产风险方案", status: "Validator issues", owner: "Validator" },
  { name: "人工可调整空间", status: "预留指标", owner: "Solution Manager" }
];
</script>

<template>
  <ModulePage title="方案对比" subtitle="Top-K 方案多指标比较" :rows="rows" />
</template>

