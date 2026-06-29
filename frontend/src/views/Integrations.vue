<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import { CheckCircle2, ClipboardCheck, PlugZap, RefreshCw, RotateCcw, Save, Send, ShieldCheck } from "@lucide/vue";
import { apiRequest } from "../services/api";

type ExternalSystem = {
  id: string;
  name: string;
  system_type: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
};

type AdapterConfig = {
  id: string;
  external_system_id: string;
  adapter_type: string;
  version: number;
  is_active: boolean;
  validation_status: "untested" | "passed" | "failed";
  config: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

type DictionarySignoff = {
  status?: "signed";
  signed_by?: string;
  signed_at?: string;
  approver_name?: string | null;
  note?: string | null;
  dictionary_keys?: string[];
  accepted_unmapped_statuses?: string[];
};

type AdapterFieldAcceptanceCheck = {
  scope: "record" | "mapping" | "status" | "writeback" | "sample" | "organization";
  field: string;
  required: boolean;
  status: "passed" | "warning" | "failed";
  source_path?: string | null;
  observed_count: number;
  missing_count: number;
  sample_values: string[];
  message: string;
};

type AdapterFieldAcceptanceResult = {
  config_id: string;
  external_system_id: string;
  system_type: string;
  adapter_type: string;
  adapter_version: number;
  status: "passed" | "warning" | "failed";
  domain_target?: string | null;
  sample_count: number;
  required_missing_count: number;
  unresolved_mapping_count: number;
  unmapped_status_count: number;
  checks: AdapterFieldAcceptanceCheck[];
  message: string;
};

type SyncTask = {
  id: string;
  external_system_id: string;
  task_type: string;
  status: string;
  payload: Record<string, unknown>;
  created_at: string;
};

type WritebackLog = {
  id: string;
  external_system_id?: string | null;
  target_id?: string | null;
  status: string;
  payload: Record<string, unknown>;
  created_at: string;
};

type ProductionSchedule = {
  id: string;
  external_system_id: string;
  sync_task_id?: string | null;
  external_id: string;
  order_id?: string | null;
  job_id?: string | null;
  line_code?: string | null;
  machine_code?: string | null;
  planned_start_at?: string | null;
  planned_end_at?: string | null;
  status?: string | null;
  quantity?: number | null;
  updated_at: string;
};

type InventorySnapshot = {
  id: string;
  external_system_id: string;
  sync_task_id?: string | null;
  external_id: string;
  material_code?: string | null;
  material_name?: string | null;
  warehouse_code?: string | null;
  status?: string | null;
  available_qty?: number | null;
  reserved_qty?: number | null;
  unit?: string | null;
  updated_at: string;
};

type DeliveryConfirmation = {
  id: string;
  external_system_id: string;
  sync_task_id?: string | null;
  external_id: string;
  order_id?: string | null;
  shipment_no?: string | null;
  carrier?: string | null;
  tracking_no?: string | null;
  status?: string | null;
  delivered_at?: string | null;
  quantity?: number | null;
  updated_at: string;
};

type AdapterStatus = {
  active_config_count: number;
  configured_system_count: number;
  enabled_system_count: number;
};

type AdapterReadinessCheck = {
  code: string;
  scope: string;
  status: "passed" | "warning" | "failed";
  severity: "info" | "warning" | "critical";
  message: string;
  target_type?: string | null;
  target_id?: string | null;
  evidence: Record<string, unknown>;
};

type AdapterReadinessReport = {
  status: "ready" | "warning" | "blocked";
  generated_at: string;
  required_system_types: string[];
  passed_count: number;
  warning_count: number;
  failed_count: number;
  checks: AdapterReadinessCheck[];
};

const loading = ref(false);
const saving = ref(false);
const actionLoading = ref("");
const error = ref("");
const status = ref<AdapterStatus | null>(null);
const readiness = ref<AdapterReadinessReport | null>(null);
const systems = ref<ExternalSystem[]>([]);
const configs = ref<AdapterConfig[]>([]);
const fieldAcceptance = ref<AdapterFieldAcceptanceResult | null>(null);
const syncTasks = ref<SyncTask[]>([]);
const retryQueue = ref<SyncTask[]>([]);
const writebackLogs = ref<WritebackLog[]>([]);
const productionSchedules = ref<ProductionSchedule[]>([]);
const inventorySnapshots = ref<InventorySnapshot[]>([]);
const deliveryConfirmations = ref<DeliveryConfirmation[]>([]);
const targetId = ref("");
const syncSystemType = ref("crm");
const crmDryRun = ref(true);
const writebackSystemType = ref("crm");
const writebackStatus = ref("completed");
const writebackPayloadJson = ref('{\n  "note": "approved solution"\n}');
const signoffForm = reactive({
  visible: false,
  config_id: "",
  note: "",
  approver_name: "",
  accepted_unmapped_statuses_text: "",
  confirmation: ""
});

const systemForm = reactive({
  name: "",
  system_type: "crm",
  enabled: true
});

const configForm = reactive({
  external_system_id: "",
  adapter_type: "crm_api",
  is_active: true,
  configJson: JSON.stringify(
    {
      base_url: "https://crm.example.test/api",
      mode: "mock",
      auth_type: "api_key",
      api_key: "replace-me",
      dry_run: true,
      incremental: true,
      incremental_cursor_param: "updated_after",
      incremental_cursor_path: "sync.next_cursor",
      record_cursor_path: "updated_at",
      retry_count: 2,
      retry_backoff_sec: 0.2,
      writeback: {
        dry_run: true,
        mode: "mock",
        endpoint: "/writebacks/{target_id}",
        method: "POST",
        confirmation_path: "ok",
        field_mapping: {
          external_id: "target_id",
          state: "status",
          confirmed_at: "confirmed_at"
        }
      },
      records_path: "data.orders",
      field_mapping: {
        order_id: "crm_id",
        external_order_id: "crm_id",
        customer_name: "customer.name",
        product_name: "product.title",
        quantity: "qty",
        material: "spec.material",
        thickness: "spec.thickness",
        due_date: "due"
      },
      inbound_status_dictionary: {
        READY_TO_RUN: "ready",
        WAIT_RELEASE: "pending_release"
      },
      defaults: {
        print_side: "single",
        min_gap_mm: 3,
        bleed_mm: 2
      },
      organization_acceptance: {
        required_org_unit_codes: ["planning", "press_line_a"],
        required_recipient_group_names: ["Production Exceptions"],
        require_users: true,
        require_recipient_groups: true
      },
      pages: [
        {
          data: {
            orders: [
              {
                crm_id: "CRM-001",
                customer: { name: "Acme" },
                product: { title: "Gift Box" },
                qty: 1200,
                spec: { material: "white_card", thickness: "350gsm" },
                due: "2026-07-01"
              }
            ]
          }
        }
      ]
    },
    null,
    2
  )
});

const systemOptions = computed(() => systems.value.map((item) => ({ label: `${item.name} (${item.system_type})`, value: item.id })));

function statusType(value: string) {
  if (value === "passed" || value === "completed" || value === "ready") return "success";
  if (value === "failed" || value === "blocked") return "danger";
  if (value === "skipped" || value === "warning") return "warning";
  return "info";
}

function systemName(systemId?: string | null) {
  return systems.value.find((item) => item.id === systemId)?.name || systemId || "-";
}

function formatJson(value: unknown) {
  return JSON.stringify(value, null, 2);
}

function dictionarySignoff(row: AdapterConfig): DictionarySignoff {
  const value = row.config.dictionary_signoff;
  return value && typeof value === "object" && !Array.isArray(value) ? (value as DictionarySignoff) : {};
}

function dictionarySignoffType(row: AdapterConfig) {
  return dictionarySignoff(row).status === "signed" ? "success" : "warning";
}

function dictionarySignoffLabel(row: AdapterConfig) {
  return dictionarySignoff(row).status === "signed" ? "已签核" : "未签核";
}

function openDictionarySignoff(row: AdapterConfig) {
  const signoff = dictionarySignoff(row);
  signoffForm.visible = true;
  signoffForm.config_id = row.id;
  signoffForm.note = signoff.note || "";
  signoffForm.approver_name = signoff.approver_name || "";
  signoffForm.accepted_unmapped_statuses_text = (signoff.accepted_unmapped_statuses || []).join("\n");
  signoffForm.confirmation = `SIGNOFF ${row.id}`;
}

async function signoffDictionary() {
  actionLoading.value = `signoff:${signoffForm.config_id}`;
  error.value = "";
  try {
    const accepted_unmapped_statuses = signoffForm.accepted_unmapped_statuses_text
      .split(/[\n,]/)
      .map((item) => item.trim())
      .filter(Boolean);
    await apiRequest(`/adapters/configs/${signoffForm.config_id}/dictionary-signoff`, {
      method: "POST",
      body: JSON.stringify({
        note: signoffForm.note || null,
        approver_name: signoffForm.approver_name || null,
        accepted_unmapped_statuses,
        confirmation: signoffForm.confirmation
      })
    });
    signoffForm.visible = false;
    ElMessage({ type: "success", message: "数据字典已签核" });
    await loadAll();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    actionLoading.value = "";
  }
}

async function loadAll() {
  loading.value = true;
  error.value = "";
  try {
    const [
      statusRow,
      readinessRow,
      systemRows,
      configRows,
      taskRows,
      retryRows,
      writebackRows,
      scheduleRows,
      inventoryRows,
      deliveryRows
    ] = await Promise.all([
      apiRequest<AdapterStatus>("/adapters/status"),
      apiRequest<AdapterReadinessReport>("/adapters/readiness?required_system_types=crm,mes,erp"),
      apiRequest<ExternalSystem[]>("/adapters/systems"),
      apiRequest<AdapterConfig[]>("/adapters/configs"),
      apiRequest<SyncTask[]>("/adapters/sync-tasks?limit=50"),
      apiRequest<SyncTask[]>("/adapters/sync-tasks/retry-queue?limit=50"),
      apiRequest<WritebackLog[]>("/adapters/writeback-logs?limit=50"),
      apiRequest<ProductionSchedule[]>("/adapters/production-schedules?limit=50"),
      apiRequest<InventorySnapshot[]>("/adapters/inventory-snapshots?limit=50"),
      apiRequest<DeliveryConfirmation[]>("/adapters/delivery-confirmations?limit=50")
    ]);
    status.value = statusRow;
    readiness.value = readinessRow;
    systems.value = systemRows;
    configs.value = configRows;
    syncTasks.value = taskRows;
    retryQueue.value = retryRows;
    writebackLogs.value = writebackRows;
    productionSchedules.value = scheduleRows;
    inventorySnapshots.value = inventoryRows;
    deliveryConfirmations.value = deliveryRows;
    if (!configForm.external_system_id && systemRows.length) {
      configForm.external_system_id = systemRows[0].id;
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    loading.value = false;
  }
}

async function createSystem() {
  saving.value = true;
  error.value = "";
  try {
    await apiRequest<ExternalSystem>("/adapters/systems", {
      method: "POST",
      body: JSON.stringify(systemForm)
    });
    systemForm.name = "";
    systemForm.system_type = "crm";
    systemForm.enabled = true;
    await loadAll();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    saving.value = false;
  }
}

async function createConfig() {
  saving.value = true;
  error.value = "";
  try {
    const config = JSON.parse(configForm.configJson) as Record<string, unknown>;
    await apiRequest<AdapterConfig>(`/adapters/systems/${configForm.external_system_id}/configs`, {
      method: "POST",
      body: JSON.stringify({
        adapter_type: configForm.adapter_type,
        is_active: configForm.is_active,
        config
      })
    });
    await loadAll();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    saving.value = false;
  }
}

async function updateSystem(row: ExternalSystem) {
  actionLoading.value = row.id;
  error.value = "";
  try {
    await apiRequest<ExternalSystem>(`/adapters/systems/${row.id}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled: row.enabled })
    });
    await loadAll();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    actionLoading.value = "";
  }
}

async function activateConfig(row: AdapterConfig) {
  actionLoading.value = row.id;
  error.value = "";
  try {
    await apiRequest<AdapterConfig>(`/adapters/configs/${row.id}/activate`, { method: "POST" });
    await loadAll();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    actionLoading.value = "";
  }
}

async function testConfig(row: AdapterConfig) {
  actionLoading.value = `test:${row.id}`;
  error.value = "";
  try {
    const result = await apiRequest<{ status: string; message: string }>(`/adapters/configs/${row.id}/test`, { method: "POST" });
    ElMessage({ type: result.status === "passed" ? "success" : "warning", message: result.message });
    await loadAll();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    actionLoading.value = "";
  }
}

async function runFieldAcceptance(row: AdapterConfig) {
  actionLoading.value = `field:${row.id}`;
  error.value = "";
  try {
    fieldAcceptance.value = await apiRequest<AdapterFieldAcceptanceResult>(
      `/adapters/configs/${row.id}/field-acceptance`,
      { method: "POST" }
    );
    ElMessage({
      type: fieldAcceptance.value.status === "passed" ? "success" : fieldAcceptance.value.status === "failed" ? "error" : "warning",
      message: fieldAcceptance.value.message
    });
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    actionLoading.value = "";
  }
}

async function runCrmSync() {
  actionLoading.value = "crm-sync";
  error.value = "";
  try {
    const suffix = `?dry_run=${crmDryRun.value ? "true" : "false"}`;
    const endpoint = syncSystemType.value === "crm" ? "/adapters/crm/sync" : `/adapters/${syncSystemType.value}/sync`;
    await apiRequest<Record<string, unknown>>(`${endpoint}${suffix}`, { method: "POST" });
    await loadAll();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    actionLoading.value = "";
  }
}

async function retrySyncTask(row: SyncTask) {
  actionLoading.value = `retry:${row.id}`;
  error.value = "";
  try {
    const result = await apiRequest<SyncTask>(`/adapters/sync-tasks/${row.id}/retry`, { method: "POST" });
    ElMessage({ type: result.status === "completed" ? "success" : "warning", message: `重试结果：${result.status}` });
    await loadAll();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    actionLoading.value = "";
  }
}

async function runCrmWriteback() {
  actionLoading.value = "crm-writeback";
  error.value = "";
  try {
    const extraPayload = writebackPayloadJson.value.trim()
      ? (JSON.parse(writebackPayloadJson.value) as Record<string, unknown>)
      : {};
    await apiRequest<Record<string, unknown>>(`/adapters/${writebackSystemType.value}/writeback`, {
      method: "POST",
      body: JSON.stringify({
        target_id: targetId.value || null,
        target_type: "solution",
        status: writebackStatus.value || "completed",
        payload: extraPayload
      })
    });
    await loadAll();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    actionLoading.value = "";
  }
}

onMounted(loadAll);
</script>

<template>
  <section>
    <div class="page-header">
      <div>
        <h1 class="page-title">系统集成</h1>
        <div class="page-subtitle">CRM / MES / ERP / 商业拼版软件 Adapter 配置、校验和审计</div>
      </div>
      <el-button type="primary" :loading="loading" @click="loadAll">
        <RefreshCw :size="16" />
        刷新
      </el-button>
    </div>

    <el-alert v-if="error" :title="error" type="error" :closable="false" style="margin-bottom: 16px" />

    <div class="metric-grid" style="margin-bottom: 16px">
      <div class="metric">
        <div class="metric-label">已启用系统</div>
        <div class="metric-value">{{ status?.enabled_system_count || 0 }}</div>
      </div>
      <div class="metric">
        <div class="metric-label">已配置系统</div>
        <div class="metric-value">{{ status?.configured_system_count || 0 }}</div>
      </div>
      <div class="metric">
        <div class="metric-label">启用配置</div>
        <div class="metric-value">{{ status?.active_config_count || 0 }}</div>
      </div>
      <div class="metric">
        <div class="metric-label">同步/回写事件</div>
        <div class="metric-value">{{ syncTasks.length + writebackLogs.length }}</div>
      </div>
      <div class="metric">
        <div class="metric-label">重试队列</div>
        <div class="metric-value">{{ retryQueue.length }}</div>
      </div>
      <div class="metric">
        <div class="metric-label">上线就绪</div>
        <div class="metric-value">{{ readiness?.status || "-" }}</div>
      </div>
    </div>

    <div class="work-band">
      <div class="template-toolbar">
        <div class="band-heading">集成上线就绪报告</div>
        <el-tag v-if="readiness" :type="statusType(readiness.status)">
          {{ readiness.status }}
        </el-tag>
      </div>
      <div v-if="readiness" class="acceptance-summary">
        <span>必需系统 {{ readiness.required_system_types.join(", ") }}</span>
        <span>通过 {{ readiness.passed_count }}</span>
        <span>警告 {{ readiness.warning_count }}</span>
        <span>阻断 {{ readiness.failed_count }}</span>
        <span>生成 {{ readiness.generated_at }}</span>
      </div>
      <el-table v-if="readiness" :data="readiness.checks" border>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="scope" label="范围" width="110" />
        <el-table-column prop="code" label="检查项" min-width="170" />
        <el-table-column prop="severity" label="级别" width="100" />
        <el-table-column prop="message" label="说明" min-width="280" />
        <el-table-column prop="target_id" label="目标" min-width="170" />
      </el-table>
    </div>

    <div class="two-column">
      <div class="work-band">
        <div class="band-heading">
          <PlugZap :size="18" />
          <span>创建外部系统</span>
        </div>
        <el-form label-width="84px">
          <el-form-item label="名称"><el-input v-model="systemForm.name" placeholder="CRM Production" /></el-form-item>
          <el-form-item label="类型">
            <el-select v-model="systemForm.system_type" style="width: 100%">
              <el-option label="CRM" value="crm" />
              <el-option label="MES" value="mes" />
              <el-option label="ERP" value="erp" />
              <el-option label="Solver" value="solver" />
              <el-option label="Other" value="other" />
            </el-select>
          </el-form-item>
          <el-form-item label="启用"><el-switch v-model="systemForm.enabled" /></el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="saving" @click="createSystem">
              <Save :size="16" />
              保存
            </el-button>
          </el-form-item>
        </el-form>
      </div>

      <div class="work-band">
        <div class="band-heading">
          <Save :size="18" />
          <span>创建配置版本</span>
        </div>
        <el-form label-width="96px">
          <el-form-item label="外部系统">
            <el-select v-model="configForm.external_system_id" filterable style="width: 100%">
              <el-option v-for="item in systemOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="Adapter"><el-input v-model="configForm.adapter_type" placeholder="crm_api" /></el-form-item>
          <el-form-item label="立即启用"><el-switch v-model="configForm.is_active" /></el-form-item>
          <el-form-item label="配置 JSON">
            <el-input v-model="configForm.configJson" type="textarea" :rows="10" spellcheck="false" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="saving" :disabled="!configForm.external_system_id" @click="createConfig">
              <Save :size="16" />
              保存版本
            </el-button>
          </el-form-item>
        </el-form>
      </div>
    </div>

    <div class="work-band">
      <div class="band-heading">外部系统</div>
      <el-table v-loading="loading" :data="systems" border>
        <el-table-column prop="name" label="名称" min-width="180" />
        <el-table-column prop="system_type" label="类型" width="100" />
        <el-table-column label="启用" width="110">
          <template #default="{ row }">
            <el-switch v-model="row.enabled" :loading="actionLoading === row.id" @change="updateSystem(row)" />
          </template>
        </el-table-column>
        <el-table-column prop="updated_at" label="更新时间" min-width="180" />
      </el-table>
    </div>

    <div class="work-band">
      <div class="band-heading">配置版本</div>
      <el-table v-loading="loading" :data="configs" border>
        <el-table-column label="系统" min-width="170">
          <template #default="{ row }">{{ systemName(row.external_system_id) }}</template>
        </el-table-column>
        <el-table-column prop="adapter_type" label="Adapter" min-width="130" />
        <el-table-column prop="version" label="版本" width="80" />
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'info'">{{ row.is_active ? "启用" : "停用" }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="校验" width="110">
          <template #default="{ row }">
            <el-tag :type="statusType(row.validation_status)">{{ row.validation_status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="字典签核" width="120">
          <template #default="{ row }">
            <el-tag :type="dictionarySignoffType(row)">{{ dictionarySignoffLabel(row) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="配置" min-width="260">
          <template #default="{ row }"><pre class="pre-wrap">{{ formatJson(row.config) }}</pre></template>
        </el-table-column>
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-tooltip content="启用版本" placement="top">
              <el-button circle size="small" :disabled="row.is_active" :loading="actionLoading === row.id" @click="activateConfig(row)">
                <CheckCircle2 :size="15" />
              </el-button>
            </el-tooltip>
            <el-tooltip content="校验配置" placement="top">
              <el-button circle size="small" :loading="actionLoading === `test:${row.id}`" @click="testConfig(row)">
                <PlugZap :size="15" />
              </el-button>
            </el-tooltip>
            <el-tooltip content="字段验收" placement="top">
              <el-button circle size="small" :loading="actionLoading === `field:${row.id}`" @click="runFieldAcceptance(row)">
                <ClipboardCheck :size="15" />
              </el-button>
            </el-tooltip>
            <el-tooltip content="数据字典签核" placement="top">
              <el-button circle size="small" :loading="actionLoading === `signoff:${row.id}`" @click="openDictionarySignoff(row)">
                <ShieldCheck :size="15" />
              </el-button>
            </el-tooltip>
          </template>
        </el-table-column>
      </el-table>

      <div v-if="fieldAcceptance" class="acceptance-panel">
        <div class="acceptance-summary">
          <el-tag :type="statusType(fieldAcceptance.status)">{{ fieldAcceptance.status }}</el-tag>
          <span>{{ fieldAcceptance.message }}</span>
          <span>样本 {{ fieldAcceptance.sample_count }}</span>
          <span>必填缺口 {{ fieldAcceptance.required_missing_count }}</span>
          <span>映射问题 {{ fieldAcceptance.unresolved_mapping_count }}</span>
          <span>状态字典问题 {{ fieldAcceptance.unmapped_status_count }}</span>
        </div>
        <el-table :data="fieldAcceptance.checks" border>
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag :type="statusType(row.status)">{{ row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="scope" label="范围" width="100" />
          <el-table-column label="字段" min-width="150">
            <template #default="{ row }">
              {{ row.field }}{{ row.required ? " *" : "" }}
            </template>
          </el-table-column>
          <el-table-column prop="source_path" label="来源路径" min-width="170" />
          <el-table-column label="观测/缺失" width="110">
            <template #default="{ row }">{{ row.observed_count }} / {{ row.missing_count }}</template>
          </el-table-column>
          <el-table-column label="样本值" min-width="180">
            <template #default="{ row }">{{ row.sample_values?.join(", ") || "-" }}</template>
          </el-table-column>
          <el-table-column prop="message" label="说明" min-width="260" />
        </el-table>
      </div>
    </div>

    <el-dialog v-model="signoffForm.visible" title="数据字典签核" width="560px">
      <el-form label-width="128px">
        <el-form-item label="配置 ID">
          <el-input v-model="signoffForm.config_id" disabled />
        </el-form-item>
        <el-form-item label="签核人">
          <el-input v-model="signoffForm.approver_name" placeholder="客户或内部签核人" />
        </el-form-item>
        <el-form-item label="接受未映射值">
          <el-input
            v-model="signoffForm.accepted_unmapped_statuses_text"
            type="textarea"
            :rows="3"
            placeholder="每行一个客户状态码；字段验收仍有未映射状态时必须填写"
          />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="signoffForm.note" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item label="确认短语">
          <el-input v-model="signoffForm.confirmation" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="signoffForm.visible = false">取消</el-button>
        <el-button type="primary" :loading="actionLoading === `signoff:${signoffForm.config_id}`" @click="signoffDictionary">
          签核
        </el-button>
      </template>
    </el-dialog>

    <div class="two-column">
      <div class="work-band">
        <div class="band-heading">
          <Send :size="18" />
          <span>系统同步与回写</span>
        </div>
        <div style="display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-bottom: 12px">
          <el-select v-model="syncSystemType" style="width: 110px">
            <el-option label="CRM" value="crm" />
            <el-option label="MES" value="mes" />
            <el-option label="ERP" value="erp" />
          </el-select>
          <el-switch
            v-model="crmDryRun"
            active-text="Dry Run"
            inactive-text="正式导入"
          />
          <el-button :loading="actionLoading === 'crm-sync'" @click="runCrmSync">
            {{ crmDryRun ? "Dry Run 同步" : syncSystemType === "crm" ? "正式导入" : "正式同步" }}
          </el-button>
        </div>
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 10px; margin-bottom: 10px">
          <el-select v-model="writebackSystemType">
            <el-option label="CRM" value="crm" />
            <el-option label="MES" value="mes" />
            <el-option label="ERP" value="erp" />
          </el-select>
          <el-input v-model="targetId" placeholder="solution_id / order_id" />
          <el-input v-model="writebackStatus" placeholder="status" />
        </div>
        <el-input v-model="writebackPayloadJson" type="textarea" :rows="3" spellcheck="false" style="margin-bottom: 10px" />
        <el-button type="primary" :loading="actionLoading === 'crm-writeback'" @click="runCrmWriteback">回写确认</el-button>
      </div>
      <div class="work-band">
        <div class="band-heading">同步任务</div>
        <el-alert
          v-if="retryQueue.length"
          :title="`有 ${retryQueue.length} 个同步失败任务等待重试`"
          type="warning"
          :closable="false"
          style="margin-bottom: 12px"
        />
        <el-table :data="syncTasks" border>
          <el-table-column prop="created_at" label="时间" min-width="170" />
          <el-table-column label="系统" min-width="150">
            <template #default="{ row }">{{ systemName(row.external_system_id) }}</template>
          </el-table-column>
          <el-table-column prop="task_type" label="任务" min-width="130" />
          <el-table-column label="状态" width="100">
            <template #default="{ row }"><el-tag :type="statusType(row.status)">{{ row.status }}</el-tag></template>
          </el-table-column>
          <el-table-column label="导入" width="110">
            <template #default="{ row }">{{ row.payload.imported_count ?? 0 }} / {{ row.payload.mapped_count ?? 0 }}</template>
          </el-table-column>
          <el-table-column label="游标" min-width="190">
            <template #default="{ row }">
              {{ row.payload.cursor_in || "-" }} → {{ row.payload.next_cursor || "-" }}
            </template>
          </el-table-column>
          <el-table-column label="详情" min-width="260">
            <template #default="{ row }"><pre class="pre-wrap">{{ formatJson(row.payload) }}</pre></template>
          </el-table-column>
          <el-table-column label="操作" width="90" fixed="right">
            <template #default="{ row }">
              <el-tooltip v-if="row.status === 'failed'" content="重试同步任务" placement="top">
                <el-button circle size="small" :loading="actionLoading === `retry:${row.id}`" @click="retrySyncTask(row)">
                  <RotateCcw :size="15" />
                </el-button>
              </el-tooltip>
              <span v-else>-</span>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </div>

    <div class="work-band">
      <div class="band-heading">生产运营归档</div>
      <el-tabs>
        <el-tab-pane :label="`排程 ${productionSchedules.length}`">
          <el-table :data="productionSchedules" border>
            <el-table-column prop="updated_at" label="更新时间" min-width="170" />
            <el-table-column label="系统" min-width="150">
              <template #default="{ row }">{{ systemName(row.external_system_id) }}</template>
            </el-table-column>
            <el-table-column prop="external_id" label="外部工单" min-width="150" />
            <el-table-column prop="order_id" label="订单" min-width="130" />
            <el-table-column prop="line_code" label="产线" width="100" />
            <el-table-column prop="machine_code" label="机台" width="120" />
            <el-table-column prop="planned_start_at" label="计划开始" min-width="170" />
            <el-table-column label="状态" width="120">
              <template #default="{ row }"><el-tag :type="statusType(row.status || '')">{{ row.status || "-" }}</el-tag></template>
            </el-table-column>
            <el-table-column prop="quantity" label="数量" width="100" />
          </el-table>
        </el-tab-pane>
        <el-tab-pane :label="`库存 ${inventorySnapshots.length}`">
          <el-table :data="inventorySnapshots" border>
            <el-table-column prop="updated_at" label="更新时间" min-width="170" />
            <el-table-column label="系统" min-width="150">
              <template #default="{ row }">{{ systemName(row.external_system_id) }}</template>
            </el-table-column>
            <el-table-column prop="external_id" label="外部库存" min-width="150" />
            <el-table-column prop="material_code" label="物料编码" min-width="130" />
            <el-table-column prop="material_name" label="物料名称" min-width="170" />
            <el-table-column prop="warehouse_code" label="仓库" width="100" />
            <el-table-column label="状态" width="120">
              <template #default="{ row }"><el-tag :type="statusType(row.status || '')">{{ row.status || "-" }}</el-tag></template>
            </el-table-column>
            <el-table-column prop="available_qty" label="可用" width="100" />
            <el-table-column prop="reserved_qty" label="预留" width="100" />
            <el-table-column prop="unit" label="单位" width="80" />
          </el-table>
        </el-tab-pane>
        <el-tab-pane :label="`交付 ${deliveryConfirmations.length}`">
          <el-table :data="deliveryConfirmations" border>
            <el-table-column prop="updated_at" label="更新时间" min-width="170" />
            <el-table-column label="系统" min-width="150">
              <template #default="{ row }">{{ systemName(row.external_system_id) }}</template>
            </el-table-column>
            <el-table-column prop="external_id" label="外部交付" min-width="150" />
            <el-table-column prop="order_id" label="订单" min-width="130" />
            <el-table-column prop="shipment_no" label="发运单" min-width="130" />
            <el-table-column prop="carrier" label="承运商" width="110" />
            <el-table-column prop="tracking_no" label="追踪号" min-width="140" />
            <el-table-column label="状态" width="120">
              <template #default="{ row }"><el-tag :type="statusType(row.status || '')">{{ row.status || "-" }}</el-tag></template>
            </el-table-column>
            <el-table-column prop="delivered_at" label="签收时间" min-width="170" />
            <el-table-column prop="quantity" label="数量" width="100" />
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </div>

    <div class="work-band">
      <div class="band-heading">回写日志</div>
      <el-table :data="writebackLogs" border>
        <el-table-column prop="created_at" label="时间" min-width="170" />
        <el-table-column label="系统" min-width="150">
          <template #default="{ row }">{{ systemName(row.external_system_id) }}</template>
        </el-table-column>
        <el-table-column prop="target_id" label="目标 ID" min-width="160" />
        <el-table-column prop="status" label="状态" width="100" />
        <el-table-column label="详情" min-width="260">
          <template #default="{ row }"><pre class="pre-wrap">{{ formatJson(row.payload) }}</pre></template>
        </el-table-column>
      </el-table>
    </div>
  </section>
</template>

<style scoped>
.acceptance-panel {
  margin-top: 16px;
}

.acceptance-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin-bottom: 12px;
  color: #475569;
  font-size: 13px;
}
</style>
