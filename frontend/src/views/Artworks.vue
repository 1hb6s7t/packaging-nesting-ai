<script setup lang="ts">
import { ref } from "vue";
import { Upload } from "@lucide/vue";
import { apiRequest } from "../services/api";
import SvgPreview from "../components/SvgPreview.vue";

const uploadResult = ref<Record<string, unknown> | null>(null);
const parseResult = ref<Record<string, unknown> | null>(null);
const previewSvg = ref("");
const error = ref("");

async function handleFile(file: File) {
  error.value = "";
  const form = new FormData();
  form.append("file", file);
  try {
    uploadResult.value = await apiRequest("/artworks/upload", { method: "POST", body: form });
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  }
  return false;
}

async function parsePolygon() {
  error.value = "";
  const artworkId = uploadResult.value?.artwork_id;
  if (!artworkId || typeof artworkId !== "string") {
    error.value = "请先上传版图文件";
    return;
  }
  try {
    parseResult.value = await apiRequest(`/artworks/${artworkId}/parse-polygon`, { method: "POST" });
    previewSvg.value = await apiRequest<string>(`/artworks/${artworkId}/preview`);
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  }
}
</script>

<template>
  <section>
    <div class="page-header">
      <div>
        <h1 class="page-title">版图上传</h1>
        <div class="page-subtitle">SVG/DXF 优先进入 Polygon 标准化</div>
      </div>
    </div>
    <div class="work-band">
      <el-upload drag :auto-upload="false" :before-upload="handleFile">
        <Upload :size="26" />
        <div style="margin-top: 8px">SVG / DXF / PDF / CDR / AI / EPS / PLT</div>
      </el-upload>
      <el-button type="primary" style="margin-top: 12px" @click="parsePolygon">解析 Polygon</el-button>
      <el-alert v-if="error" :title="error" type="error" :closable="false" style="margin-top: 12px" />
    </div>
    <div class="two-column">
      <div class="work-band">
        <pre class="pre-wrap">{{ uploadResult }}</pre>
        <pre class="pre-wrap">{{ parseResult }}</pre>
      </div>
      <SvgPreview :svg="previewSvg" />
    </div>
  </section>
</template>
