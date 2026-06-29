<script setup lang="ts">
defineProps<{ svg?: string }>();
</script>

<template>
  <div class="svg-frame">
    <div v-if="svg" v-html="svg"></div>
    <el-empty v-else description="无预览" />
  </div>
</template>

