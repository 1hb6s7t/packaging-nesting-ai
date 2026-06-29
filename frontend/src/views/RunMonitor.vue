<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ElMessageBox } from "element-plus";
import { Ban, BellRing, RefreshCw, RotateCcw, Wrench } from "@lucide/vue";
import { apiRequest } from "../services/api";
import { useAppStore } from "../stores/app";

type SolverRun = {
  id: string;
  nesting_job_id: string;
  solver_name: string;
  solver_version: string;
  status: string;
  runtime_ms: number;
  created_at: string;
};

type SolverRunLog = {
  id: string;
  solver_run_id: string;
  level: string;
  message: string;
  payload: Record<string, unknown>;
  created_at: string;
};

type WorkTask = {
  id: string;
  task_type: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled" | "timed_out";
  target_type: string;
  target_id: string;
  parent_task_id?: string | null;
  actor_id?: string | null;
  payload: Record<string, unknown>;
  result: Record<string, unknown>;
  error?: string | null;
  attempt: number;
  max_attempts: number;
  timeout_sec?: number | null;
  cancel_requested: boolean;
  progress_percent: number;
  heartbeat_at?: string | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
};

type WorkTaskMetrics = {
  total: number;
  queued: number;
  running: number;
  completed: number;
  failed: number;
  cancelled: number;
  timed_out: number;
  active: number;
  stale_running: number;
  stale_after_sec: number;
  oldest_queued_at?: string | null;
};

type TaskAlert = {
  code: string;
  severity: "warning" | "critical";
  message: string;
  actual: number;
  threshold: number;
};

type TaskAlertCheckResult = {
  status: "ok" | "alerting";
  metrics: WorkTaskMetrics;
  alerts: TaskAlert[];
  notification_count: number;
  external_push?: Record<string, unknown> | null;
};

type MaintenanceSchedule = {
  enabled: boolean;
  interval_minutes: number;
  checks: {
    archive_expired_exports: boolean;
    conversion_sla_check: boolean;
    task_alert_check: boolean;
  };
};

type ScheduledMaintenanceRunResult = {
  status: "ok" | "attention";
  generated_at: string;
  enabled_checks: string[];
  export_archive?: { checked_count: number; archived_count: number; status: string } | null;
  conversion_sla?: { status: string; overdue_count: number; notification_count: number } | null;
  task_alerts?: TaskAlertCheckResult | null;
};

type ApiRouteMetrics = {
  method: string;
  route: string;
  status_class: string;
  count: number;
  error_count: number;
  total_duration_ms: number;
  avg_duration_ms: number;
  max_duration_ms: number;
};

type ApiMetrics = {
  total_requests: number;
  error_count: number;
  total_duration_ms: number;
  avg_duration_ms: number;
  routes: ApiRouteMetrics[];
};

const runs = ref<SolverRun[]>([]);
const logs = ref<SolverRunLog[]>([]);
const tasks = ref<WorkTask[]>([]);
const metrics = ref<WorkTaskMetrics | null>(null);
const apiMetrics = ref<ApiMetrics | null>(null);
const alertCheck = ref<TaskAlertCheckResult | null>(null);
const maintenanceSchedule = ref<MaintenanceSchedule | null>(null);
const maintenanceResult = ref<ScheduledMaintenanceRunResult | null>(null);
const selectedRunId = ref("");
const selectedTask = ref<WorkTask | null>(null);
const error = ref("");
const loading = ref(false);
const taskActionLoading = ref<Record<string, string>>({});
const appStore = useAppStore();
const canManageTasks = computed(() => appStore.hasPermission("tasks:manage"));
const apiErrorRate = computed(() => {
  if (!apiMetrics.value?.total_requests) return 0;
  return (apiMetrics.value.error_count / apiMetrics.value.total_requests) * 100;
});
const apiMaxDurationMs = computed(() =>
  Math.max(0, ...(apiMetrics.value?.routes.map((item) => item.max_duration_ms) || [0]))
);
const topApiRoutes = computed(() =>
  (apiMetrics.value?.routes || [])
    .slice()
    .sort(
      (left, right) =>
        right.error_count - left.error_count ||
        right.avg_duration_ms - left.avg_duration_ms ||
        right.count - left.count
    )
    .slice(0, 8)
);

