<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { CheckCircle2, PlayCircle, RefreshCw, Save } from "@lucide/vue";
import { apiRequest } from "../services/api";

type HardConstraint = {
  name: string;
  when: string;
  action: "reject";
  reason: string;
};

type SoftScoreRule = {
  name: string;
  expression: string;
  weight: number;
};

type RuleDefinition = {
  ruleset_id: string;
  hard_constraints: HardConstraint[];
  soft_scores: SoftScoreRule[];
};

type RuleSet = {
  id: string;
  name: string;
  version: string;
  is_active: boolean;
  definition: RuleDefinition;
  created_at: string;
  updated_at: string;
};

type RuleExecutionLog = {
  id: string;
  rule_set_id?: string | null;
  order_id?: string | null;
  result: Record<string, unknown>;
  created_at: string;
};

const loading = ref(false);
const saving = ref(false);
const activating = ref("");
const error = ref("");
const ruleSets = ref<RuleSet[]>([]);
const activeRuleSet = ref<RuleSet | null>(null);
const logs = ref<RuleExecutionLog[]>([]);

const createForm = reactive({
  name: "",
  version: "",
  is_active: false,
  definitionJson: ""
});

const activeHardConstraints = computed(() => activeRuleSet.value?.definition.hard_constraints || []);
const activeSoftScores = computed(() => activeRuleSet.value?.definition.soft_scores || []);
const totalWeight = computed(() => activeSoftScores.value.reduce((sum, rule) => sum + Number(rule.weight || 0), 0));

function formatJson(value: unknown) {
  return JSON.stringify(value, null, 2);
}

function resetCreateFormFromActive() {
  const base = activeRuleSet.value?.definition || {
    ruleset_id: "packaging_custom_v1",
    hard_constraints: [],
    soft_scores: []
  };
  createForm.name = "";
  createForm.version = "";
  createForm.is_active = false;
  createForm.definitionJson = formatJson(base);
}

