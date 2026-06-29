<script setup lang="ts">
import { computed, ref } from "vue";
import { ElMessageBox } from "element-plus";
import { Archive, Download, FileInput, PackageCheck, ScrollText, Send, ShieldCheck, XCircle } from "@lucide/vue";
import SvgPreview from "../components/SvgPreview.vue";
import { apiErrorFromResponse, apiFetch, apiRequest } from "../services/api";
import { useAppStore } from "../stores/app";

interface NestingSolution {
  solution_id: string;
  job_id: string;
  solver: string;
  status: string;
  rank: number;
  utilization_rate: number;
  waste_rate: number;
}

interface SolutionApproval {
  id: string;
  solution_id: string;
  requested_by: string;
  decided_by?: string | null;
  status: "pending" | "approved" | "rejected";
  request_note?: string | null;
  decision_note?: string | null;
  created_at: string;
  updated_at: string;
}

interface SolutionExport {
  id: string;
  solution_id: string;
  export_type: "pdf" | "dxf";
  version: number;
  lifecycle_status: "active" | "superseded" | "archived";
  retention_until?: string | null;
  superseded_by_export_id?: string | null;
  storage_key: string;
  checksum?: string | null;
  storage_backend?: string | null;
  storage_object_key?: string | null;
  storage_version_id?: string | null;
  storage_etag?: string | null;
  storage_size_bytes?: number | null;
  status: string;
  download_path: string;
  created_at: string;
  updated_at: string;
}

interface RecoveryItem {
  export_id: string;
  export_type: "pdf" | "dxf";
  version: number;
  lifecycle_status: "active" | "superseded" | "archived";
  storage_backend: string;
  object_key: string;
  expected_storage_version_id?: string | null;
  actual_storage_version_id?: string | null;
  expected_etag?: string | null;
  actual_etag?: string | null;
  storage_exists: boolean;
  size_bytes?: number | null;
  expected_checksum?: string | null;
  actual_checksum?: string | null;
  status: "ok" | "missing" | "unreadable" | "checksum_mismatch" | "version_mismatch";
  error?: string | null;
}

interface RecoveryReport {
  solution_id: string;
  generated_at: string;
  status: "passed" | "failed";
  checked_count: number;
  ok_count: number;
  missing_count: number;
  unreadable_count: number;
  checksum_mismatch_count: number;
  version_mismatch_count: number;
  archive_dry_run?: { checked_count: number; archived_count: number; status: string } | null;
  items: RecoveryItem[];
}

const appStore = useAppStore();
const solutionId = ref(appStore.lastSolutionId);
const solution = ref<NestingSolution | null>(null);
const report = ref<Record<string, unknown> | null>(null);
const approvals = ref<SolutionApproval[]>([]);
const exports = ref<SolutionExport[]>([]);
const manifest = ref<Record<string, unknown> | null>(null);
const recoveryReport = ref<RecoveryReport | null>(null);
const svg = ref("");
const error = ref("");
const loading = ref(false);
const approvalNote = ref("");
const decisionNote = ref("");
const archiveDryRun = ref(false);

const latestApproval = computed(() => approvals.value[0]);
const isApproved = computed(() => solution.value?.status === "approved");
const hasPendingApproval = computed(() => latestApproval.value?.status === "pending");
const canWriteSolutions = computed(() => appStore.hasPermission("solutions:write"));
const canApproveSolutions = computed(() => appStore.hasPermission("solutions:approve"));
const canExportSolutions = computed(() => appStore.hasPermission("solutions:export"));
const canArchiveSolutions = computed(() => appStore.hasPermission("solutions:archive"));
const canReadExports = computed(() => appStore.hasAnyPermission(["solutions:export", "solutions:archive"]));

function statusType(status?: string) {
  if (status === "approved" || status === "valid" || status === "ready" || status === "passed" || status === "ok") return "success";
  if (status === "rejected" || status === "invalid" || status === "failed" || status === "missing" || status === "unreadable" || status === "checksum_mismatch" || status === "version_mismatch") return "danger";
  if (status === "pending" || status === "pending_approval") return "warning";
  return "info";
}

async function load() {
  if (!solutionId.value) {
    error.value = "请输入 solution_id";
    return;
  }
  loading.value = true;
  error.value = "";
  try {
    const [solutionPayload, reportPayload, approvalRows, exportRows, exportManifest, preview] = await Promise.all([
      apiRequest<NestingSolution>(`/solutions/${solutionId.value}`),
      apiRequest<Record<string, unknown>>(`/solutions/${solutionId.value}/report`),
      apiRequest<SolutionApproval[]>(`/solutions/${solutionId.value}/approval`),
      canReadExports.value ? apiRequest<SolutionExport[]>(`/solutions/${solutionId.value}/exports`) : Promise.resolve([]),
      canArchiveSolutions.value
        ? apiRequest<Record<string, unknown>>(`/solutions/${solutionId.value}/exports/manifest`)
        : Promise.resolve(null),
      apiRequest<string>(`/solutions/${solutionId.value}/preview.svg`)
    ]);
    solution.value = solutionPayload;
    report.value = reportPayload;
    approvals.value = approvalRows;
    exports.value = exportRows;
    manifest.value = exportManifest;
    svg.value = preview;
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    loading.value = false;
  }
}

