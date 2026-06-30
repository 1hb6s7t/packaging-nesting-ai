<script setup lang="ts">
import { computed, ref } from "vue";
import { BarChart3, Eye, FileSearch, GitCompare, Play, RefreshCw, ShieldCheck, Upload } from "@lucide/vue";
import {
  type BatchArtworkSummary,
  type BatchBenchmarkRun,
  type BatchLayoutRunResult,
  type ProductionPlan,
  type ProductionPattern,
  approveBatchPlan,
  createBatchLayoutJob,
  exportBatchPlan,
  listBatchLayoutPlans,
  parseBatchArtworks,
  preflightBatchArtworks,
  previewBatchPlan,
  retryFailedBatchArtworks,
  requestBatchPlanApproval,
  runBatchLayoutJob,
  runEnterpriseBatch20000,
  runEnterpriseBatch1500,
  runEnterpriseStress787,
  uploadBatchArtworks
} from "../services/api";

const fileInput = ref<HTMLInputElement | null>(null);
const selectedFiles = ref<File[]>([]);
const sourceName = ref(`batch-${new Date().toISOString().slice(0, 10)}`);
const summary = ref<BatchArtworkSummary | null>(null);
const runResult = ref<BatchLayoutRunResult | null>(null);
const selectedPlan = ref<ProductionPlan | null>(null);
const previewSvg = ref("");
const stressResult = ref<BatchBenchmarkRun | null>(null);
const error = ref("");
const loading = ref("");

const currentBatchId = computed(() => summary.value?.batch.batch_id || "");
const plans = computed(() => runResult.value?.plans || []);
const groups = computed(() => runResult.value?.groups || []);
const oversizeItems = computed(() => (summary.value?.items || []).filter((item) => item.classification === "OVERSIZE"));
const manualReviewItems = computed(() =>
  (summary.value?.items || []).filter((item) => item.status === "manual_review" || item.preflight_report?.requires_manual_review)
);
const failedItems = computed(() => (summary.value?.items || []).filter((item) => item.status === "failed"));
const parsedCount = computed(() => summary.value?.batch.parsed_count || 0);
const planLegalCount = computed(() => plans.value.filter((plan) => plan.hard_rule_pass).length);

function openFilePicker() {
  fileInput.value?.click();
}

function handleFileSelect(event: Event) {
  const target = event.target as HTMLInputElement;
  selectedFiles.value = Array.from(target.files || []);
}

async function runStep<T>(label: string, action: () => Promise<T>): Promise<T | null> {
  loading.value = label;
  error.value = "";
  try {
    return await action();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
    return null;
  } finally {
    loading.value = "";
  }
}

async function uploadBatch() {
  if (!selectedFiles.value.length) {
    error.value = "请选择至少一个版图文件";
    return;
  }
  const result = await runStep("upload", () => uploadBatchArtworks(selectedFiles.value, sourceName.value));
  if (result) {
    summary.value = result;
    runResult.value = null;
    selectedPlan.value = null;
    previewSvg.value = "";
  }
}

async function preflightBatch() {
  if (!currentBatchId.value) return;
  const result = await runStep("preflight", () => preflightBatchArtworks(currentBatchId.value));
  if (result) summary.value = result;
}

async function parseBatch() {
  if (!currentBatchId.value) return;
  const result = await runStep("parse", () => parseBatchArtworks(currentBatchId.value));
  if (result) summary.value = result;
}

async function retryFailedBatch() {
  if (!currentBatchId.value) return;
  const result = await runStep("retryFailed", () =>
    retryFailedBatchArtworks(
      currentBatchId.value,
      failedItems.value.map((item) => item.item_id)
    )
  );
  if (result) summary.value = result;
}

async function runLayout() {
  if (!currentBatchId.value) return;
  const result = await runStep("layout", async () => {
    const job = await createBatchLayoutJob(currentBatchId.value);
    return runBatchLayoutJob(job.job_id);
  });
  if (result) {
    runResult.value = result;
    selectedPlan.value = result.plans[0] || null;
    previewSvg.value = "";
  }
}

