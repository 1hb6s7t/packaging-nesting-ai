<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { Clock, Play, RefreshCw, Save } from "@lucide/vue";
import { apiRequest } from "../services/api";

type BenchmarkCase = {
  case_id: string;
  name: string;
  source?: string;
  sheet: Record<string, unknown>;
  items: Array<Record<string, unknown>>;
  baseline_utilization_rate?: number | null;
  created_at?: string;
  updated_at?: string;
};

type BenchmarkRun = {
  run_id: string;
  case_id: string;
  solver_name: string;
  utilization_rate: number;
  waste_rate: number;
  runtime_ms: number;
  valid: boolean;
  failure_reason?: string | null;
  created_at?: string;
};

type WorkTask = {
  id: string;
  task_type: string;
  target_id: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled" | "timed_out";
  result: Record<string, unknown>;
  error?: string | null;
  progress_percent: number;
  created_at: string;
};

const loading = ref(false);
const saving = ref(false);
const taskLoading = ref(false);
const error = ref("");
const cases = ref<BenchmarkCase[]>([]);
const runs = ref<BenchmarkRun[]>([]);
const benchmarkTasks = ref<WorkTask[]>([]);
const selectedCaseId = ref("");

const caseJson = ref(
  JSON.stringify(
    {
      case_id: "bench_demo",
      name: "Demo benchmark",
      sheet: {
        sheet_id: "bench_sheet_500_400",
        width: 500,
        height: 400,
        margin_top: 5,
        margin_right: 5,
        margin_bottom: 5,
        margin_left: 5,
        gripper_mm: 10,
        material: "white_card",
        thickness: "350gsm"
      },
      items: [
        {
          item_id: "bench_item_1",
          order_id: "bench_order_1",
          polygon: { shape_id: "bench_shape_1", outer: [[0, 0], [100, 0], [100, 80], [0, 80]] },
          priority_score: 0.9
        }
      ],
      baseline_utilization_rate: 0.1
    },
    null,
    2
  )
);

const validRunCount = computed(() => runs.value.filter((row) => row.valid).length);
const averageUtilization = computed(() => {
  if (!runs.value.length) return "0.0000";
  const total = runs.value.reduce((sum, row) => sum + Number(row.utilization_rate || 0), 0);
  return (total / runs.value.length).toFixed(4);
});

function statusType(row: BenchmarkRun) {
  return row.valid ? "success" : "danger";
}

function taskStatusType(status: WorkTask["status"]) {
  if (status === "completed") return "success";
  if (status === "failed" || status === "timed_out") return "danger";
  if (status === "cancelled") return "warning";
  if (status === "running") return "primary";
  return "info";
}

async function loadAll() {
  loading.value = true;
  error.value = "";
  try {
    const [caseRows, runRows] = await Promise.all([
      apiRequest<BenchmarkCase[]>("/benchmark/cases"),
      apiRequest<BenchmarkRun[]>("/benchmark/runs?limit=100")
    ]);
    cases.value = caseRows;
    runs.value = runRows;
    if (!selectedCaseId.value && caseRows.length) {
      selectedCaseId.value = caseRows[0].case_id;
    }
    await loadBenchmarkTasks();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    loading.value = false;
  }
}

async function loadBenchmarkTasks() {
  try {
    const rows = await apiRequest<WorkTask[]>("/tasks?limit=100");
    benchmarkTasks.value = rows.filter((row) => row.task_type === "benchmark.run");
  } catch {
    benchmarkTasks.value = [];
  }
}

async function saveCase() {
  saving.value = true;
  error.value = "";
  try {
    const payload = JSON.parse(caseJson.value) as BenchmarkCase;
    const saved = await apiRequest<BenchmarkCase>("/benchmark/cases", {
      method: "POST",
      body: JSON.stringify(payload)
    });
    selectedCaseId.value = saved.case_id;
    await loadAll();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    saving.value = false;
  }
}

async function runCase(caseId = selectedCaseId.value) {
  if (!caseId) return;
  saving.value = true;
  error.value = "";
  try {
    await apiRequest<BenchmarkRun>(`/benchmark/cases/${encodeURIComponent(caseId)}/runs`, { method: "POST" });
    await loadAll();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    saving.value = false;
  }
}

async function runCaseAsync(caseId = selectedCaseId.value) {
  if (!caseId) return;
  taskLoading.value = true;
  error.value = "";
  try {
    const task = await apiRequest<WorkTask>(`/benchmark/cases/${encodeURIComponent(caseId)}/runs/async`, { method: "POST" });
    benchmarkTasks.value = [task, ...benchmarkTasks.value.filter((row) => row.id !== task.id)];
    await loadAll();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    taskLoading.value = false;
  }
}

