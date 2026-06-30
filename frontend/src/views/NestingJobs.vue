<script setup lang="ts">
import { ref } from "vue";
import { BellRing, ClipboardCheck, CloudUpload, Play, Send, ShieldCheck, ShoppingCart } from "@lucide/vue";
import SvgPreview from "../components/SvgPreview.vue";
import { apiRequest } from "../services/api";
import { useAppStore } from "../stores/app";

type WorkTask = {
  id: string;
  task_type: string;
  status: string;
  target_id: string;
  result: Record<string, unknown>;
};

type MaterialAvailabilityItem = {
  material: string;
  required_qty: number;
  available_qty: number;
  reserved_qty: number;
  net_available_qty: number;
  shortage_qty: number;
  unit?: string | null;
  status: string;
  order_ids: string[];
  source_count: number;
};

type MaterialAvailabilityCheck = {
  job_id: string;
  overall_status: string;
  order_count: number;
  inventory_source_count: number;
  missing_order_ids: string[];
  warnings: string[];
  items: MaterialAvailabilityItem[];
};

type ProductionScheduleReadinessItem = {
  order_id: string;
  required_qty: number;
  scheduled_qty: number;
  status: string;
  latest_status?: string | null;
  planned_start_at?: string | null;
  line_code?: string | null;
  machine_code?: string | null;
};

type DeliveryClosureItem = {
  order_id: string;
  required_qty: number;
  delivered_qty: number;
  status: string;
  latest_status?: string | null;
  delivered_at?: string | null;
  shipment_no?: string | null;
};

type ProductionReadinessCheck = {
  job_id: string;
  overall_status: string;
  material_status: string;
  schedule_status: string;
  delivery_status: string;
  order_count: number;
  schedule_source_count: number;
  delivery_source_count: number;
  warnings: string[];
  schedule_items: ProductionScheduleReadinessItem[];
  delivery_items: DeliveryClosureItem[];
};

type ProductionAlert = {
  code: string;
  severity: string;
  message: string;
  status: string;
  affected_order_ids: string[];
};

type ProductionAlertCheck = {
  status: string;
  readiness: ProductionReadinessCheck;
  alerts: ProductionAlert[];
  notification_count: number;
};

type ProcurementRecommendation = {
  material: string;
  shortage_qty: number;
  recommended_purchase_qty: number;
  unit?: string | null;
  severity: string;
  order_ids: string[];
};

type ProcurementAlertCheck = {
  status: string;
  material_readiness: MaterialAvailabilityCheck;
  recommendations: ProcurementRecommendation[];
  notification_count: number;
};

type ExceptionWritebackAction = {
  system_type: string;
  target_type: string;
  requested_status: string;
  reason: string;
  writeback_log: {
    id: string;
    status: string;
    target_id?: string | null;
    payload: Record<string, unknown>;
  };
};

type ExceptionWritebackResult = {
  job_id: string;
  dry_run: boolean;
  status: string;
  action_count: number;
  writeback_count: number;
  skipped_count: number;
  failed_count: number;
  actions: ExceptionWritebackAction[];
};

const appStore = useAppStore();

function templateId(prefix: string) {
  return `${prefix}_${new Date().toISOString().replace(/\D/g, "").slice(0, 14)}`;
}

const templateJobId = templateId("job_template");

const jobJson = ref(
  JSON.stringify(
    {
      job_id: templateJobId,
      sheet: {
        sheet_id: "sheet_889_1194",
        width: 889,
        height: 1194,
        margin_top: 10,
        margin_right: 10,
        margin_bottom: 10,
        margin_left: 10,
        gripper_mm: 12,
        material: "white_card",
        thickness: "350gsm",
        cost_per_sheet: 4.2
      },
      candidate_items: [
        {
          item_id: "item_1",
          order_id: "O001",
          polygon: { shape_id: "shape_1", outer: [[0, 0], [120, 0], [120, 80], [0, 80]] },
          priority_score: 0.9,
          min_gap_mm: 3,
          bleed_mm: 2
        },
        {
          item_id: "item_2",
          order_id: "O002",
          polygon: { shape_id: "shape_2", outer: [[0, 0], [160, 0], [160, 120], [0, 120]] },
          priority_score: 0.7,
          min_gap_mm: 3,
          bleed_mm: 2
        }
      ]
    },
    null,
    2
  )
);
const result = ref<Record<string, unknown> | null>(null);
const materialReadiness = ref<MaterialAvailabilityCheck | null>(null);
const productionReadiness = ref<ProductionReadinessCheck | null>(null);
const productionAlerts = ref<ProductionAlertCheck | null>(null);
const procurementAlerts = ref<ProcurementAlertCheck | null>(null);
const exceptionWritebacks = ref<ExceptionWritebackResult | null>(null);
const svg = ref("");
const error = ref("");
const loading = ref(false);