async function loadPreview(plan: ProductionPlan) {
  selectedPlan.value = plan;
  const result = await runStep("preview", () => previewBatchPlan(plan.plan_id));
  if (result) previewSvg.value = result;
}

function loadPatternPlacement(pattern: ProductionPattern) {
  previewSvg.value = pattern.placement_svg || "";
}

function placementCoverage(pattern: ProductionPattern) {
  const complete = pattern.placement_json.complete_item_coverage === true;
  const omitted = Number(pattern.placement_json.omitted_item_count || 0);
  return complete ? "complete" : `truncated +${omitted}`;
}

function shortChecksum(value: string | null | undefined) {
  return value ? value.slice(0, 12) : "missing";
}

async function tryExport(plan: ProductionPlan) {
  selectedPlan.value = plan;
  const result = await runStep("export", () => exportBatchPlan(plan.plan_id));
  if (result) {
    error.value = "";
  }
}

async function requestApproval(plan: ProductionPlan) {
  selectedPlan.value = plan;
  const result = await runStep("approvalRequest", () => requestBatchPlanApproval(plan.plan_id));
  if (result) {
    await refreshPlans(plan.job_id, plan.plan_id);
  }
}

async function approvePlan(plan: ProductionPlan) {
  selectedPlan.value = plan;
  const result = await runStep("approvalDecision", () => approveBatchPlan(plan.plan_id));
  if (result) {
    await refreshPlans(plan.job_id, plan.plan_id);
  }
}

async function refreshPlans(jobId: string, planId?: string) {
  const rows = await listBatchLayoutPlans(jobId);
  if (runResult.value) {
    runResult.value = { ...runResult.value, plans: rows };
  }
  selectedPlan.value = rows.find((row) => row.plan_id === planId) || rows[0] || null;
}

async function runStress(type: "787" | "1500" | "20000") {
  const result = await runStep(type === "787" ? "stress787" : type === "1500" ? "stress1500" : "stress20000", () =>
    type === "787" ? runEnterpriseStress787() : type === "1500" ? runEnterpriseBatch1500(1500) : runEnterpriseBatch20000(20000)
  );
  if (result) stressResult.value = result;
}

function statusTag(status: string) {
  if (["parsed", "completed", "validator_passed", "passed"].includes(status)) return "success";
  if (["failed", "validator_failed"].includes(status)) return "danger";
  if (["manual_review", "conversion_required"].includes(status)) return "warning";
  if (["running", "preflighted"].includes(status)) return "primary";
  return "info";
}

function percent(value: number | undefined | null) {
  return `${((value || 0) * 100).toFixed(1)}%`;
}
</script>