async function validateSolution() {
  if (!canWriteSolutions.value) {
    error.value = "缺少权限：solutions:write";
    return;
  }
  await runAction(async () => {
    await apiRequest(`/solutions/${solutionId.value}/validate`, { method: "POST" });
  });
}

async function requestApproval() {
  if (!canWriteSolutions.value) {
    error.value = "缺少权限：solutions:write";
    return;
  }
  await runAction(async () => {
    await apiRequest(`/solutions/${solutionId.value}/approval/request`, {
      method: "POST",
      body: JSON.stringify({ note: approvalNote.value || null })
    });
    approvalNote.value = "";
  });
}

async function decideApproval(decision: "approved" | "rejected") {
  if (!canApproveSolutions.value) {
    error.value = "缺少权限：solutions:approve";
    return;
  }
  await runAction(async () => {
    const confirmation = await requestConfirmation(
      `${decision === "approved" ? "APPROVE" : "REJECT"} ${solutionId.value}`,
      decision === "approved" ? "审批通过确认" : "审批驳回确认"
    );
    await apiRequest(`/solutions/${solutionId.value}/approval/decision`, {
      method: "POST",
      body: JSON.stringify({ decision, note: decisionNote.value || null, confirmation })
    });
    decisionNote.value = "";
  });
}

async function exportProduction(type: "pdf" | "dxf") {
  if (!canExportSolutions.value) {
    error.value = "缺少权限：solutions:export";
    return;
  }
  await runAction(async () => {
    const confirmation = await requestConfirmation(`EXPORT ${type.toUpperCase()} ${solutionId.value}`, `生产导出 ${type.toUpperCase()}`);
    await apiRequest<SolutionExport>(`/solutions/${solutionId.value}/export/${type}`, {
      method: "POST",
      body: JSON.stringify({ confirmation })
    });
  });
}

async function queueExport(type: "pdf" | "dxf") {
  if (!canExportSolutions.value) {
    error.value = "缺少权限：solutions:export";
    return;
  }
  await runAction(async () => {
    const confirmation = await requestConfirmation(`EXPORT ${type.toUpperCase()} ${solutionId.value}`, `${type.toUpperCase()} 入队导出`);
    await apiRequest(`/solutions/${solutionId.value}/export/${type}/async`, {
      method: "POST",
      body: JSON.stringify({ confirmation })
    });
  });
}

async function archiveExpiredExports() {
  if (!canArchiveSolutions.value) {
    error.value = "缺少权限：solutions:archive";
    return;
  }
  await runAction(async () => {
    const result = await apiRequest<{ archived_count: number; checked_count: number; status: string }>(
      "/solutions/exports/archive-expired",
      {
        method: "POST",
        body: JSON.stringify({ solution_id: solutionId.value, dry_run: archiveDryRun.value })
      }
    );
    if (archiveDryRun.value) {
      return;
    }
    if (result.archived_count === 0 && result.checked_count === 0) {
      return;
    }
  });
}

async function runRecoveryDrill() {
  if (!canArchiveSolutions.value) {
    error.value = "缺少权限：solutions:archive";
    return;
  }
  await runAction(async () => {
    recoveryReport.value = await apiRequest<RecoveryReport>(`/solutions/${solutionId.value}/exports/recovery-drill`, {
      method: "POST",
      body: JSON.stringify({ include_archive_dry_run: true })
    });
  });
}

async function downloadExport(row: SolutionExport) {
  if (!canExportSolutions.value) {
    error.value = "缺少权限：solutions:export";
    return;
  }
  error.value = "";
  try {
    const response = await apiFetch(row.download_path);
    if (!response.ok) {
      throw await apiErrorFromResponse(response, row.download_path);
    }
    const blob = await response.blob();
    const objectUrl = window.URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = exportDownloadFilename(response, row);
    link.click();
    window.URL.revokeObjectURL(objectUrl);
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  }
}

function exportDownloadFilename(response: Response, row: SolutionExport) {
  const disposition = response.headers.get("content-disposition") || "";
  const match = disposition.match(/filename="?([^";]+)"?/i);
  return match?.[1] || `${row.id}.${row.export_type}`;
}

