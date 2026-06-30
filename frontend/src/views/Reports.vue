<script setup lang="ts">
import { computed, ref } from "vue";
import { Search } from "@lucide/vue";
import { apiRequest } from "../services/api";
import { useAppStore } from "../stores/app";

interface ValidationSummary {
  is_valid?: boolean | null;
  issue_count?: number;
  error_count?: number;
  warning_count?: number;
  issue_codes?: string[];
  overlap?: boolean;
  out_of_bounds?: boolean;
  gripper_conflict?: boolean;
  min_gap_violation?: boolean;
  rotation_invalid?: boolean;
}

interface ReportPlacement {
  item_id: string;
  order_id: string;
  x: number;
  y: number;
  rotation: number;
  mirrored: boolean;
  width?: number | null;
  height?: number | null;
}

interface ReportUnplacedItem {
  item_id: string;
  order_id?: string | null;
  reason: string;
}

interface SolutionReport {
  job_id: string;
  solution_id: string;
  solver: string;
  status: string;
  rank: number;
  utilization_rate: number;
  waste_rate: number;
  fixed_count?: number;
  candidate_count?: number;
  placed_count: number;
  unplaced_count: number;
  estimated_sheet_cost?: number;
  estimated_waste_cost?: number;
  validation_summary?: ValidationSummary;
  placed_items?: ReportPlacement[];
  unplaced_items?: ReportUnplacedItem[];
  validation?: Record<string, unknown> | null;
  score?: Record<string, unknown> | null;
  exports?: Record<string, string>;
}

const solutionId = ref(useAppStore().lastSolutionId);
const report = ref<SolutionReport | null>(null);
const loading = ref(false);
const error = ref("");

const validationSummary = computed(() => report.value?.validation_summary ?? {});
const placedItems = computed(() => report.value?.placed_items ?? []);
const unplacedItems = computed(() => report.value?.unplaced_items ?? []);
const issueCodes = computed(() => validationSummary.value.issue_codes ?? []);
const rawJson = computed(() => (report.value ? JSON.stringify(report.value, null, 2) : ""));

async function load() {
  if (!solutionId.value.trim()) {
    error.value = "请输入 solution_id";
    return;
  }
  loading.value = true;
  error.value = "";
  try {
    report.value = await apiRequest<SolutionReport>(`/solutions/${solutionId.value.trim()}/report`);
  } catch (err) {
    error.value = err instanceof Error ? err.message : "报告加载失败";
  } finally {
    loading.value = false;
  }
}

function formatPercent(value?: number) {
  if (typeof value !== "number") return "-";
  return `${(value * 100).toFixed(1)}%`;
}

function formatNumber(value?: number) {
  if (typeof value !== "number") return "-";
  return value.toFixed(2);
}

function statusType(status?: string | boolean | null) {
  if (status === true || status === "valid" || status === "approved" || status === "passed" || status === "candidate") return "success";
  if (status === false || status === "invalid" || status === "failed" || status === "rejected") return "danger";
  if (status === "partial" || status === "pending_approval") return "warning";
  return "info";
}
</script>