function statusType(status: string) {
  if (status === "ok") return "success";
  if (status === "attention") return "warning";
  if (status === "completed") return "success";
  if (status === "failed" || status === "timed_out") return "danger";
  if (status === "cancelled") return "info";
  if (status === "running" || status === "queued") return "warning";
  return "info";
}

function statusClassType(statusClass: string) {
  if (statusClass === "2xx") return "success";
  if (statusClass === "4xx") return "warning";
  if (statusClass === "5xx") return "danger";
  return "info";
}

function canCancelTask(task: WorkTask) {
  return (task.status === "queued" || task.status === "running") && !task.cancel_requested;
}

function canRetryTask(task: WorkTask) {
  return ["failed", "cancelled", "timed_out"].includes(task.status) && task.attempt < task.max_attempts;
}

function timeoutLabel(task: WorkTask) {
  return task.timeout_sec == null ? "-" : `${task.timeout_sec}s`;
}

function formatJson(value: unknown) {
  const text = JSON.stringify(value ?? {}, null, 2);
  return text.length > 1600 ? `${text.slice(0, 1600)}...` : text;
}

function formatMs(value: number) {
  return `${value.toFixed(value >= 100 ? 0 : 1)} ms`;
}

function formatPercent(value: number) {
  return `${value.toFixed(2)}%`;
}