async function createAndRun() {
  await runWithJob(async (job) => {
    const runResult = await apiRequest<{ solutions: Array<{ solution_id: string; job_id: string }> }>(
      `/nesting/jobs/${job.job_id}/run`,
      { method: "POST" }
    );
    result.value = runResult as unknown as Record<string, unknown>;
    const first = runResult.solutions[0];
    appStore.setLastSolution(first.solution_id, first.job_id);
    svg.value = await apiRequest<string>(`/solutions/${first.solution_id}/preview.svg`);
  });
}

async function createAndQueue() {
  await runWithJob(async (job) => {
    const task = await apiRequest<WorkTask>(`/nesting/jobs/${job.job_id}/run-async`, { method: "POST" });
    result.value = task as unknown as Record<string, unknown>;
    svg.value = "";
  });
}

async function checkMaterialReadiness() {
  await runWithJob(async (job) => {
    const check = await apiRequest<MaterialAvailabilityCheck>(`/nesting/jobs/${job.job_id}/material-readiness`);
    materialReadiness.value = check;
    result.value = check as unknown as Record<string, unknown>;
    svg.value = "";
  });
}

async function checkProductionReadiness() {
  await runWithJob(async (job) => {
    const check = await apiRequest<ProductionReadinessCheck>(`/nesting/jobs/${job.job_id}/production-readiness`);
    productionReadiness.value = check;
    result.value = check as unknown as Record<string, unknown>;
    svg.value = "";
  });
}

async function checkProductionAlerts() {
  await runWithJob(async (job) => {
    const check = await apiRequest<ProductionAlertCheck>(`/nesting/jobs/${job.job_id}/production-alerts/check`, {
      method: "POST",
      body: JSON.stringify({ notify: true })
    });
    productionAlerts.value = check;
    productionReadiness.value = check.readiness;
    result.value = check as unknown as Record<string, unknown>;
    svg.value = "";
  });
}

async function checkProcurementAlerts() {
  await runWithJob(async (job) => {
    const check = await apiRequest<ProcurementAlertCheck>(`/nesting/jobs/${job.job_id}/procurement-alerts/check`, {
      method: "POST",
      body: JSON.stringify({ notify: true, safety_stock_rate: 0.1 })
    });
    procurementAlerts.value = check;
    materialReadiness.value = check.material_readiness;
    result.value = check as unknown as Record<string, unknown>;
    svg.value = "";
  });
}

async function runExceptionWritebacks() {
  await runWithJob(async (job) => {
    const resultPayload = await apiRequest<ExceptionWritebackResult>(`/nesting/jobs/${job.job_id}/exception-writebacks/run`, {
      method: "POST",
      body: JSON.stringify({ dry_run: true, safety_stock_rate: 0.1 })
    });
    exceptionWritebacks.value = resultPayload;
    result.value = resultPayload as unknown as Record<string, unknown>;
    svg.value = "";
  });
}

function statusType(status: string) {
  if (["ready", "ok", "scheduled", "in_progress", "completed", "delivered"].includes(status)) return "success";
  if (["blocked", "shortage", "failed"].includes(status)) return "danger";
  if (["partial", "missing", "unknown", "skipped"].includes(status)) return "warning";
  return "warning";
}

