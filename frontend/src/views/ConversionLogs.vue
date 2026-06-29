<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import { FileClock, RefreshCw, Save, Search, Send, UploadCloud, X } from "@lucide/vue";
import { apiRequest } from "../services/api";

type ConversionStatus = "queued" | "completed" | "failed" | "manual_required" | "skipped" | "overdue";

type ConversionJob = {
  id: string;
  artwork_file_id: string;
  source_format: string;
  target_format: string;
  status: ConversionStatus;
  log?: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

type ConversionResult = {
  job: ConversionJob;
  polygon_count: number;
  polygon_storage_key?: string | null;
  message: string;
};

const loading = ref(false);
const saving = ref(false);
const submitting = ref(false);
const checkingSla = ref(false);
const error = ref("");
const jobs = ref<ConversionJob[]>([]);
const selectedJob = ref<ConversionJob | null>(null);

const filters = reactive({
  artwork_id: "",
  status: ""
});

const editor = reactive({
  status: "manual_required" as ConversionStatus,
  log: ""
});

const resultForm = reactive({
  content: "",
  log: "",
  parse_polygon: true
});
const submitForm = reactive({
  sla_minutes: 120,
  rotate_callback_token: false
});

const manualCount = computed(() => jobs.value.filter((row) => row.status === "manual_required").length);
const failedCount = computed(() => jobs.value.filter((row) => row.status === "failed").length);
const resolvedCount = computed(() => jobs.value.filter((row) => row.status === "completed" || row.status === "skipped").length);

function statusType(status: ConversionStatus) {
  if (status === "completed") return "success";
  if (status === "failed" || status === "overdue") return "danger";
  if (status === "manual_required") return "warning";
  if (status === "skipped") return "info";
  return "";
}

function formatTime(value: string) {
  return value ? new Date(value).toLocaleString() : "-";
}

function selectedMetadataText(key: string) {
  if (!selectedJob.value) return "-";
  const value = selectedJob.value.metadata?.[key];
  return value === undefined || value === null || value === "" ? "-" : String(value);
}

function selectedHashPreview(key: string) {
  const value = selectedMetadataText(key);
  return value === "-" ? value : `${value.slice(0, 12)}...`;
}

function buildQuery() {
  const params = new URLSearchParams({ limit: "100" });
  if (filters.artwork_id.trim()) {
    params.set("artwork_id", filters.artwork_id.trim());
  }
  if (filters.status) {
    params.set("status", filters.status);
  }
  return params.toString();
}

function selectJob(row: ConversionJob) {
  selectedJob.value = row;
  editor.status = row.status;
  editor.log = row.log || "";
  resultForm.log = row.log || "";
}

function clearFilters() {
  filters.artwork_id = "";
  filters.status = "";
  void loadJobs();
}

async function loadJobs() {
  loading.value = true;
  error.value = "";
  try {
    jobs.value = await apiRequest<ConversionJob[]>(`/artworks/conversion-jobs?${buildQuery()}`);
    if (selectedJob.value) {
      const refreshed = jobs.value.find((row) => row.id === selectedJob.value?.id);
      selectedJob.value = refreshed || null;
      if (refreshed) {
        editor.status = refreshed.status;
        editor.log = refreshed.log || "";
      }
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    loading.value = false;
  }
}

async function updateJob() {
  if (!selectedJob.value) return;
  saving.value = true;
  error.value = "";
  try {
    const updated = await apiRequest<ConversionJob>(`/artworks/conversion-jobs/${selectedJob.value.id}`, {
      method: "PATCH",
      body: JSON.stringify({ status: editor.status, log: editor.log || null })
    });
    ElMessage({ type: "success", message: "转换作业已更新" });
    selectedJob.value = updated;
    await loadJobs();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    saving.value = false;
  }
}

async function submitExternalJob() {
  if (!selectedJob.value) return;
  submitting.value = true;
  error.value = "";
  try {
    const result = await apiRequest<{ job: ConversionJob; message: string }>(`/artworks/conversion-jobs/${selectedJob.value.id}/submit`, {
      method: "POST",
      body: JSON.stringify({
        sla_minutes: submitForm.sla_minutes,
        rotate_callback_token: submitForm.rotate_callback_token
      })
    });
    ElMessage({ type: result.job.status === "failed" ? "warning" : "success", message: result.message });
    selectedJob.value = result.job;
    submitForm.rotate_callback_token = false;
    await loadJobs();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    submitting.value = false;
  }
}

async function checkSla() {
  checkingSla.value = true;
  error.value = "";
  try {
    const result = await apiRequest<{ status: string; overdue_count: number; notification_count: number }>(
      "/artworks/conversion-jobs/sla/check",
      {
        method: "POST",
        body: JSON.stringify({ notify: true })
      }
    );
    ElMessage({
      type: result.overdue_count ? "warning" : "success",
      message: result.overdue_count
        ? `发现 ${result.overdue_count} 个逾期转换作业，通知 ${result.notification_count} 条`
        : "转换 SLA 正常"
    });
    await loadJobs();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    checkingSla.value = false;
  }
}

async function applyNormalizedResult() {
  if (!selectedJob.value) return;
  saving.value = true;
  error.value = "";
  try {
    const result = await apiRequest<ConversionResult>(`/artworks/conversion-jobs/${selectedJob.value.id}/result`, {
      method: "POST",
      body: JSON.stringify({
        status: "completed",
        target_format: selectedJob.value.target_format,
        content: resultForm.content,
        log: resultForm.log || "Normalized artifact uploaded",
        parse_polygon: resultForm.parse_polygon
      })
    });
    ElMessage({ type: "success", message: `转换结果已回写，解析 ${result.polygon_count} 个 Polygon` });
    selectedJob.value = result.job;
    resultForm.content = "";
    await loadJobs();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    saving.value = false;
  }
}

onMounted(loadJobs);
</script>

<template>
  <section>
    <div class="page-header">
      <div>
        <h1 class="page-title">文件转换日志</h1>
        <div class="page-subtitle">归档 CDR/AI/PDF，记录人工或外部转换服务结果，SVG/DXF 直接进入解析流程</div>
      </div>
      <div class="header-actions">
        <el-button :loading="checkingSla" @click="checkSla">
          <FileClock :size="16" />
          SLA 巡检
        </el-button>
        <el-button type="primary" :loading="loading" @click="loadJobs">
          <RefreshCw :size="16" />
          刷新
        </el-button>
      </div>
    </div>

    <el-alert v-if="error" :title="error" type="error" :closable="false" style="margin-bottom: 16px" />

    <div class="metric-grid" style="margin-bottom: 16px">
      <div class="metric">
        <div class="metric-label">转换作业</div>
        <div class="metric-value">{{ jobs.length }}</div>
      </div>
      <div class="metric">
        <div class="metric-label">待人工处理</div>
        <div class="metric-value">{{ manualCount }}</div>
      </div>
      <div class="metric">
        <div class="metric-label">失败</div>
        <div class="metric-value">{{ failedCount }}</div>
      </div>
      <div class="metric">
        <div class="metric-label">已完成/跳过</div>
        <div class="metric-value">{{ resolvedCount }}</div>
      </div>
    </div>

    <div class="work-band">
      <div class="band-heading">
        <Search :size="16" />
        筛选
      </div>
      <div class="filter-row">
        <el-input v-model="filters.artwork_id" clearable placeholder="artwork_file_id" style="max-width: 360px" />
        <el-select v-model="filters.status" clearable placeholder="状态" style="width: 220px">
          <el-option label="queued" value="queued" />
          <el-option label="manual_required" value="manual_required" />
          <el-option label="completed" value="completed" />
          <el-option label="failed" value="failed" />
          <el-option label="skipped" value="skipped" />
          <el-option label="overdue" value="overdue" />
        </el-select>
        <el-button type="primary" :loading="loading" @click="loadJobs">
          <Search :size="16" />
          查询
        </el-button>
        <el-button @click="clearFilters">
          <X :size="16" />
          清空
        </el-button>
      </div>
    </div>

    <div class="two-column">
      <div class="work-band">
        <div class="band-heading">
          <FileClock :size="16" />
          作业更新
        </div>
        <el-alert
          v-if="!selectedJob"
          title="从右侧表格选择一条转换作业"
          type="info"
          :closable="false"
          style="margin-bottom: 14px"
        />
        <template v-else>
          <el-descriptions :column="1" border size="small" style="margin-bottom: 14px">
            <el-descriptions-item label="Job ID">{{ selectedJob.id }}</el-descriptions-item>
            <el-descriptions-item label="Artwork">{{ selectedJob.artwork_file_id }}</el-descriptions-item>
            <el-descriptions-item label="格式">{{ selectedJob.source_format }} -> {{ selectedJob.target_format }}</el-descriptions-item>
            <el-descriptions-item label="SLA 截止">{{ selectedMetadataText("sla_due_at") }}</el-descriptions-item>
            <el-descriptions-item label="提交次数">{{ selectedMetadataText("submit_attempt") === "-" ? 0 : selectedMetadataText("submit_attempt") }}</el-descriptions-item>
            <el-descriptions-item label="Token 尾号">{{ selectedMetadataText("callback_token_tail") }}</el-descriptions-item>
            <el-descriptions-item label="Token Hash">{{ selectedHashPreview("callback_token_hash") }}</el-descriptions-item>
            <el-descriptions-item label="Token 轮换">{{ selectedMetadataText("callback_token_rotated_at") }}</el-descriptions-item>
          </el-descriptions>
          <el-form label-position="top">
            <el-form-item label="状态">
              <el-select v-model="editor.status" style="width: 100%">
                <el-option label="queued" value="queued" />
                <el-option label="manual_required" value="manual_required" />
                <el-option label="completed" value="completed" />
                <el-option label="failed" value="failed" />
                <el-option label="skipped" value="skipped" />
                <el-option label="overdue" value="overdue" />
              </el-select>
            </el-form-item>
            <el-form-item label="日志">
              <el-input v-model="editor.log" type="textarea" :rows="8" maxlength="4000" show-word-limit />
            </el-form-item>
            <el-button type="primary" :loading="saving" @click="updateJob">
              <Save :size="16" />
              保存
            </el-button>
            <el-form-item label="外部转换 SLA 分钟" style="margin-top: 14px">
              <el-input-number v-model="submitForm.sla_minutes" :min="1" :max="1440" style="width: 100%" />
            </el-form-item>
            <el-form-item label="回调 Token 轮换">
              <el-switch
                v-model="submitForm.rotate_callback_token"
                active-text="提交时轮换"
                inactive-text="沿用现有"
              />
            </el-form-item>
            <el-button :loading="submitting" @click="submitExternalJob">
              <Send :size="16" />
              提交外部服务
            </el-button>
            <el-divider />
            <el-form-item label="Normalized SVG/DXF 内容">
              <el-input
                v-model="resultForm.content"
                type="textarea"
                :rows="8"
                spellcheck="false"
                placeholder="<svg ...> 或 DXF 文本"
              />
            </el-form-item>
            <el-form-item label="回写日志">
              <el-input v-model="resultForm.log" maxlength="4000" show-word-limit />
            </el-form-item>
            <el-form-item label="解析 Polygon">
              <el-switch v-model="resultForm.parse_polygon" />
            </el-form-item>
            <el-button
              type="success"
              :loading="saving"
              :disabled="!resultForm.content.trim() || selectedJob.status === 'completed'"
              @click="applyNormalizedResult"
            >
              <UploadCloud :size="16" />
              回写转换结果
            </el-button>
          </el-form>
        </template>
      </div>

      <div class="work-band">
        <div class="band-heading">转换作业列表</div>
        <el-table v-loading="loading" :data="jobs" border @row-click="selectJob">
          <el-table-column prop="id" label="Job ID" min-width="220" show-overflow-tooltip />
          <el-table-column prop="artwork_file_id" label="Artwork" min-width="190" show-overflow-tooltip />
          <el-table-column prop="source_format" label="源格式" width="90" />
          <el-table-column prop="target_format" label="目标" width="90" />
          <el-table-column label="状态" width="140">
            <template #default="{ row }">
              <el-tag :type="statusType(row.status)">{{ row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="log" label="日志" min-width="260" show-overflow-tooltip />
          <el-table-column label="SLA 截止" min-width="180">
            <template #default="{ row }">{{ row.metadata?.sla_due_at || "-" }}</template>
          </el-table-column>
          <el-table-column label="创建时间" width="180">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="90" fixed="right">
            <template #default="{ row }">
              <el-button size="small" @click.stop="selectJob(row)">编辑</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </div>
  </section>
</template>

<style scoped>
.filter-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
}

.header-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  justify-content: flex-end;
}
</style>
