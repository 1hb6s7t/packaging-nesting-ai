<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { apiRequest } from "../services/api";

type OperationLog = {
  id: string;
  actor_id: string | null;
  action: string;
  target_type: string;
  target_id: string | null;
  payload: Record<string, unknown>;
  created_at: string;
};

const rows = ref<OperationLog[]>([]);
const error = ref("");
const loading = ref(false);
const filters = reactive({
  action: "",
  target_type: "",
  target_id: "",
  actor_id: "",
  created_from: "",
  created_to: "",
  limit: 200
});

function formatPayload(payload: Record<string, unknown>) {
  const text = JSON.stringify(payload ?? {}, null, 2);
  return text.length > 1200 ? `${text.slice(0, 1200)}...` : text;
}

async function loadLogs() {
  loading.value = true;
  error.value = "";
  try {
    const params = new URLSearchParams();
    params.set("limit", String(filters.limit || 200));
    for (const key of ["action", "target_type", "target_id", "actor_id", "created_from", "created_to"] as const) {
      const value = filters[key].trim();
      if (value) {
        params.set(key, value);
      }
    }
    rows.value = await apiRequest<OperationLog[]>(`/operation-logs?${params.toString()}`);
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    loading.value = false;
  }
}

function resetFilters() {
  filters.action = "";
  filters.target_type = "";
  filters.target_id = "";
  filters.actor_id = "";
  filters.created_from = "";
  filters.created_to = "";
  filters.limit = 200;
  void loadLogs();
}

onMounted(loadLogs);
</script>

<template>
  <section>
    <div class="page-header">
      <div>
        <h1 class="page-title">操作日志</h1>
        <div class="page-subtitle">写操作审计、目标对象、结构化 payload</div>
      </div>
      <el-button type="primary" :loading="loading" @click="loadLogs">刷新</el-button>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" style="margin-bottom: 12px" />
    <div class="work-band">
      <el-form class="audit-filters" label-width="76px">
        <el-form-item label="动作">
          <el-input v-model="filters.action" clearable placeholder="例如 auth.login_failed" />
        </el-form-item>
        <el-form-item label="对象类型">
          <el-input v-model="filters.target_type" clearable placeholder="例如 adapter_config" />
        </el-form-item>
        <el-form-item label="对象 ID">
          <el-input v-model="filters.target_id" clearable />
        </el-form-item>
        <el-form-item label="操作者">
          <el-input v-model="filters.actor_id" clearable />
        </el-form-item>
        <el-form-item label="开始时间">
          <el-input v-model="filters.created_from" clearable type="datetime-local" />
        </el-form-item>
        <el-form-item label="结束时间">
          <el-input v-model="filters.created_to" clearable type="datetime-local" />
        </el-form-item>
        <el-form-item label="条数">
          <el-input-number v-model="filters.limit" :min="1" :max="500" :step="50" />
        </el-form-item>
        <el-form-item class="filter-actions">
          <el-button type="primary" :loading="loading" @click="loadLogs">筛选</el-button>
          <el-button @click="resetFilters">重置</el-button>
        </el-form-item>
      </el-form>
      <el-table :data="rows" border v-loading="loading">
        <el-table-column prop="created_at" label="时间" min-width="180" />
        <el-table-column prop="actor_id" label="操作者" width="110" />
        <el-table-column prop="action" label="动作" min-width="180" />
        <el-table-column prop="target_type" label="对象类型" min-width="140" />
        <el-table-column prop="target_id" label="对象 ID" min-width="170" />
        <el-table-column label="Payload" min-width="360">
          <template #default="{ row }">
            <pre class="payload-preview">{{ formatPayload(row.payload) }}</pre>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </section>
</template>

<style scoped>
.audit-filters {
  display: grid;
  grid-template-columns: repeat(4, minmax(180px, 1fr));
  gap: 10px 12px;
  margin-bottom: 14px;
}

.audit-filters :deep(.el-form-item) {
  margin-bottom: 0;
}

.filter-actions {
  align-items: flex-end;
}

.payload-preview {
  max-height: 128px;
  margin: 0;
  overflow: auto;
  color: #334155;
  font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
  font-size: 12px;
  line-height: 1.45;
  white-space: pre-wrap;
  word-break: break-word;
}

@media (max-width: 1100px) {
  .audit-filters {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 640px) {
  .audit-filters {
    grid-template-columns: 1fr;
  }
}
</style>