<template>
  <section>
    <div class="page-header">
      <div>
        <h1 class="page-title">批量版图工作台</h1>
        <div class="page-subtitle">1500 文件批量预检、特征分类、自动分组、787x1092 裁切与 Top3 生产方案</div>
      </div>
      <el-button :loading="Boolean(loading)" @click="runStress('1500')">
        <BarChart3 :size="16" />
        1500 压测
      </el-button>
    </div>

    <el-alert v-if="error" :title="error" type="error" :closable="false" style="margin-bottom: 16px" />

    <div class="metric-grid" style="margin-bottom: 16px">
      <div class="metric">
        <div class="metric-label">批次文件</div>
        <div class="metric-value">{{ summary?.batch.item_count || selectedFiles.length }}</div>
      </div>
      <div class="metric">
        <div class="metric-label">已解析</div>
        <div class="metric-value">{{ parsedCount }}</div>
      </div>
      <div class="metric">
        <div class="metric-label">人工复核</div>
        <div class="metric-value">{{ manualReviewItems.length }}</div>
      </div>
      <div class="metric">
        <div class="metric-label">合法 Top3</div>
        <div class="metric-value">{{ planLegalCount }}/{{ plans.length || 3 }}</div>
      </div>
    </div>

    <div class="work-band">
      <div class="band-heading">
        <Upload :size="18" />
        批量上传与解析
      </div>
      <div class="batch-toolbar">
        <el-input v-model="sourceName" placeholder="批次名称" style="max-width: 260px" />
        <input ref="fileInput" type="file" multiple class="hidden-file-input" @change="handleFileSelect" />
        <el-button @click="openFilePicker">
          <Upload :size="16" />
          选择文件
        </el-button>
        <el-button type="primary" :loading="loading === 'upload'" :disabled="!selectedFiles.length" @click="uploadBatch">
          <Upload :size="16" />
          上传批次
        </el-button>
        <el-button :loading="loading === 'preflight'" :disabled="!currentBatchId" @click="preflightBatch">
          <FileSearch :size="16" />
          预检
        </el-button>
        <el-button :loading="loading === 'parse'" :disabled="!currentBatchId" @click="parseBatch">
          <RefreshCw :size="16" />
          解析
        </el-button>
        <el-button :loading="loading === 'retryFailed'" :disabled="!currentBatchId || !failedItems.length" @click="retryFailedBatch">
          <RefreshCw :size="16" />
          重试失败
        </el-button>
        <el-button type="success" :loading="loading === 'layout'" :disabled="!currentBatchId" @click="runLayout">
          <Play :size="16" />
          运行 Top3
        </el-button>
      </div>
      <div class="file-summary" v-if="selectedFiles.length">
        <el-tag v-for="file in selectedFiles.slice(0, 8)" :key="file.name" type="info">{{ file.name }}</el-tag>
        <span v-if="selectedFiles.length > 8">+{{ selectedFiles.length - 8 }}</span>
      </div>
    </div>

    <div class="work-band" v-if="summary">
      <div class="band-heading">批量特征与分类</div>
      <el-table :data="summary.items" border height="420">
        <el-table-column prop="filename" label="文件" min-width="220" show-overflow-tooltip />
        <el-table-column prop="source_format" label="格式" width="90" />
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-tag :type="statusTag(row.status)">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="分类" width="130">
          <template #default="{ row }">
            <el-tag :type="row.classification === 'OVERSIZE' ? 'danger' : 'success'">
              {{ row.classification || "UNCLASSIFIED" }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="BBox" min-width="130">
          <template #default="{ row }">
            <span v-if="row.feature?.bbox">{{ row.feature.bbox.width.toFixed(1) }} x {{ row.feature.bbox.height.toFixed(1) }}</span>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="面积" width="100">
          <template #default="{ row }">{{ row.feature?.area?.toFixed?.(1) || "-" }}</template>
        </el-table-column>
        <el-table-column label="置信度" width="100">
          <template #default="{ row }">{{ percent(row.feature?.parse_confidence) }}</template>
        </el-table-column>
        <el-table-column prop="retry_count" label="重试" width="80" />
        <el-table-column prop="parse_error" label="异常" min-width="260" show-overflow-tooltip />
      </el-table>
    </div>

    <div class="two-column" v-if="runResult">
      <div class="work-band">
        <div class="band-heading">自动分组</div>
        <el-table :data="groups" border>
          <el-table-column prop="compatibility_key" label="兼容键" min-width="220" show-overflow-tooltip />
          <el-table-column label="数量" width="90">
            <template #default="{ row }">{{ row.item_ids.length }}</template>
          </el-table-column>
          <el-table-column prop="material" label="材料" width="120" />
          <el-table-column prop="thickness" label="厚度" width="120" />
        </el-table>
      </div>

      <div class="work-band">
        <div class="band-heading">
          <GitCompare :size="18" />
          Top3 生产方案
        </div>
        <el-table :data="plans" border>
          <el-table-column prop="rank" label="#" width="54" />
          <el-table-column prop="intent" label="策略" min-width="150" />
          <el-table-column label="状态" width="130">
            <template #default="{ row }">
              <el-tag :type="statusTag(row.status)">{{ row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="利用率" width="100">
            <template #default="{ row }">{{ percent(row.utilization_rate) }}</template>
          </el-table-column>
          <el-table-column prop="total_sheets_used" label="张数" width="90" />
          <el-table-column label="履约" width="100">
            <template #default="{ row }">{{ percent(row.quantity_fulfillment_rate) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="330" fixed="right">
            <template #default="{ row }">
              <el-button size="small" @click="loadPreview(row)">
                <Eye :size="14" />
                预览
              </el-button>
              <el-button size="small" :loading="loading === 'approvalRequest'" @click="requestApproval(row)">
                审批
              </el-button>
              <el-button size="small" type="success" :loading="loading === 'approvalDecision'" @click="approvePlan(row)">
                批准
              </el-button>
              <el-button size="small" type="warning" :loading="loading === 'export'" @click="tryExport(row)">
                <ShieldCheck :size="14" />
                导出
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </div>

    <div class="two-column" v-if="selectedPlan">
      <div class="work-band">
        <div class="band-heading">Pattern 详情与数量履约</div>
        <el-table :data="selectedPlan.patterns" border>
          <el-table-column prop="pattern_type" label="Pattern" min-width="160" />
          <el-table-column prop="cut_variant_id" label="裁切变体" min-width="180" show-overflow-tooltip />
          <el-table-column prop="units_per_sheet" label="每张" width="80" />
          <el-table-column prop="required_sheets" label="张数" width="80" />
          <el-table-column label="履约" width="100">
            <template #default="{ row }">{{ percent(row.quantity_fulfillment_rate) }}</template>
          </el-table-column>
          <el-table-column label="Artifact" width="120">
            <template #default="{ row }">
              <el-tag :type="row.placement_checksum ? 'success' : 'danger'">{{ placementCoverage(row) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="Checksum" width="130">
            <template #default="{ row }">{{ shortChecksum(row.placement_checksum) }}</template>
          </el-table-column>
          <el-table-column label="SVG" width="86">
            <template #default="{ row }">
              <el-button size="small" :disabled="!row.placement_svg" @click="loadPatternPlacement(row)">View</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
      <div class="work-band">
        <div class="band-heading">方案预览</div>
        <div class="svg-frame plan-preview" v-html="previewSvg || '<svg xmlns=&quot;http://www.w3.org/2000/svg&quot; viewBox=&quot;0 0 400 120&quot;><text x=&quot;20&quot; y=&quot;60&quot;>请选择预览</text></svg>'"></div>
      </div>
    </div>

    <div class="work-band" v-if="oversizeItems.length || stressResult">
      <div class="band-heading">异常与压力结果</div>
      <div class="batch-toolbar" style="margin-bottom: 12px">
        <el-button :loading="loading === 'stress787'" @click="runStress('787')">
          <BarChart3 :size="16" />
          787 MOQ 压测
        </el-button>
        <el-button :loading="loading === 'stress1500'" @click="runStress('1500')">
          <BarChart3 :size="16" />
          1500 文件压测
        </el-button>
        <el-button :loading="loading === 'stress20000'" @click="runStress('20000')">
          <BarChart3 :size="16" />
          20000 文件压测
        </el-button>
      </div>
      <el-alert
        v-if="oversizeItems.length"
        :title="`超尺寸异常件 ${oversizeItems.length} 个，需要转换或人工确认`"
        type="warning"
        :closable="false"
        style="margin-bottom: 12px"
      />
      <el-descriptions v-if="stressResult" border :column="4">
        <el-descriptions-item label="类型">{{ stressResult.benchmark_type }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ stressResult.status }}</el-descriptions-item>
        <el-descriptions-item label="文件数">{{ stressResult.file_count }}</el-descriptions-item>
        <el-descriptions-item label="P95">{{ stressResult.p95_runtime_ms || 0 }} ms</el-descriptions-item>
        <el-descriptions-item label="硬约束">{{ percent(stressResult.hard_rule_pass_rate) }}</el-descriptions-item>
        <el-descriptions-item label="履约">{{ percent(stressResult.quantity_fulfillment_rate) }}</el-descriptions-item>
        <el-descriptions-item label="TopK合法">{{ percent(stressResult.topk_legal_rate) }}</el-descriptions-item>
        <el-descriptions-item label="均分">{{ stressResult.avg_case_score.toFixed(1) }}</el-descriptions-item>
      </el-descriptions>
    </div>
  </section>
</template>

<style scoped>
.batch-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.hidden-file-input {
  display: none;
}

.file-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 12px;
  color: #637083;
  font-size: 13px;
}

.plan-preview {
  min-height: 260px;
}
</style>
