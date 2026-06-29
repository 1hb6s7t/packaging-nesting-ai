<script setup lang="ts">
import { Bot, Play, RefreshCw, Send } from "@lucide/vue";
import { ElMessage } from "element-plus";
import { computed, onMounted, ref, watch } from "vue";
import { apiRequest } from "../services/api";

type AiToolDefinition = {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
};

type AiToolCallResult = {
  tool_name: string;
  status: "completed" | "blocked" | "failed";
  result: Record<string, unknown>;
  message: string;
  safety: Record<string, unknown>;
};

type AiChatResponse = {
  mode: string;
  message: string;
  available_tools: string[];
  recommended_tool_calls: Array<Record<string, unknown>>;
  actor: string;
  input: Record<string, unknown>;
};

const tools = ref<AiToolDefinition[]>([]);
const selectedTool = ref("search_orders");
const argumentsJson = ref(defaultArguments("search_orders"));
const chatInput = ref("查找同材同厚订单，比较求解结果并生成报告计划");
const toolOutput = ref<AiToolCallResult | null>(null);
const chatOutput = ref<AiChatResponse | null>(null);
const loadingTools = ref(false);
const executing = ref(false);
const planning = ref(false);

const selectedDefinition = computed(() => tools.value.find((tool) => tool.name === selectedTool.value));
const formattedToolOutput = computed(() => formatJson(toolOutput.value));
const formattedChatOutput = computed(() => formatJson(chatOutput.value));
const formattedParameters = computed(() => formatJson(selectedDefinition.value?.parameters || {}));

onMounted(loadTools);

watch(selectedTool, (toolName) => {
  argumentsJson.value = defaultArguments(toolName);
});

async function loadTools() {
  loadingTools.value = true;
  try {
    tools.value = await apiRequest<AiToolDefinition[]>("/ai/tools");
    if (!tools.value.some((tool) => tool.name === selectedTool.value) && tools.value.length) {
      selectedTool.value = tools.value[0].name;
    }
  } finally {
    loadingTools.value = false;
  }
}

async function executeSelectedTool() {
  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(argumentsJson.value || "{}");
  } catch {
    ElMessage.error("参数 JSON 格式无效");
    return;
  }
  executing.value = true;
  try {
    toolOutput.value = await apiRequest<AiToolCallResult>("/ai/tools/execute", {
      method: "POST",
      body: JSON.stringify({ tool_name: selectedTool.value, arguments: parsed })
    });
    ElMessage({
      type: toolOutput.value.status === "completed" ? "success" : toolOutput.value.status === "blocked" ? "warning" : "error",
      message: toolOutput.value.message
    });
  } finally {
    executing.value = false;
  }
}

async function sendPlanRequest() {
  planning.value = true;
  try {
    chatOutput.value = await apiRequest<AiChatResponse>("/ai/chat", {
      method: "POST",
      body: JSON.stringify({ message: chatInput.value })
    });
  } finally {
    planning.value = false;
  }
}

function formatJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function defaultArguments(toolName: string): string {
  const defaults: Record<string, Record<string, unknown>> = {
    search_orders: { query: "", limit: 20 },
    get_order_detail: { order_id: "" },
    get_artwork_geometry: { artwork_file_id: "" },
    get_sheet_specs: {},
    create_nesting_job: { sheet_id: "", candidate_order_ids: [] },
    run_solver: { job_id: "" },
    validate_solution: { solution_id: "" },
    compare_solutions: { job_id: "" },
    explain_unplaced_items: { solution_id: "" },
    generate_report: { solution_id: "" },
    export_pdf: { solution_id: "" },
    export_dxf: { solution_id: "" },
    write_back_crm: { solution_id: "" }
  };
  return formatJson(defaults[toolName] || {});
}
</script>

<template>
  <section>
    <div class="page-header">
      <div>
        <h1 class="page-title">AI 助手</h1>
        <div class="page-subtitle">受控工具执行与审计</div>
      </div>
      <el-button :loading="loadingTools" @click="loadTools">
        <RefreshCw :size="16" />
      </el-button>
    </div>

    <div class="two-column">
      <div class="work-band">
        <div class="band-heading"><Bot :size="18" /> Function Calling</div>
        <el-form label-position="top">
          <el-form-item label="工具">
            <el-select v-model="selectedTool" filterable style="width: 100%">
              <el-option v-for="tool in tools" :key="tool.name" :label="tool.name" :value="tool.name" />
            </el-select>
          </el-form-item>
          <el-form-item label="参数 JSON">
            <el-input v-model="argumentsJson" type="textarea" :rows="9" />
          </el-form-item>
          <div class="ai-toolbar">
            <el-button type="primary" :loading="executing" @click="executeSelectedTool">
              <Play :size="16" /> 执行
            </el-button>
          </div>
        </el-form>

        <el-divider />
        <div class="tool-description">{{ selectedDefinition?.description || "-" }}</div>
        <pre class="pre-wrap schema-preview">{{ formattedParameters }}</pre>
      </div>

      <div>
        <div class="work-band">
          <div class="band-heading"><Send :size="18" /> 计划</div>
          <el-input v-model="chatInput" type="textarea" :rows="4" />
          <div class="ai-toolbar">
            <el-button :loading="planning" @click="sendPlanRequest">
              <Send :size="16" /> 生成计划
            </el-button>
          </div>
          <pre class="pre-wrap">{{ formattedChatOutput }}</pre>
        </div>

        <div class="work-band">
          <div class="band-heading"><Play :size="18" /> 执行结果</div>
          <pre class="pre-wrap">{{ formattedToolOutput }}</pre>
        </div>

        <div class="work-band">
          <el-table :data="tools" border size="small">
            <el-table-column prop="name" label="工具" width="190" />
            <el-table-column prop="description" label="说明" min-width="260" />
          </el-table>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.ai-toolbar {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 12px;
}

.ai-toolbar :deep(.el-button) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.tool-description {
  color: #334155;
  font-size: 13px;
  line-height: 1.6;
}

.schema-preview {
  margin-top: 10px;
  padding: 10px;
  border: 1px solid #dde3ea;
  border-radius: 6px;
  background: #f8fafc;
  font-size: 12px;
}
</style>