async function loadAll() {
  loading.value = true;
  error.value = "";
  try {
    const [sets, active, logRows] = await Promise.all([
      apiRequest<RuleSet[]>("/rules/sets"),
      apiRequest<RuleSet>("/rules/sets/active"),
      apiRequest<RuleExecutionLog[]>("/rules/execution-logs?limit=100")
    ]);
    ruleSets.value = sets;
    activeRuleSet.value = active;
    logs.value = logRows;
    if (!createForm.definitionJson) {
      resetCreateFormFromActive();
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    loading.value = false;
  }
}

async function createRuleSet() {
  saving.value = true;
  error.value = "";
  try {
    const definition = JSON.parse(createForm.definitionJson) as RuleDefinition;
    await apiRequest<RuleSet>("/rules/sets", {
      method: "POST",
      body: JSON.stringify({
        name: createForm.name,
        version: createForm.version,
        is_active: createForm.is_active,
        definition
      })
    });
    createForm.name = "";
    createForm.version = "";
    createForm.is_active = false;
    createForm.definitionJson = "";
    await loadAll();
    resetCreateFormFromActive();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    saving.value = false;
  }
}

async function activateRuleSet(row: RuleSet) {
  activating.value = row.id;
  error.value = "";
  try {
    await apiRequest<RuleSet>(`/rules/sets/${row.id}/activate`, { method: "POST" });
    await loadAll();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    activating.value = "";
  }
}

function resultDecision(row: RuleExecutionLog) {
  const decision = row.result.decision as Record<string, unknown> | undefined;
  return decision || {};
}

function decisionAccepted(row: RuleExecutionLog) {
  return resultDecision(row).accepted === true;
}

function decisionScore(row: RuleExecutionLog) {
  const score = resultDecision(row).priority_score;
  return typeof score === "number" ? score.toFixed(4) : "-";
}

onMounted(loadAll);
</script>

<template>
  <section>
    <div class="page-header">
      <div>
        <h1 class="page-title">规则配置</h1>
        <div class="page-subtitle">规则集版本、启用状态、硬约束、软评分和执行日志</div>
      </div>
      <el-button type="primary" :loading="loading" @click="loadAll">
        <RefreshCw :size="16" />
        刷新
      </el-button>
    </div>

    <el-alert v-if="error" :title="error" type="error" :closable="false" style="margin-bottom: 16px" />

    <div class="metric-grid" style="margin-bottom: 16px">
      <div class="metric">
        <div class="metric-label">当前规则集</div>
        <div class="metric-value">{{ activeRuleSet?.name || "-" }}</div>
      </div>
      <div class="metric">
        <div class="metric-label">版本</div>
        <div class="metric-value">{{ activeRuleSet?.version || "-" }}</div>
      </div>
      <div class="metric">
        <div class="metric-label">软评分权重</div>
        <div class="metric-value">{{ totalWeight.toFixed(2) }}</div>
      </div>
      <div class="metric">
        <div class="metric-label">执行日志</div>
        <div class="metric-value">{{ logs.length }}</div>
      </div>
    </div>

    <div class="two-column">
      <div class="work-band">
        <div class="band-heading">
          <Save :size="18" />
          <span>创建规则集版本</span>
        </div>
        <el-form label-width="84px">
          <el-form-item label="名称"><el-input v-model="createForm.name" placeholder="Packaging Policy" /></el-form-item>
          <el-form-item label="版本"><el-input v-model="createForm.version" placeholder="v2" /></el-form-item>
          <el-form-item label="立即启用"><el-switch v-model="createForm.is_active" /></el-form-item>
          <el-form-item label="定义 JSON">
            <el-input v-model="createForm.definitionJson" type="textarea" :rows="18" spellcheck="false" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="saving" @click="createRuleSet">
              <Save :size="16" />
              保存
            </el-button>
          </el-form-item>
        </el-form>
      </div>

      <div>
        <div class="work-band">
          <div class="band-heading">
            <CheckCircle2 :size="18" />
            <span>当前启用规则</span>
          </div>
          <el-table v-loading="loading" :data="activeHardConstraints" border style="margin-bottom: 14px">
            <el-table-column prop="name" label="硬约束" min-width="150" />
            <el-table-column prop="when" label="条件" min-width="240" />
            <el-table-column prop="reason" label="拒绝原因" min-width="180" />
          </el-table>
          <el-table v-loading="loading" :data="activeSoftScores" border>
            <el-table-column prop="name" label="软评分" min-width="150" />
            <el-table-column prop="expression" label="表达式" min-width="240" />
            <el-table-column prop="weight" label="权重" width="100" />
          </el-table>
        </div>

        <div class="work-band">
          <div class="band-heading">规则集版本</div>
          <el-table v-loading="loading" :data="ruleSets" border>
            <el-table-column prop="name" label="名称" min-width="180" />
            <el-table-column prop="version" label="版本" width="100" />
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <el-tag :type="row.is_active ? 'success' : 'info'">{{ row.is_active ? "启用" : "停用" }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="updated_at" label="更新时间" min-width="180" />
            <el-table-column label="操作" width="110" fixed="right">
              <template #default="{ row }">
                <el-tooltip content="启用此版本" placement="top">
                  <el-button
                    circle
                    size="small"
                    :disabled="row.is_active"
                    :loading="activating === row.id"
                    aria-label="启用此版本"
                    @click="activateRuleSet(row)"
                  >
                    <PlayCircle :size="15" />
                  </el-button>
                </el-tooltip>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </div>
    </div>

    <div class="work-band">
      <div class="band-heading">规则执行日志</div>
      <el-table v-loading="loading" :data="logs" border>
        <el-table-column prop="created_at" label="时间" min-width="180" />
        <el-table-column prop="rule_set_id" label="规则集 ID" min-width="190" />
        <el-table-column prop="order_id" label="订单 ID" min-width="150" />
        <el-table-column label="结果" width="100">
          <template #default="{ row }">
            <el-tag :type="decisionAccepted(row) ? 'success' : 'danger'">
              {{ decisionAccepted(row) ? "通过" : "拒绝" }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="评分" width="100">
          <template #default="{ row }">{{ decisionScore(row) }}</template>
        </el-table-column>
        <el-table-column label="详情" min-width="320">
          <template #default="{ row }">
            <pre class="pre-wrap">{{ row.result }}</pre>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </section>
</template>