async function loadAll() {
  loading.value = true;
  error.value = "";
  try {
    const [runRows, taskRows] = await Promise.all([
      apiRequest<SolverRun[]>("/nesting/runs"),
      apiRequest<WorkTask[]>("/tasks?limit=100")
    ]);
    const [metricPayload, apiMetricPayload, schedulePayload] = await Promise.all([
      apiRequest<WorkTaskMetrics>("/tasks/metrics"),
      apiRequest<ApiMetrics>("/metrics"),
      apiRequest<MaintenanceSchedule>("/tasks/maintenance/schedule")
    ]);
    metrics.value = metricPayload;
    apiMetrics.value = apiMetricPayload;
    maintenanceSchedule.value = schedulePayload;
    runs.value = runRows;
    tasks.value = taskRows;
    if (selectedTask.value) {
      selectedTask.value = taskRows.find((row) => row.id === selectedTask.value?.id) || selectedTask.value;
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    loading.value = false;
  }
}

async function checkAlerts() {
  loading.value = true;
  error.value = "";
  try {
    alertCheck.value = await apiRequest<TaskAlertCheckResult>("/tasks/alerts/check", {
      method: "POST",
      body: JSON.stringify({})
    });
    metrics.value = alertCheck.value.metrics;
    apiMetrics.value = await apiRequest<ApiMetrics>("/metrics");
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    loading.value = false;
  }
}

async function runMaintenance() {
  if (!canManageTasks.value) {
    error.value = "缺少权限：tasks:manage";
    return;
  }
  loading.value = true;
  error.value = "";
  try {
    maintenanceResult.value = await apiRequest<ScheduledMaintenanceRunResult>("/tasks/maintenance/run", {
      method: "POST",
      body: JSON.stringify({
        archive_expired_exports: true,
        archive_dry_run: false,
        conversion_sla_check: true,
        conversion_sla_notify: true,
        task_alert_check: true,
        task_alert_notify: true,
        task_alert_push_external: false
      })
    });
    await loadAll();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    loading.value = false;
  }
}

async function loadLogs(row: SolverRun) {
  selectedTask.value = null;
  selectedRunId.value = row.id;
  logs.value = await apiRequest<SolverRunLog[]>(`/nesting/runs/${row.id}/logs`);
}

function inspectTask(row: WorkTask) {
  selectedTask.value = row;
}

async function cancelTask(row: WorkTask) {
  if (!canManageTasks.value) {
    error.value = "缺少权限：tasks:manage";
    return;
  }
  taskActionLoading.value = { ...taskActionLoading.value, [row.id]: "cancel" };
  error.value = "";
  try {
    const confirmation = await requestConfirmation(`CANCEL ${row.id}`, "取消任务确认");
    const updated = await apiRequest<WorkTask>(`/tasks/${row.id}/cancel`, {
      method: "POST",
      body: JSON.stringify({ confirmation })
    });
    selectedTask.value = updated;
    await loadAll();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    const next = { ...taskActionLoading.value };
    delete next[row.id];
    taskActionLoading.value = next;
  }
}

async function retryTask(row: WorkTask) {
  if (!canManageTasks.value) {
    error.value = "缺少权限：tasks:manage";
    return;
  }
  taskActionLoading.value = { ...taskActionLoading.value, [row.id]: "retry" };
  error.value = "";
  try {
    const confirmation = await requestConfirmation(`RETRY ${row.id}`, "重试任务确认");
    const retry = await apiRequest<WorkTask>(`/tasks/${row.id}/retry`, {
      method: "POST",
      body: JSON.stringify({ confirmation })
    });
    selectedTask.value = retry;
    await loadAll();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    const next = { ...taskActionLoading.value };
    delete next[row.id];
    taskActionLoading.value = next;
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

onMounted(loadAll);
</script>

<template>
  <section>
    <div class="page-header">
      <div>
        <h1 class="page-title">运行监控</h1>
        <div class="page-subtitle">任务队列、Solver 运行和结构化日志</div>
      </div>
      <el-button :loading="loading" @click="checkAlerts">
        <BellRing :size="16" />
        检查告警
      </el-button>
      <el-button :disabled="!canManageTasks" :loading="loading" @click="runMaintenance">
        <Wrench :size="16" />
        执行维护
      </el-button>
      <el-button type="primary" :loading="loading" @click="loadAll">
        <RefreshCw :size="16" />
        刷新
      </el-button>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" style="margin-bottom: 12px" />

    <el-alert
      v-if="alertCheck?.alerts.length"
      :title="`触发 ${alertCheck.alerts.length} 条任务队列告警，生成 ${alertCheck.notification_count} 条站内通知`"
      type="warning"
      :closable="false"
      style="margin-bottom: 12px"
    >
      <div class="alert-list">
        <el-tag
          v-for="item in alertCheck.alerts"
          :key="item.code"
          :type="item.severity === 'critical' ? 'danger' : 'warning'"
        >
          {{ item.code }} {{ item.actual }}/{{ item.threshold }}
        </el-tag>
      </div>
    </el-alert>

    <div v-if="maintenanceSchedule" class="work-band">
      <div class="section-title">维护调度</div>
      <el-descriptions :column="4" border size="small">
        <el-descriptions-item label="定时">
          <el-tag :type="maintenanceSchedule.enabled ? 'success' : 'info'">
            {{ maintenanceSchedule.enabled ? "enabled" : "disabled" }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="间隔">{{ maintenanceSchedule.interval_minutes }} 分钟</el-descriptions-item>
        <el-descriptions-item label="归档巡检">
          {{ maintenanceSchedule.checks.archive_expired_exports ? "开启" : "关闭" }}
        </el-descriptions-item>
        <el-descriptions-item label="转换 SLA">
          {{ maintenanceSchedule.checks.conversion_sla_check ? "开启" : "关闭" }}
        </el-descriptions-item>
        <el-descriptions-item label="任务告警">
          {{ maintenanceSchedule.checks.task_alert_check ? "开启" : "关闭" }}
        </el-descriptions-item>
        <el-descriptions-item v-if="maintenanceResult" label="最近执行">
          <el-tag :type="statusType(maintenanceResult.status)">{{ maintenanceResult.status }}</el-tag>
          {{ maintenanceResult.generated_at }}
        </el-descriptions-item>
        <el-descriptions-item v-if="maintenanceResult?.export_archive" label="归档">
          {{ maintenanceResult.export_archive.archived_count }}/{{ maintenanceResult.export_archive.checked_count }}
        </el-descriptions-item>
        <el-descriptions-item v-if="maintenanceResult?.conversion_sla" label="SLA 逾期">
          {{ maintenanceResult.conversion_sla.overdue_count }}
        </el-descriptions-item>
      </el-descriptions>
    </div>

    <div v-if="metrics" class="metrics-grid">
      <div class="metric-tile">
        <div class="metric-value">{{ metrics.active }}</div>
        <div class="metric-label">活跃任务</div>
      </div>
      <div class="metric-tile">
        <div class="metric-value">{{ metrics.queued }}</div>
        <div class="metric-label">排队</div>
      </div>
      <div class="metric-tile">
        <div class="metric-value">{{ metrics.running }}</div>
        <div class="metric-label">运行中</div>
      </div>
      <div class="metric-tile" :class="{ danger: metrics.stale_running > 0 }">
        <div class="metric-value">{{ metrics.stale_running }}</div>
        <div class="metric-label">心跳超时</div>
      </div>
      <div class="metric-tile">
        <div class="metric-value">{{ metrics.failed + metrics.timed_out }}</div>
        <div class="metric-label">失败/超时</div>
      </div>
    </div>

    <div v-if="apiMetrics" class="work-band">
      <div class="section-title">API 指标</div>
      <div class="metrics-grid api-metrics-grid">
        <div class="metric-tile">
          <div class="metric-value">{{ apiMetrics.total_requests }}</div>
          <div class="metric-label">请求数</div>
        </div>
        <div class="metric-tile" :class="{ danger: apiMetrics.error_count > 0 }">
          <div class="metric-value">{{ apiMetrics.error_count }}</div>
          <div class="metric-label">5xx</div>
        </div>
        <div class="metric-tile" :class="{ danger: apiErrorRate > 0 }">
          <div class="metric-value">{{ formatPercent(apiErrorRate) }}</div>
          <div class="metric-label">错误率</div>
        </div>
        <div class="metric-tile">
          <div class="metric-value">{{ formatMs(apiMetrics.avg_duration_ms) }}</div>
          <div class="metric-label">平均耗时</div>
        </div>
        <div class="metric-tile">
          <div class="metric-value">{{ formatMs(apiMaxDurationMs) }}</div>
          <div class="metric-label">最大耗时</div>
        </div>
      </div>
      <el-table :data="topApiRoutes" border size="small">
        <el-table-column label="Route" min-width="240">
          <template #default="{ row }">
            <span class="api-route">{{ row.route }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="method" label="方法" width="90" />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="statusClassType(row.status_class)">{{ row.status_class }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="count" label="请求" width="90" />
        <el-table-column prop="error_count" label="5xx" width="80" />
        <el-table-column label="平均耗时" width="120">
          <template #default="{ row }">{{ formatMs(row.avg_duration_ms) }}</template>
        </el-table-column>
        <el-table-column label="最大耗时" width="120">
          <template #default="{ row }">{{ formatMs(row.max_duration_ms) }}</template>
        </el-table-column>
      </el-table>
    </div>

    <div class="work-band">
      <div class="section-title">后台任务</div>
      <el-table :data="tasks" border highlight-current-row @row-click="inspectTask">
        <el-table-column prop="id" label="Task ID" min-width="170" />
        <el-table-column prop="task_type" label="类型" min-width="150" />
        <el-table-column prop="target_id" label="目标" min-width="160" />
        <el-table-column prop="status" label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="尝试" width="90">
          <template #default="{ row }">{{ row.attempt }}/{{ row.max_attempts }}</template>
        </el-table-column>
        <el-table-column label="进度" width="130">
          <template #default="{ row }">
            <el-progress :percentage="row.progress_percent || 0" :stroke-width="8" />
          </template>
        </el-table-column>
        <el-table-column label="超时" width="90">
          <template #default="{ row }">{{ timeoutLabel(row) }}</template>
        </el-table-column>
        <el-table-column prop="heartbeat_at" label="心跳" min-width="170" />
        <el-table-column prop="created_at" label="创建时间" min-width="170" />
        <el-table-column prop="completed_at" label="完成时间" min-width="170" />
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <div class="task-actions">
              <el-tooltip content="取消任务" placement="top">
                <el-button
                  circle
                  size="small"
                  :disabled="!canManageTasks || !canCancelTask(row)"
                  :loading="taskActionLoading[row.id] === 'cancel'"
                  aria-label="取消任务"
                  @click.stop="cancelTask(row)"
                >
                  <Ban :size="15" />
                </el-button>
              </el-tooltip>
              <el-tooltip content="重试任务" placement="top">
                <el-button
                  circle
                  size="small"
                  :disabled="!canManageTasks || !canRetryTask(row)"
                  :loading="taskActionLoading[row.id] === 'retry'"
                  aria-label="重试任务"
                  @click.stop="retryTask(row)"
                >
                  <RotateCcw :size="15" />
                </el-button>
              </el-tooltip>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="two-column">
      <div class="work-band">
        <div class="section-title">Solver Run</div>
        <el-table :data="runs" border highlight-current-row @row-click="loadLogs">
          <el-table-column prop="id" label="Run ID" min-width="170" />
          <el-table-column prop="nesting_job_id" label="Job" min-width="150" />
          <el-table-column prop="solver_name" label="Solver" min-width="140" />
          <el-table-column prop="status" label="状态" width="100" />
          <el-table-column prop="runtime_ms" label="耗时 ms" width="110" />
        </el-table>
      </div>
      <div class="work-band">
        <div class="section-title">详情</div>
        <div v-if="selectedTask">
          <div style="font-weight: 700; margin-bottom: 8px">任务 {{ selectedTask.id }}</div>
          <el-descriptions :column="2" border size="small" style="margin-bottom: 10px">
            <el-descriptions-item label="状态">
              <el-tag :type="statusType(selectedTask.status)">{{ selectedTask.status }}</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="尝试">{{ selectedTask.attempt }}/{{ selectedTask.max_attempts }}</el-descriptions-item>
            <el-descriptions-item label="超时">{{ timeoutLabel(selectedTask) }}</el-descriptions-item>
            <el-descriptions-item label="进度">{{ selectedTask.progress_percent }}%</el-descriptions-item>
            <el-descriptions-item label="心跳">{{ selectedTask.heartbeat_at || "-" }}</el-descriptions-item>
            <el-descriptions-item label="取消请求">
              {{ selectedTask.cancel_requested ? "是" : "否" }}
            </el-descriptions-item>
            <el-descriptions-item label="父任务">{{ selectedTask.parent_task_id || "-" }}</el-descriptions-item>
            <el-descriptions-item label="错误">{{ selectedTask.error || "-" }}</el-descriptions-item>
          </el-descriptions>
          <div class="detail-json-grid">
            <div>
              <div class="json-title">Payload</div>
              <pre class="json-preview">{{ formatJson(selectedTask.payload) }}</pre>
            </div>
            <div>
              <div class="json-title">Result</div>
              <pre class="json-preview">{{ formatJson(selectedTask.result) }}</pre>
            </div>
          </div>
        </div>
        <div v-else>
          <div style="font-weight: 700; margin-bottom: 8px">运行日志 {{ selectedRunId }}</div>
          <el-table :data="logs" border>
            <el-table-column prop="level" label="级别" width="90" />
            <el-table-column prop="message" label="消息" min-width="180" />
            <el-table-column prop="created_at" label="时间" min-width="180" />
            <el-table-column label="Payload" min-width="280">
              <template #default="{ row }">
                <pre class="json-preview log-payload">{{ formatJson(row.payload) }}</pre>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.section-title {
  margin-bottom: 12px;
  font-size: 16px;
  font-weight: 700;
  color: #172033;
}

.task-actions {
  display: flex;
  gap: 6px;
  align-items: center;
}

.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  justify-content: flex-end;
}

.alert-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}

.detail-json-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.json-title {
  margin-bottom: 6px;
  color: #5f6b7a;
  font-size: 12px;
  font-weight: 700;
}

.json-preview {
  max-height: 180px;
  margin: 0;
  overflow: auto;
  color: #334155;
  font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
  font-size: 12px;
  line-height: 1.45;
  white-space: pre-wrap;
  word-break: break-word;
}

.log-payload {
  max-height: 96px;
}

.metrics-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(120px, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}

.api-metrics-grid {
  margin-bottom: 12px;
}

.metric-tile {
  padding: 14px;
  border: 1px solid #d8dee8;
  border-radius: 8px;
  background: #fff;
}

.metric-tile.danger {
  border-color: #f2b8b5;
  background: #fff7f7;
}

.metric-value {
  font-size: 24px;
  font-weight: 700;
  color: #172033;
}

.metric-label {
  margin-top: 4px;
  font-size: 13px;
  color: #5f6b7a;
}

.api-route {
  font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
  overflow-wrap: anywhere;
}

@media (max-width: 900px) {
  .metrics-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .detail-json-grid {
    grid-template-columns: 1fr;
  }
}
</style>