async function runWithJob(action: (job: { job_id: string }) => Promise<void>) {
  loading.value = true;
  error.value = "";
  materialReadiness.value = null;
  productionReadiness.value = null;
  productionAlerts.value = null;
  procurementAlerts.value = null;
  exceptionWritebacks.value = null;
  try {
    const job = JSON.parse(jobJson.value) as { job_id: string };
    await apiRequest("/nesting/jobs", { method: "POST", body: jobJson.value });
    await action(job);
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <section>
    <div class="page-header">
      <div>
        <h1 class="page-title">拼版任务创建</h1>
        <div class="page-subtitle">NestingJob JSON、SolverAdapter、Validator 与异步队列</div>
      </div>
      <div class="solution-toolbar">
        <el-button type="primary" :loading="loading" @click="createAndRun">
          <Play :size="16" />
          创建并运行
        </el-button>
        <el-button type="warning" :loading="loading" @click="createAndQueue">
          <Send :size="16" />
          创建并入队
        </el-button>
        <el-button type="success" :loading="loading" @click="checkMaterialReadiness">
          <ShieldCheck :size="16" />
          检查物料
        </el-button>
        <el-button type="info" :loading="loading" @click="checkProductionReadiness">
          <ClipboardCheck :size="16" />
          生产检查
        </el-button>
        <el-button type="danger" :loading="loading" @click="checkProductionAlerts">
          <BellRing :size="16" />
          异常告警
        </el-button>
        <el-button type="warning" :loading="loading" @click="checkProcurementAlerts">
          <ShoppingCart :size="16" />
          采购预警
        </el-button>
        <el-button type="info" :loading="loading" @click="runExceptionWritebacks">
          <CloudUpload :size="16" />
          异常回写
        </el-button>
      </div>
    </div>
    <div class="two-column">
      <div class="work-band">
        <el-input v-model="jobJson" type="textarea" :rows="25" />
        <el-alert v-if="error" :title="error" type="error" :closable="false" style="margin-top: 12px" />
      </div>
      <div>
        <div v-if="materialReadiness" class="work-band material-readiness">
          <div class="readiness-header">
            <span>物料放行</span>
            <el-tag :type="statusType(materialReadiness.overall_status)">
              {{ materialReadiness.overall_status }}
            </el-tag>
          </div>
          <el-table :data="materialReadiness.items" size="small" style="width: 100%">
            <el-table-column prop="material" label="物料" min-width="130" />
            <el-table-column prop="required_qty" label="需求" width="90" />
            <el-table-column prop="net_available_qty" label="可用" width="90" />
            <el-table-column prop="shortage_qty" label="缺口" width="90" />
            <el-table-column prop="unit" label="单位" width="80" />
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <el-tag :type="statusType(row.status)" size="small">{{ row.status }}</el-tag>
              </template>
            </el-table-column>
          </el-table>
          <el-alert
            v-for="warning in materialReadiness.warnings"
            :key="warning"
            :title="warning"
            type="warning"
            :closable="false"
            style="margin-top: 10px"
          />
        </div>
        <div v-if="procurementAlerts" class="work-band material-readiness">
          <div class="readiness-header">
            <span>采购预警</span>
            <el-tag :type="procurementAlerts.status === 'alerting' ? 'danger' : 'success'">
              {{ procurementAlerts.status }} / 通知 {{ procurementAlerts.notification_count }}
            </el-tag>
          </div>
          <el-table :data="procurementAlerts.recommendations" size="small" style="width: 100%">
            <el-table-column prop="material" label="物料" min-width="140" />
            <el-table-column prop="shortage_qty" label="缺口" width="90" />
            <el-table-column prop="recommended_purchase_qty" label="建议采购" width="110" />
            <el-table-column prop="unit" label="单位" width="80" />
            <el-table-column label="级别" width="90">
              <template #default="{ row }">
                <el-tag :type="row.severity === 'critical' ? 'danger' : 'warning'" size="small">{{ row.severity }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="订单" min-width="170">
              <template #default="{ row }">{{ row.order_ids.join(", ") || "-" }}</template>
            </el-table-column>
          </el-table>
        </div>
        <div v-if="exceptionWritebacks" class="work-band material-readiness">
          <div class="readiness-header">
            <span>异常回写</span>
            <el-tag :type="exceptionWritebacks.status === 'completed' ? 'success' : statusType(exceptionWritebacks.status)">
              {{ exceptionWritebacks.status }} / {{ exceptionWritebacks.action_count }}
            </el-tag>
          </div>
          <el-table :data="exceptionWritebacks.actions" size="small" style="width: 100%">
            <el-table-column prop="system_type" label="系统" width="80" />
            <el-table-column prop="target_type" label="目标" min-width="130" />
            <el-table-column prop="requested_status" label="回写状态" min-width="150" />
            <el-table-column prop="reason" label="原因" min-width="140" />
            <el-table-column label="结果" width="100">
              <template #default="{ row }">
                <el-tag :type="statusType(row.writeback_log.status)" size="small">{{ row.writeback_log.status }}</el-tag>
              </template>
            </el-table-column>
          </el-table>
        </div>
        <div v-if="productionAlerts" class="work-band material-readiness">
          <div class="readiness-header">
            <span>异常告警</span>
            <el-tag :type="productionAlerts.status === 'alerting' ? 'danger' : 'success'">
              {{ productionAlerts.status }} / 通知 {{ productionAlerts.notification_count }}
            </el-tag>
          </div>
          <el-table :data="productionAlerts.alerts" size="small" style="width: 100%">
            <el-table-column prop="code" label="类型" min-width="180" />
            <el-table-column label="级别" width="90">
              <template #default="{ row }">
                <el-tag :type="row.severity === 'critical' ? 'danger' : 'warning'" size="small">{{ row.severity }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="status" label="状态" width="110" />
            <el-table-column prop="message" label="说明" min-width="220" />
            <el-table-column label="订单" min-width="160">
              <template #default="{ row }">{{ row.affected_order_ids.join(", ") || "-" }}</template>
            </el-table-column>
          </el-table>
        </div>
        <div v-if="productionReadiness" class="work-band material-readiness">
          <div class="readiness-header">
            <span>生产闭环</span>
            <el-tag :type="statusType(productionReadiness.overall_status)">
              {{ productionReadiness.overall_status }}
            </el-tag>
          </div>
          <div class="status-strip">
            <el-tag :type="statusType(productionReadiness.material_status)">物料 {{ productionReadiness.material_status }}</el-tag>
            <el-tag :type="statusType(productionReadiness.schedule_status)">排程 {{ productionReadiness.schedule_status }}</el-tag>
            <el-tag :type="statusType(productionReadiness.delivery_status)">交付 {{ productionReadiness.delivery_status }}</el-tag>
          </div>
          <el-table :data="productionReadiness.schedule_items" size="small" style="width: 100%; margin-top: 12px">
            <el-table-column prop="order_id" label="订单" min-width="130" />
            <el-table-column prop="required_qty" label="需求" width="90" />
            <el-table-column prop="scheduled_qty" label="计划" width="90" />
            <el-table-column prop="line_code" label="产线" width="90" />
            <el-table-column prop="machine_code" label="机台" width="110" />
            <el-table-column label="排程" width="100">
              <template #default="{ row }">
                <el-tag :type="statusType(row.status)" size="small">{{ row.status }}</el-tag>
              </template>
            </el-table-column>
          </el-table>
          <el-table :data="productionReadiness.delivery_items" size="small" style="width: 100%; margin-top: 12px">
            <el-table-column prop="order_id" label="订单" min-width="130" />
            <el-table-column prop="required_qty" label="需求" width="90" />
            <el-table-column prop="delivered_qty" label="交付" width="90" />
            <el-table-column prop="shipment_no" label="发运单" min-width="130" />
            <el-table-column prop="delivered_at" label="签收时间" min-width="150" />
            <el-table-column label="交付" width="100">
              <template #default="{ row }">
                <el-tag :type="statusType(row.status)" size="small">{{ row.status }}</el-tag>
              </template>
            </el-table-column>
          </el-table>
          <el-alert
            v-for="warning in productionReadiness.warnings"
            :key="warning"
            :title="warning"
            type="warning"
            :closable="false"
            style="margin-top: 10px"
          />
        </div>
        <div class="work-band"><pre class="pre-wrap">{{ result }}</pre></div>
        <SvgPreview :svg="svg" />
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

.material-readiness {
  margin-bottom: 12px;
}

.readiness-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  font-weight: 600;
}

.status-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
</style>
