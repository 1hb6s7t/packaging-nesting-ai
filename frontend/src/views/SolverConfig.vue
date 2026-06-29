<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { RefreshCw, Save, SlidersHorizontal } from "@lucide/vue";
import { apiRequest } from "../services/api";

type LicensePolicy = "open_source" | "review_required" | "commercial" | "disabled";

type SolverRegistryEntry = {
  id: string;
  name: string;
  version: string;
  enabled: boolean;
  license_policy: LicensePolicy;
  created_at: string;
  updated_at: string;
};

const loading = ref(false);
const saving = ref(false);
const error = ref("");
const rows = ref<SolverRegistryEntry[]>([]);
const editor = reactive({
  visible: false,
  name: "",
  version: "",
  enabled: false,
  license_policy: "review_required" as LicensePolicy
});

const enabledCount = computed(() => rows.value.filter((row) => row.enabled).length);
const commercialCount = computed(() => rows.value.filter((row) => row.license_policy === "commercial").length);

function licenseType(policy: LicensePolicy) {
  if (policy === "open_source") return "success";
  if (policy === "commercial") return "warning";
  if (policy === "disabled") return "info";
  return "primary";
}

function solverStatus(row: SolverRegistryEntry) {
  if (!row.enabled) return "停用";
  if (row.license_policy === "commercial") return "商业授权";
  if (row.license_policy === "review_required") return "待审查";
  return "可运行";
}

async function loadRegistry() {
  loading.value = true;
  error.value = "";
  try {
    rows.value = await apiRequest<SolverRegistryEntry[]>("/solvers/registry");
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    loading.value = false;
  }
}

function editSolver(row: SolverRegistryEntry) {
  editor.visible = true;
  editor.name = row.name;
  editor.version = row.version;
  editor.enabled = row.enabled;
  editor.license_policy = row.license_policy;
}

async function saveSolver() {
  saving.value = true;
  error.value = "";
  try {
    await apiRequest<SolverRegistryEntry>(`/solvers/registry/${encodeURIComponent(editor.name)}`, {
      method: "PATCH",
      body: JSON.stringify({
        version: editor.version,
        enabled: editor.enabled,
        license_policy: editor.license_policy
      })
    });
    editor.visible = false;
    await loadRegistry();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    saving.value = false;
  }
}

async function toggleSolver(row: SolverRegistryEntry) {
  saving.value = true;
  error.value = "";
  try {
    await apiRequest<SolverRegistryEntry>(`/solvers/registry/${encodeURIComponent(row.name)}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled: row.enabled })
    });
    await loadRegistry();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    saving.value = false;
  }
}

onMounted(loadRegistry);
</script>

<template>
  <section>
    <div class="page-header">
      <div>
        <h1 class="page-title">Solver 配置</h1>
        <div class="page-subtitle">Solver 注册表、启用状态、版本和许可证策略</div>
      </div>
      <el-button type="primary" :loading="loading" @click="loadRegistry">
        <RefreshCw :size="16" />
        刷新
      </el-button>
    </div>

    <el-alert v-if="error" :title="error" type="error" :closable="false" style="margin-bottom: 16px" />

    <div class="metric-grid" style="margin-bottom: 16px">
      <div class="metric">
        <div class="metric-label">注册 Solver</div>
        <div class="metric-value">{{ rows.length }}</div>
      </div>
      <div class="metric">
        <div class="metric-label">已启用</div>
        <div class="metric-value">{{ enabledCount }}</div>
      </div>
      <div class="metric">
        <div class="metric-label">商业策略</div>
        <div class="metric-value">{{ commercialCount }}</div>
      </div>
      <div class="metric">
        <div class="metric-label">运行入口</div>
        <div class="metric-value">Registry</div>
      </div>
    </div>

    <div class="work-band">
      <div class="band-heading">
        <SlidersHorizontal :size="18" />
        <span>Solver 注册表</span>
      </div>
      <el-table v-loading="loading" :data="rows" border>
        <el-table-column prop="name" label="Solver" min-width="180" />
        <el-table-column prop="version" label="版本" min-width="180" />
        <el-table-column label="启用" width="110">
          <template #default="{ row }">
            <el-switch v-model="row.enabled" :loading="saving" @change="toggleSolver(row)" />
          </template>
        </el-table-column>
        <el-table-column label="许可证策略" width="140">
          <template #default="{ row }">
            <el-tag :type="licenseType(row.license_policy)">{{ row.license_policy }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'">{{ solverStatus(row) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="updated_at" label="更新时间" min-width="180" />
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-tooltip content="编辑版本和策略" placement="top">
              <el-button circle size="small" @click="editSolver(row)">
                <SlidersHorizontal :size="15" />
              </el-button>
            </el-tooltip>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="editor.visible" title="编辑 Solver" width="520px">
      <el-form label-width="96px">
        <el-form-item label="Solver"><el-input v-model="editor.name" disabled /></el-form-item>
        <el-form-item label="版本"><el-input v-model="editor.version" /></el-form-item>
        <el-form-item label="启用"><el-switch v-model="editor.enabled" /></el-form-item>
        <el-form-item label="许可证策略">
          <el-select v-model="editor.license_policy" style="width: 100%">
            <el-option label="Open Source" value="open_source" />
            <el-option label="Review Required" value="review_required" />
            <el-option label="Commercial" value="commercial" />
            <el-option label="Disabled" value="disabled" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editor.visible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveSolver">
          <Save :size="16" />
          保存
        </el-button>
      </template>
    </el-dialog>
  </section>
</template>