async function runAction(action: () => Promise<void>) {
  if (!solutionId.value) {
    error.value = "请输入 solution_id";
    return;
  }
  loading.value = true;
  error.value = "";
  try {
    await action();
    await load();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    loading.value = false;
  }
}

async function requestConfirmation(expected: string, title: string) {
  const result = await ElMessageBox.prompt(`请输入确认短语：${expected}`, title, {
    confirmButtonText: "确认",
    cancelButtonText: "取消",
    inputPattern: new RegExp(`^${escapeRegExp(expected)}$`),
    inputErrorMessage: "确认短语不匹配"
  });
  return result.value;
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
</script>

<template>
  <section>
    <div class="page-header">
      <div>
        <h1 class="page-title">方案管理</h1>
        <div class="page-subtitle">验证、审批、导出与生产放行</div>
      </div>
      <el-button type="primary" :loading="loading" @click="load">
        <PackageCheck :size="16" />
        加载
      </el-button>
    </div>

    <div class="work-band">
      <el-input v-model="solutionId" placeholder="solution_id" />
      <el-alert v-if="error" :title="error" type="error" :closable="false" style="margin-top: 12px" />
    </div>

    <div class="two-column">
      <div class="work-band">
        <div class="solution-toolbar">
          <el-button :disabled="!canWriteSolutions" :loading="loading" @click="validateSolution">
            <FileInput :size="16" />
            重新验证
          </el-button>
          <el-button type="warning" :disabled="!canWriteSolutions" :loading="loading" @click="requestApproval">
            <ScrollText :size="16" />
            提交审批
          </el-button>
          <el-button type="success" :disabled="!canApproveSolutions || !hasPendingApproval" :loading="loading" @click="decideApproval('approved')">
            <PackageCheck :size="16" />
            批准
          </el-button>
          <el-button type="danger" :disabled="!canApproveSolutions || !hasPendingApproval" :loading="loading" @click="decideApproval('rejected')">
            <XCircle :size="16" />
            驳回
          </el-button>
        </div>

        <el-descriptions v-if="solution" :column="2" border style="margin-top: 14px">
          <el-descriptions-item label="方案">{{ solution.solution_id }}</el-descriptions-item>
          <el-descriptions-item label="任务">{{ solution.job_id }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="statusType(solution.status)">{{ solution.status }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="Solver">{{ solution.solver }}</el-descriptions-item>
          <el-descriptions-item label="利用率">{{ (solution.utilization_rate * 100).toFixed(2) }}%</el-descriptions-item>
          <el-descriptions-item label="浪费率">{{ (solution.waste_rate * 100).toFixed(2) }}%</el-descriptions-item>
        </el-descriptions>

        <el-input
          v-model="approvalNote"
          type="textarea"
          :rows="3"
          placeholder="提交审批备注"
          style="margin-top: 14px"
        />
        <el-input
          v-model="decisionNote"
          type="textarea"
          :rows="3"
          placeholder="审批意见"
          style="margin-top: 10px"
        />

        <div class="solution-toolbar" style="margin-top: 14px">
          <el-button type="primary" :disabled="!canExportSolutions || !isApproved" :loading="loading" @click="exportProduction('pdf')">
            <Download :size="16" />
            生成 PDF
          </el-button>
          <el-button type="primary" :disabled="!canExportSolutions || !isApproved" :loading="loading" @click="exportProduction('dxf')">
            <Download :size="16" />
            生成 DXF
          </el-button>
          <el-button type="warning" :disabled="!canExportSolutions || !isApproved" :loading="loading" @click="queueExport('pdf')">
            <Send :size="16" />
            PDF 入队
          </el-button>
          <el-button type="warning" :disabled="!canExportSolutions || !isApproved" :loading="loading" @click="queueExport('dxf')">
            <Send :size="16" />
            DXF 入队
          </el-button>
        </div>
      </div>

      <SvgPreview :svg="svg" />
    </div>

    <div class="work-band">
      <div class="section-title">导出文件</div>
      <div class="solution-toolbar" style="margin-bottom: 12px">
        <el-switch v-model="archiveDryRun" :disabled="!canArchiveSolutions" active-text="Dry Run" inactive-text="正式归档" />
        <el-button :disabled="!canArchiveSolutions" :loading="loading" @click="archiveExpiredExports">
          <Archive :size="16" />
          归档过期导出
        </el-button>
        <el-button :disabled="!canArchiveSolutions" :loading="loading" @click="runRecoveryDrill">
          <ShieldCheck :size="16" />
          恢复演练
        </el-button>
      </div>
      <el-table v-if="canReadExports" :data="exports" border>
        <el-table-column prop="export_type" label="类型" width="90" />
        <el-table-column prop="version" label="版本" width="90" />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="lifecycle_status" label="生命周期" width="130">
          <template #default="{ row }">
            <el-tag :type="row.lifecycle_status === 'active' ? 'success' : 'info'">{{ row.lifecycle_status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="storage_backend" label="存储" width="90" />
        <el-table-column prop="storage_version_id" label="对象版本" min-width="190" show-overflow-tooltip />
        <el-table-column prop="storage_etag" label="ETag" min-width="190" show-overflow-tooltip />
        <el-table-column prop="storage_size_bytes" label="字节" width="110" />
        <el-table-column prop="checksum" label="SHA256" min-width="260" show-overflow-tooltip />
        <el-table-column prop="retention_until" label="保留至" min-width="170" />
        <el-table-column prop="created_at" label="生成时间" min-width="170" />
        <el-table-column label="操作" width="110" fixed="right">
          <template #default="{ row }">
            <el-button size="small" :disabled="!canExportSolutions" @click="downloadExport(row)">下载</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="recoveryReport" class="recovery-panel">
        <div class="section-title">恢复演练报告</div>
        <el-descriptions :column="4" border>
          <el-descriptions-item label="状态">
            <el-tag :type="statusType(recoveryReport.status)">{{ recoveryReport.status }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="检查对象">{{ recoveryReport.checked_count }}</el-descriptions-item>
          <el-descriptions-item label="正常">{{ recoveryReport.ok_count }}</el-descriptions-item>
          <el-descriptions-item label="缺失">{{ recoveryReport.missing_count }}</el-descriptions-item>
          <el-descriptions-item label="不可读">{{ recoveryReport.unreadable_count }}</el-descriptions-item>
          <el-descriptions-item label="校验不符">{{ recoveryReport.checksum_mismatch_count }}</el-descriptions-item>
          <el-descriptions-item label="版本不符">{{ recoveryReport.version_mismatch_count }}</el-descriptions-item>
          <el-descriptions-item label="过期 Dry Run">
            {{ recoveryReport.archive_dry_run?.checked_count ?? 0 }}
          </el-descriptions-item>
          <el-descriptions-item label="生成时间">{{ recoveryReport.generated_at }}</el-descriptions-item>
        </el-descriptions>
        <el-table :data="recoveryReport.items" border style="margin-top: 12px">
          <el-table-column prop="export_type" label="类型" width="80" />
          <el-table-column prop="version" label="版本" width="80" />
          <el-table-column prop="lifecycle_status" label="生命周期" width="120" />
          <el-table-column prop="storage_backend" label="存储" width="100" />
          <el-table-column prop="status" label="校验" width="140">
            <template #default="{ row }">
              <el-tag :type="statusType(row.status)">{{ row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="size_bytes" label="字节" width="110" />
          <el-table-column prop="object_key" label="对象键" min-width="220" show-overflow-tooltip />
          <el-table-column prop="expected_storage_version_id" label="期望版本" min-width="190" show-overflow-tooltip />
          <el-table-column prop="actual_storage_version_id" label="实际版本" min-width="190" show-overflow-tooltip />
          <el-table-column prop="actual_etag" label="实际 ETag" min-width="190" show-overflow-tooltip />
          <el-table-column prop="actual_checksum" label="实际 SHA256" min-width="260" show-overflow-tooltip />
          <el-table-column prop="error" label="错误" min-width="180" show-overflow-tooltip />
        </el-table>
      </div>
      <template v-if="canArchiveSolutions">
        <div class="section-title" style="margin-top: 16px">备份清单</div>
        <pre class="pre-wrap">{{ manifest }}</pre>
      </template>
    </div>

    <div class="two-column">
      <div class="work-band">
        <div class="section-title">审批记录</div>
        <el-table :data="approvals" border>
          <el-table-column prop="status" label="状态" width="110">
            <template #default="{ row }">
              <el-tag :type="statusType(row.status)">{{ row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="requested_by" label="提交人" min-width="150" />
          <el-table-column prop="decided_by" label="审批人" min-width="150" />
          <el-table-column prop="request_note" label="提交备注" min-width="180" />
          <el-table-column prop="decision_note" label="审批意见" min-width="180" />
          <el-table-column prop="updated_at" label="更新时间" min-width="170" />
        </el-table>
      </div>

      <div class="work-band">
        <div class="section-title">生产报告</div>
        <pre class="pre-wrap">{{ report }}</pre>
      </div>
    </div>
  </section>
</template>

<style scoped>
.solution-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.section-title {
  margin-bottom: 12px;
  font-size: 16px;
  font-weight: 700;
  color: #172033;
}

.recovery-panel {
  margin-top: 16px;
}
</style>