<template>
  <section>
    <div class="page-header">
      <div>
        <h1 class="page-title">利用率 / 成本报告</h1>
        <div class="page-subtitle">Solver 输出、Validator 报告、成本指标</div>
      </div>
    </div>
    <div class="work-band report-actions">
      <el-input v-model="solutionId" placeholder="solution_id" @keyup.enter="load" />
      <el-button type="primary" :icon="Search" :loading="loading" @click="load">生成</el-button>
    </div>

    <el-alert v-if="error" class="report-alert" type="error" :closable="false" :title="error" />

    <template v-if="report">
      <div class="metric-grid report-metrics">
        <div class="metric">
          <div class="metric-label">利用率</div>
          <div class="metric-value">{{ formatPercent(report.utilization_rate) }}</div>
        </div>
        <div class="metric">
          <div class="metric-label">废料率</div>
          <div class="metric-value">{{ formatPercent(report.waste_rate) }}</div>
        </div>
        <div class="metric">
          <div class="metric-label">放置 / 未放置</div>
          <div class="metric-value">{{ report.placed_count }} / {{ report.unplaced_count }}</div>
        </div>
        <div class="metric">
          <div class="metric-label">废料成本</div>
          <div class="metric-value">{{ formatNumber(report.estimated_waste_cost) }}</div>
        </div>
      </div>

      <div class="work-band">
        <div class="band-heading">方案摘要</div>
        <el-descriptions :column="3" border>
          <el-descriptions-item label="方案">{{ report.solution_id }}</el-descriptions-item>
          <el-descriptions-item label="任务">{{ report.job_id }}</el-descriptions-item>
          <el-descriptions-item label="Solver">{{ report.solver }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="statusType(report.status)">{{ report.status }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="排名">{{ report.rank }}</el-descriptions-item>
          <el-descriptions-item label="候选项">{{ report.candidate_count ?? "-" }}</el-descriptions-item>
          <el-descriptions-item label="固定项">{{ report.fixed_count ?? "-" }}</el-descriptions-item>
          <el-descriptions-item label="单张成本">{{ formatNumber(report.estimated_sheet_cost) }}</el-descriptions-item>
          <el-descriptions-item label="验证">
            <el-tag :type="statusType(validationSummary.is_valid)">
              {{ validationSummary.is_valid === true ? "valid" : validationSummary.is_valid === false ? "invalid" : "unknown" }}
            </el-tag>
          </el-descriptions-item>
        </el-descriptions>
      </div>

      <div class="work-band">
        <div class="band-heading">验证摘要</div>
        <div class="validation-summary">
          <el-tag :type="statusType(validationSummary.is_valid)">issues {{ validationSummary.issue_count ?? 0 }}</el-tag>
          <el-tag type="danger">errors {{ validationSummary.error_count ?? 0 }}</el-tag>
          <el-tag type="warning">warnings {{ validationSummary.warning_count ?? 0 }}</el-tag>
          <el-tag v-for="code in issueCodes" :key="code" type="info">{{ code }}</el-tag>
        </div>
      </div>

      <div class="report-table-grid">
        <div class="work-band">
          <div class="band-heading">放置明细</div>
          <el-table :data="placedItems" size="small" empty-text="无放置项">
            <el-table-column prop="item_id" label="Item" min-width="120" />
            <el-table-column prop="order_id" label="订单" min-width="120" />
            <el-table-column prop="x" label="X" width="80" />
            <el-table-column prop="y" label="Y" width="80" />
            <el-table-column prop="rotation" label="旋转" width="80" />
          </el-table>
        </div>

        <div class="work-band">
          <div class="band-heading">未放置明细</div>
          <el-table :data="unplacedItems" size="small" empty-text="无未放置项">
            <el-table-column prop="item_id" label="Item" min-width="120" />
            <el-table-column prop="order_id" label="订单" min-width="120" />
            <el-table-column prop="reason" label="原因" min-width="220" />
          </el-table>
        </div>
      </div>

      <div class="work-band">
        <el-collapse>
          <el-collapse-item title="JSON" name="raw-json">
            <pre class="pre-wrap">{{ rawJson }}</pre>
          </el-collapse-item>
        </el-collapse>
      </div>
    </template>

    <div v-else class="work-band report-empty">
      <span>输入 solution_id 后生成报告</span>
    </div>
  </section>
</template>

<style scoped>
.report-actions {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) auto;
  gap: 12px;
  align-items: center;
}

.report-alert {
  margin-bottom: 16px;
}

.report-metrics {
  margin-bottom: 16px;
}

.validation-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.report-table-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

.report-empty {
  color: #637083;
}

@media (max-width: 900px) {
  .report-actions,
  .report-table-grid {
    grid-template-columns: 1fr;
  }
}
</style>
