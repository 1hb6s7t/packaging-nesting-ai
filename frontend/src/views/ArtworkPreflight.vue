<script setup lang="ts">
import { ref } from "vue";
import { apiRequest } from "../services/api";

const filename = ref("sample-box.svg");
const content = ref('<svg width="120" height="80"><rect id="cut" x="0" y="0" width="120" height="80"/></svg>');
const report = ref<Record<string, unknown> | null>(null);

async function run() {
  report.value = await apiRequest("/artworks/preflight", {
    method: "POST",
    body: JSON.stringify({ filename: filename.value, content_type: "image/svg+xml", content: content.value })
  });
}
</script>

<template>
  <section>
    <div class="page-header">
      <div>
        <h1 class="page-title">版图预检</h1>
        <div class="page-subtitle">格式、单位、图层、刀线风险</div>
      </div>
      <el-button type="primary" @click="run">预检</el-button>
    </div>
    <div class="two-column">
      <div class="work-band">
        <el-input v-model="filename" />
        <el-input v-model="content" type="textarea" :rows="14" style="margin-top: 12px" />
      </div>
      <div class="work-band">
        <pre class="pre-wrap">{{ report }}</pre>
      </div>
    </div>
  </section>
</template>

