<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { Check, CheckCheck, RefreshCw, Save, Users } from "@lucide/vue";
import { apiRequest } from "../services/api";
import { useAppStore } from "../stores/app";

type NotificationRead = {
  id: string;
  user_id: string;
  event_type: string;
  title: string;
  message: string;
  target_type?: string | null;
  target_id?: string | null;
  payload: Record<string, unknown>;
  is_read: boolean;
  read_at?: string | null;
  created_at: string;
};

type MessageTemplateRead = {
  id: string;
  name: string;
  event_type: string;
  channel: "in_app" | "webhook" | "email";
  title_template: string;
  message_template: string;
  recipient_permission_code?: string | null;
  recipient_group_id?: string | null;
  escalation_permission_code?: string | null;
  escalation_group_id?: string | null;
  escalation_after_minutes?: number | null;
  is_active: boolean;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

type NotificationRecipientGroupRead = {
  id: string;
  name: string;
  description?: string | null;
  member_user_ids: string[];
  permission_codes: string[];
  department_codes: string[];
  is_active: boolean;
  metadata: Record<string, unknown>;
  resolved_user_count: number;
  created_at: string;
  updated_at: string;
};

const notifications = ref<NotificationRead[]>([]);
const templates = ref<MessageTemplateRead[]>([]);
const recipientGroups = ref<NotificationRecipientGroupRead[]>([]);
const activeTab = ref("notifications");
const unreadOnly = ref(false);
const loading = ref(false);
const templateLoading = ref(false);
const groupLoading = ref(false);
const saving = ref(false);
const templateSaving = ref(false);
const groupSaving = ref(false);
const error = ref("");
const templateError = ref("");
const groupError = ref("");
const appStore = useAppStore();
const canManageNotifications = computed(() => appStore.hasPermission("notifications:manage"));
const templateForm = reactive({
  name: "任务队列告警",
  event_type: "work_task.queued_high",
  channel: "in_app" as "in_app" | "webhook" | "email",
  title_template: "任务队列告警",
  message_template: "当前队列 {metrics.queued}，阈值 {alert.threshold}",
  recipient_permission_code: "audit:read",
  recipient_group_id: "",
  escalation_permission_code: "tasks:manage",
  escalation_group_id: "",
  escalation_after_minutes: 30,
  is_active: true,
  webhook_provider: "generic" as "generic" | "feishu" | "wecom",
  metadata_json: "{}",
});
const groupForm = reactive({
  name: "生产上线收件组",
  description: "",
  member_user_ids_text: "",
  permission_codes_text: "audit:read",
  department_codes_text: "",
  is_active: true,
  metadata_json: "{}",
});

function tagType(row: NotificationRead) {
  if (row.is_read) return "info";
  if (row.event_type.includes("failed") || row.event_type.includes("timed_out") || row.event_type.includes("rejected")) {
    return "danger";
  }
  if (row.event_type.includes("approved")) return "success";
  return "warning";
}

async function loadNotifications() {
  loading.value = true;
  error.value = "";
  try {
    notifications.value = await apiRequest<NotificationRead[]>(`/notifications?limit=200&unread_only=${unreadOnly.value}`);
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    loading.value = false;
  }
}

async function markRead(row: NotificationRead) {
  saving.value = true;
  error.value = "";
  try {
    const updated = await apiRequest<NotificationRead>(`/notifications/${row.id}/read`, { method: "POST" });
    notifications.value = notifications.value.map((item) => (item.id === updated.id ? updated : item));
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    saving.value = false;
  }
}

async function markAllRead() {
  saving.value = true;
  error.value = "";
  try {
    await apiRequest<{ updated_count: number }>("/notifications/read-all", { method: "POST" });
    await loadNotifications();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    saving.value = false;
  }
}

async function loadTemplates() {
  if (!canManageNotifications.value) {
    templateError.value = "缺少权限：notifications:manage";
    return;
  }
  templateLoading.value = true;
  templateError.value = "";
  try {
    const [templateRows, groupRows] = await Promise.all([
      apiRequest<MessageTemplateRead[]>("/notifications/templates?limit=200"),
      apiRequest<NotificationRecipientGroupRead[]>("/notifications/recipient-groups?limit=200"),
    ]);
    templates.value = templateRows;
    recipientGroups.value = groupRows;
  } catch (err) {
    templateError.value = err instanceof Error ? err.message : String(err);
  } finally {
    templateLoading.value = false;
  }
}

async function createTemplate() {
  if (!canManageNotifications.value) {
    templateError.value = "缺少权限：notifications:manage";
    return;
  }
  templateSaving.value = true;
  templateError.value = "";
  try {
    let metadata: Record<string, unknown> = {};
    if (templateForm.metadata_json.trim()) {
      metadata = JSON.parse(templateForm.metadata_json);
    }
    if (templateForm.channel === "webhook") {
      metadata.webhook_provider = templateForm.webhook_provider;
    }
    await apiRequest<MessageTemplateRead>("/notifications/templates", {
      method: "POST",
      body: JSON.stringify({
        name: templateForm.name,
        event_type: templateForm.event_type,
        channel: templateForm.channel,
        title_template: templateForm.title_template,
        message_template: templateForm.message_template,
        recipient_permission_code: templateForm.recipient_permission_code || null,
        recipient_group_id: templateForm.recipient_group_id || null,
        escalation_permission_code: templateForm.escalation_permission_code || null,
        escalation_group_id: templateForm.escalation_group_id || null,
        escalation_after_minutes: templateForm.escalation_after_minutes || null,
        is_active: templateForm.is_active,
        metadata,
      }),
    });
    await loadTemplates();
  } catch (err) {
    templateError.value = err instanceof Error ? err.message : String(err);
  } finally {
    templateSaving.value = false;
  }
}

function parseCsv(value: string) {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

async function loadGroups() {
  if (!canManageNotifications.value) {
    groupError.value = "缺少权限：notifications:manage";
    return;
  }
  groupLoading.value = true;
  groupError.value = "";
  try {
    recipientGroups.value = await apiRequest<NotificationRecipientGroupRead[]>("/notifications/recipient-groups?limit=200");
  } catch (err) {
    groupError.value = err instanceof Error ? err.message : String(err);
  } finally {
    groupLoading.value = false;
  }
}

async function createGroup() {
  if (!canManageNotifications.value) {
    groupError.value = "缺少权限：notifications:manage";
    return;
  }
  groupSaving.value = true;
  groupError.value = "";
  try {
    let metadata: Record<string, unknown> = {};
    if (groupForm.metadata_json.trim()) {
      metadata = JSON.parse(groupForm.metadata_json);
    }
    await apiRequest<NotificationRecipientGroupRead>("/notifications/recipient-groups", {
      method: "POST",
      body: JSON.stringify({
        name: groupForm.name,
        description: groupForm.description || null,
        member_user_ids: parseCsv(groupForm.member_user_ids_text),
        permission_codes: parseCsv(groupForm.permission_codes_text),
        department_codes: parseCsv(groupForm.department_codes_text),
        is_active: groupForm.is_active,
        metadata,
      }),
    });
    groupForm.name = "";
    groupForm.description = "";
    groupForm.member_user_ids_text = "";
    groupForm.permission_codes_text = "";
    groupForm.department_codes_text = "";
    groupForm.is_active = true;
    groupForm.metadata_json = "{}";
    await loadGroups();
  } catch (err) {
    groupError.value = err instanceof Error ? err.message : String(err);
  } finally {
    groupSaving.value = false;
  }
}

async function toggleGroup(row: NotificationRecipientGroupRead) {
  if (!canManageNotifications.value) {
    groupError.value = "缺少权限：notifications:manage";
    return;
  }
  groupSaving.value = true;
  groupError.value = "";
  try {
    const updated = await apiRequest<NotificationRecipientGroupRead>(`/notifications/recipient-groups/${row.id}`, {
      method: "PATCH",
      body: JSON.stringify({ is_active: !row.is_active }),
    });
    recipientGroups.value = recipientGroups.value.map((item) => (item.id === updated.id ? updated : item));
  } catch (err) {
    groupError.value = err instanceof Error ? err.message : String(err);
  } finally {
    groupSaving.value = false;
  }
}

async function toggleTemplate(row: MessageTemplateRead) {
  if (!canManageNotifications.value) {
    templateError.value = "缺少权限：notifications:manage";
    return;
  }
  templateSaving.value = true;
  templateError.value = "";
  try {
    const updated = await apiRequest<MessageTemplateRead>(`/notifications/templates/${row.id}`, {
      method: "PATCH",
      body: JSON.stringify({ is_active: !row.is_active }),
    });
    templates.value = templates.value.map((item) => (item.id === updated.id ? updated : item));
  } catch (err) {
    templateError.value = err instanceof Error ? err.message : String(err);
  } finally {
    templateSaving.value = false;
  }
}

function handleTabChange(name: string | number) {
  if (!canManageNotifications.value) {
    activeTab.value = "notifications";
    return;
  }
  if (name === "templates" && templates.value.length === 0) {
    void loadTemplates();
  }
  if (name === "groups" && recipientGroups.value.length === 0) {
    void loadGroups();
  }
}

function channelLabel(channel: MessageTemplateRead["channel"]) {
  if (channel === "webhook") return "Webhook";
  if (channel === "email") return "邮件";
  return "站内";
}

onMounted(loadNotifications);
</script>

<template>
  <section>
    <div class="page-header">
      <div>
        <h1 class="page-title">通知中心</h1>
        <div class="page-subtitle">审批、任务失败和超时事件</div>
      </div>
      <div class="toolbar">
        <el-checkbox v-model="unreadOnly" @change="loadNotifications">只看未读</el-checkbox>
        <el-button :loading="loading" @click="loadNotifications">
          <RefreshCw :size="16" />
          刷新
        </el-button>
        <el-button type="primary" :loading="saving" @click="markAllRead">
          <CheckCheck :size="16" />
          全部已读
        </el-button>
      </div>
    </div>

    <el-alert v-if="error" :title="error" type="error" :closable="false" style="margin-bottom: 16px" />

    <el-tabs v-model="activeTab" @tab-change="handleTabChange">
      <el-tab-pane label="通知" name="notifications">
        <div class="work-band">
          <el-table v-loading="loading" :data="notifications" border>
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <el-tag :type="tagType(row)">{{ row.is_read ? "已读" : "未读" }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="title" label="标题" min-width="160" />
            <el-table-column prop="message" label="消息" min-width="240" />
            <el-table-column prop="event_type" label="事件" min-width="180" />
            <el-table-column prop="target_id" label="目标" min-width="180" />
            <el-table-column prop="created_at" label="时间" min-width="180" />
            <el-table-column label="操作" width="90" fixed="right">
              <template #default="{ row }">
                <el-tooltip content="标记已读" placement="top">
                  <el-button
                    circle
                    size="small"
                    :disabled="row.is_read"
                    :loading="saving && !row.is_read"
                    aria-label="标记已读"
                    @click="markRead(row)"
                  >
                    <Check :size="15" />
                  </el-button>
                </el-tooltip>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>

      <el-tab-pane v-if="canManageNotifications" label="消息模板" name="templates">
        <el-alert
          v-if="templateError"
          :title="templateError"
          type="error"
          :closable="false"
          style="margin-bottom: 16px"
        />
        <div class="two-column">
          <div class="work-band">
            <div class="band-heading">创建模板</div>
            <el-form label-width="104px">
              <el-form-item label="名称">
                <el-input v-model="templateForm.name" />
              </el-form-item>
              <el-form-item label="事件">
                <el-input v-model="templateForm.event_type" />
              </el-form-item>
              <el-form-item label="通道">
                <el-select v-model="templateForm.channel" style="width: 100%">
                  <el-option label="站内" value="in_app" />
                  <el-option label="Webhook" value="webhook" />
                  <el-option label="邮件" value="email" />
                </el-select>
              </el-form-item>
              <el-form-item v-if="templateForm.channel === 'webhook'" label="Webhook类型">
                <el-select v-model="templateForm.webhook_provider" style="width: 100%">
                  <el-option label="通用 JSON" value="generic" />
                  <el-option label="飞书/Lark" value="feishu" />
                  <el-option label="企业微信" value="wecom" />
                </el-select>
              </el-form-item>
              <el-form-item label="标题模板">
                <el-input v-model="templateForm.title_template" />
              </el-form-item>
              <el-form-item label="正文模板">
                <el-input v-model="templateForm.message_template" type="textarea" :rows="4" />
              </el-form-item>
              <el-form-item label="接收权限">
                <el-input v-model="templateForm.recipient_permission_code" />
              </el-form-item>
              <el-form-item label="接收组">
                <el-select v-model="templateForm.recipient_group_id" clearable filterable style="width: 100%">
                  <el-option v-for="group in recipientGroups" :key="group.id" :label="group.name" :value="group.id" />
                </el-select>
              </el-form-item>
              <el-form-item label="升级权限">
                <el-input v-model="templateForm.escalation_permission_code" />
              </el-form-item>
              <el-form-item label="升级组">
                <el-select v-model="templateForm.escalation_group_id" clearable filterable style="width: 100%">
                  <el-option v-for="group in recipientGroups" :key="group.id" :label="group.name" :value="group.id" />
                </el-select>
              </el-form-item>
              <el-form-item label="升级分钟">
                <el-input-number v-model="templateForm.escalation_after_minutes" :min="1" style="width: 100%" />
              </el-form-item>
              <el-form-item label="启用">
                <el-switch v-model="templateForm.is_active" active-text="启用" inactive-text="停用" />
              </el-form-item>
              <el-form-item label="元数据">
                <el-input v-model="templateForm.metadata_json" type="textarea" :rows="3" />
              </el-form-item>
              <el-form-item>
                <el-button type="primary" :disabled="!canManageNotifications" :loading="templateSaving" @click="createTemplate">
                  <Save :size="16" />
                  保存
                </el-button>
              </el-form-item>
            </el-form>
          </div>

          <div class="work-band">
            <div class="template-toolbar">
              <div class="band-heading">模板列表</div>
              <el-button :disabled="!canManageNotifications" :loading="templateLoading" @click="loadTemplates">
                <RefreshCw :size="16" />
                刷新
              </el-button>
            </div>
            <el-table v-loading="templateLoading" :data="templates" border>
              <el-table-column label="状态" width="90">
                <template #default="{ row }">
                  <el-tag :type="row.is_active ? 'success' : 'info'">{{ row.is_active ? "启用" : "停用" }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="name" label="名称" min-width="150" />
              <el-table-column prop="event_type" label="事件" min-width="180" />
              <el-table-column label="通道" width="100">
                <template #default="{ row }">{{ channelLabel(row.channel) }}</template>
              </el-table-column>
              <el-table-column label="Webhook类型" min-width="120">
                <template #default="{ row }">
                  {{ row.channel === "webhook" ? row.metadata.webhook_provider || row.metadata.provider || "generic" : "-" }}
                </template>
              </el-table-column>
              <el-table-column prop="recipient_permission_code" label="接收权限" min-width="150" />
              <el-table-column label="接收组" min-width="150">
                <template #default="{ row }">
                  {{ recipientGroups.find((group) => group.id === row.recipient_group_id)?.name || row.recipient_group_id || "-" }}
                </template>
              </el-table-column>
              <el-table-column prop="escalation_permission_code" label="升级权限" min-width="150" />
              <el-table-column label="升级组" min-width="150">
                <template #default="{ row }">
                  {{ recipientGroups.find((group) => group.id === row.escalation_group_id)?.name || row.escalation_group_id || "-" }}
                </template>
              </el-table-column>
              <el-table-column label="操作" width="110" fixed="right">
                <template #default="{ row }">
                  <el-button size="small" :disabled="!canManageNotifications" :loading="templateSaving" @click="toggleTemplate(row)">
                    {{ row.is_active ? "停用" : "启用" }}
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane v-if="canManageNotifications" label="收件组" name="groups">
        <el-alert
          v-if="groupError"
          :title="groupError"
          type="error"
          :closable="false"
          style="margin-bottom: 16px"
        />
        <div class="two-column">
          <div class="work-band">
            <div class="band-heading">
              <Users :size="18" />
              <span>创建收件组</span>
            </div>
            <el-form label-width="112px">
              <el-form-item label="名称">
                <el-input v-model="groupForm.name" />
              </el-form-item>
              <el-form-item label="说明">
                <el-input v-model="groupForm.description" />
              </el-form-item>
              <el-form-item label="用户ID">
                <el-input v-model="groupForm.member_user_ids_text" type="textarea" :rows="2" />
              </el-form-item>
              <el-form-item label="权限编码">
                <el-input v-model="groupForm.permission_codes_text" type="textarea" :rows="2" />
              </el-form-item>
              <el-form-item label="部门编码">
                <el-input v-model="groupForm.department_codes_text" type="textarea" :rows="2" />
              </el-form-item>
              <el-form-item label="启用">
                <el-switch v-model="groupForm.is_active" active-text="启用" inactive-text="停用" />
              </el-form-item>
              <el-form-item label="元数据">
                <el-input v-model="groupForm.metadata_json" type="textarea" :rows="3" />
              </el-form-item>
              <el-form-item>
                <el-button type="primary" :disabled="!canManageNotifications" :loading="groupSaving" @click="createGroup">
                  <Save :size="16" />
                  保存
                </el-button>
              </el-form-item>
            </el-form>
          </div>

          <div class="work-band">
            <div class="template-toolbar">
              <div class="band-heading">收件组列表</div>
              <el-button :disabled="!canManageNotifications" :loading="groupLoading" @click="loadGroups">
                <RefreshCw :size="16" />
                刷新
              </el-button>
            </div>
            <el-table v-loading="groupLoading" :data="recipientGroups" border>
              <el-table-column label="状态" width="90">
                <template #default="{ row }">
                  <el-tag :type="row.is_active ? 'success' : 'info'">{{ row.is_active ? "启用" : "停用" }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="name" label="名称" min-width="150" />
              <el-table-column prop="description" label="说明" min-width="160" />
              <el-table-column label="解析人数" width="100">
                <template #default="{ row }">{{ row.resolved_user_count }}</template>
              </el-table-column>
              <el-table-column label="用户ID" min-width="180">
                <template #default="{ row }">{{ row.member_user_ids.join(", ") || "-" }}</template>
              </el-table-column>
              <el-table-column label="权限" min-width="160">
                <template #default="{ row }">{{ row.permission_codes.join(", ") || "-" }}</template>
              </el-table-column>
              <el-table-column label="部门" min-width="160">
                <template #default="{ row }">{{ row.department_codes.join(", ") || "-" }}</template>
              </el-table-column>
              <el-table-column label="操作" width="110" fixed="right">
                <template #default="{ row }">
                  <el-button size="small" :disabled="!canManageNotifications" :loading="groupSaving" @click="toggleGroup(row)">
                    {{ row.is_active ? "停用" : "启用" }}
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>
  </section>
</template>

<style scoped>
.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  justify-content: flex-end;
}

.template-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}
</style>