function editCase(row: BenchmarkCase) {
  selectedCaseId.value = row.case_id;
  caseJson.value = JSON.stringify(
    {
      case_id: row.case_id,
      name: row.name,
      sheet: row.sheet,
      items: row.items,
      baseline_utilization_rate: row.baseline_utilization_rate
    },
    null,
    2
  );
}

onMounted(loadAll);
</script>

<template>
  <section>
    <div class="page-header">
      <div>
        <h1 class="page-title">基准测试</h1>
        <div class="page-subtitle">Benchmark Case、Solver 运行记录和利用率基线</div>
      </div>
      <el-button type="primary" :loading="loading" @click="loadAll">
        <RefreshCw :size="16" />
        刷新
      </el-button>
    </div>

    <el-alert v-if="error" :title="error" type="error" :closable="false" style="margin-bottom: 16px" />

    <div class="metric-grid" style="margin-bottom: 16px">
      <div class="metric">
        <div class="metric-label">案例数</div>
        <div class="metric-value">{{ cases.length }}</div>
      </div>
      <div class="metric">
        <div class="metric-label">运行次数</div>
        <div class="metric-value">{{ runs.length }}</div>
      </div>
      <div class="metric">
        <div class="metric-label">通过运行</div>
        <div class="metric-value">{{ validRunCount }}</div>
      </div>
      <div class="metric">
        <div class="metric-label">平均利用率</div>
        <div class="metric-value">{{ averageUtilization }}</div>
      </div>
    </div>

    <div class="two-column">
      <div class="work-band">
        <div class="band-heading">保存案例</div>
        <el-input v-model="caseJson" type="textarea" :rows="24" spellcheck="false" />
        <div class="toolbar-row">
          <el-button type="primary" :loading="saving" @click="saveCase">
            <Save :size="16" />
            保存案例
          </el-button>
          <el-select v-model="selectedCaseId" filterable placeholder="选择案例" style="width: 220px">
            <el-option v-for="item in cases" :key="item.case_id" :label="item.name" :value="item.case_id" />
          </el-select>
          <el-button type="warning" :loading="saving" :disabled="!selectedCaseId" @click="runCase()">
            <Play :size="16" />
            运行
          </el-button>
          <el-button type="success" :loading="taskLoading" :disabled="!selectedCaseId" @click="runCaseAsync()">
            <Clock :size="16" />
            异步运行
          </el-button>
        </div>
      </div>

      <div class="work-band">
        <div class="band-heading">案例列表</div>
        <el-table v-loading="loading" :data="cases" border>
          <el-table-column prop="case_id" label="Case ID" min-width="150" />
          <el-table-column prop="name" label="名称" min-width="160" />
          <el-table-column prop="source" label="来源" width="100" />
          <el-table-column prop="baseline_utilization_rate" label="基线" width="90" />
          <el-table-column label="操作" width="220" fixed="right">
            <template #default="{ row }">
              <el-button size="small" @click="editCase(row)">编辑</el-button>
              <el-button size="small" type="primary" :loading="saving" @click="runCase(row.case_id)">运行</el-button>
              <el-button size="small" type="success" :loading="taskLoading" @click="runCaseAsync(row.case_id)">异步</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </div>

    <div class="work-band" v-if="benchmarkTasks.length">
      <div class="band-heading">Benchmark 任务</div>
      <el-table :data="benchmarkTasks" border>
        <el-table-column prop="created_at" label="时间" min-width="170" />
        <el-table-column prop="target_id" label="Case ID" min-width="150" />
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-tag :type="taskStatusType(row.status)">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="progress_percent" label="进度" width="90" />
        <el-table-column label="Run ID" min-width="170">
          <template #default="{ row }">{{ row.result?.run_id || "-" }}</template>
        </el-table-column>
        <el-table-column prop="error" label="错误" min-width="220" show-overflow-tooltip />
      </el-table>
    </div>

    <div class="work-band">
      <div class="band-heading">运行历史</div>
      <el-table v-loading="loading" :data="runs" border>
        <el-table-column prop="created_at" label="时间" min-width="170" />
        <el-table-column prop="case_id" label="Case ID" min-width="150" />
        <el-table-column prop="solver_name" label="Solver" min-width="140" />
        <el-table-column prop="utilization_rate" label="利用率" width="100" />
        <el-table-column prop="waste_rate" label="浪费率" width="100" />
        <el-table-column prop="runtime_ms" label="耗时 ms" width="100" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row)">{{ row.valid ? "通过" : "失败" }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="failure_reason" label="失败原因" min-width="160" />
      </el-table>
    </div>
  </section>
</template>

<style scoped>
.toolbar-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-top: 12px;
}
</style>
