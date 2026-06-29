<script setup lang="ts">
defineProps<{
  title: string;
  subtitle?: string;
  rows?: Array<Record<string, string | number>>;
}>();
</script>

<template>
  <section>
    <div class="page-header">
      <div>
        <h1 class="page-title">{{ title }}</h1>
        <div v-if="subtitle" class="page-subtitle">{{ subtitle }}</div>
      </div>
    </div>
    <div class="work-band">
      <el-table :data="rows || []" border>
        <el-table-column prop="name" label="名称" min-width="180" />
        <el-table-column prop="status" label="状态" min-width="160" />
        <el-table-column prop="owner" label="责任模块" min-width="180" />
      </el-table>
    </div>
  </section>
</template>

