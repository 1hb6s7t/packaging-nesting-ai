<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { RefreshCw, Save, ShieldPlus, UserPlus } from "@lucide/vue";
import { apiRequest } from "../services/api";

interface PermissionRead {
  id: string;
  code: string;
  description?: string | null;
}

interface RoleRead {
  id: string;
  name: string;
  description?: string | null;
  permission_codes: string[];
}

interface UserAccountRead {
  id: string;
  email: string;
  display_name: string;
  is_active: boolean;
  org_unit_code?: string | null;
  org_unit_name?: string | null;
  job_title?: string | null;
  external_user_id?: string | null;
  roles: RoleRead[];
  permissions: string[];
}

const loading = ref(false);
const saving = ref(false);
const error = ref("");
const users = ref<UserAccountRead[]>([]);
const roles = ref<RoleRead[]>([]);
const permissions = ref<PermissionRead[]>([]);

const roleForm = reactive({
  name: "",
  description: "",
  permission_codes: [] as string[]
});

const userForm = reactive({
  email: "",
  display_name: "",
  password: "",
  org_unit_code: "",
  org_unit_name: "",
  job_title: "",
  external_user_id: "",
  role_ids: [] as string[],
  is_active: true
});

const roleEditor = reactive({
  visible: false,
  id: "",
  name: "",
  description: "",
  permission_codes: [] as string[]
});

const userEditor = reactive({
  visible: false,
  id: "",
  email: "",
  display_name: "",
  password: "",
  org_unit_code: "",
  org_unit_name: "",
  job_title: "",
  external_user_id: "",
  role_ids: [] as string[],
  is_active: true
});

const passwordPolicyMessage = "密码必须为 12-128 字符，并且至少包含一个字母和一个数字";
const passwordLetterPattern = /\p{L}/u;
const passwordDigitPattern = /\p{Nd}/u;

function passwordPolicyError(password: string): string | null {
  if (
    password.length < 12 ||
    password.length > 128 ||
    !passwordLetterPattern.test(password) ||
    !passwordDigitPattern.test(password)
  ) {
    return passwordPolicyMessage;
  }
  return null;
}

async function loadAll() {
  loading.value = true;
  error.value = "";
  try {
    const [permissionRows, roleRows, userRows] = await Promise.all([
      apiRequest<PermissionRead[]>("/rbac/permissions"),
      apiRequest<RoleRead[]>("/rbac/roles"),
      apiRequest<UserAccountRead[]>("/rbac/users")
    ]);
    permissions.value = permissionRows;
    roles.value = roleRows;
    users.value = userRows;
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    loading.value = false;
  }
}

async function createRole() {
  saving.value = true;
  error.value = "";
  try {
    await apiRequest<RoleRead>("/rbac/roles", {
      method: "POST",
      body: JSON.stringify(roleForm)
    });
    roleForm.name = "";
    roleForm.description = "";
    roleForm.permission_codes = [];
    await loadAll();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    saving.value = false;
  }
}

async function createUser() {
  saving.value = true;
  error.value = "";
  try {
    const passwordError = passwordPolicyError(userForm.password);
    if (passwordError) {
      error.value = passwordError;
      return;
    }
    await apiRequest<UserAccountRead>("/rbac/users", {
      method: "POST",
      body: JSON.stringify(userForm)
    });
    userForm.email = "";
    userForm.display_name = "";
    userForm.password = "";
    userForm.org_unit_code = "";
    userForm.org_unit_name = "";
    userForm.job_title = "";
    userForm.external_user_id = "";
    userForm.role_ids = [];
    userForm.is_active = true;
    await loadAll();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    saving.value = false;
  }
}

function editRole(row: RoleRead) {
  roleEditor.visible = true;
  roleEditor.id = row.id;
  roleEditor.name = row.name;
  roleEditor.description = row.description || "";
  roleEditor.permission_codes = [...row.permission_codes];
}

async function saveRole() {
  saving.value = true;
  error.value = "";
  try {
    await apiRequest<RoleRead>(`/rbac/roles/${roleEditor.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        name: roleEditor.name,
        description: roleEditor.description,
        permission_codes: roleEditor.permission_codes
      })
    });
    roleEditor.visible = false;
    await loadAll();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    saving.value = false;
  }
}

function editUser(row: UserAccountRead) {
  userEditor.visible = true;
  userEditor.id = row.id;
  userEditor.email = row.email;
  userEditor.display_name = row.display_name;
  userEditor.password = "";
  userEditor.org_unit_code = row.org_unit_code || "";
  userEditor.org_unit_name = row.org_unit_name || "";
  userEditor.job_title = row.job_title || "";
  userEditor.external_user_id = row.external_user_id || "";
  userEditor.role_ids = row.roles.map((role) => role.id);
  userEditor.is_active = row.is_active;
}

async function saveUser() {
  saving.value = true;
  error.value = "";
  try {
    const payload: Record<string, unknown> = {
      display_name: userEditor.display_name,
      is_active: userEditor.is_active,
      org_unit_code: userEditor.org_unit_code || null,
      org_unit_name: userEditor.org_unit_name || null,
      job_title: userEditor.job_title || null,
      external_user_id: userEditor.external_user_id || null,
      role_ids: userEditor.role_ids
    };
    if (userEditor.password) {
      const passwordError = passwordPolicyError(userEditor.password);
      if (passwordError) {
        error.value = passwordError;
        return;
      }
      payload.password = userEditor.password;
    }
    await apiRequest<UserAccountRead>(`/rbac/users/${userEditor.id}`, {
      method: "PATCH",
      body: JSON.stringify(payload)
    });
    userEditor.visible = false;
    await loadAll();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    saving.value = false;
  }
}

onMounted(loadAll);
</script>

<template>
  <section>
    <div class="page-header">
      <div>
        <h1 class="page-title">权限管理</h1>
        <div class="page-subtitle">用户、角色、权限、组织映射与账号状态</div>
      </div>
      <el-button :loading="loading" @click="loadAll">
        <RefreshCw :size="16" />
        刷新
      </el-button>
    </div>

    <el-alert v-if="error" :title="error" type="error" :closable="false" style="margin-bottom: 16px" />

    <div class="two-column">
      <div class="work-band">
        <div class="band-heading">
          <UserPlus :size="18" />
          <span>创建用户</span>
        </div>
        <el-form label-width="84px">
          <el-form-item label="邮箱"><el-input v-model="userForm.email" /></el-form-item>
          <el-form-item label="姓名"><el-input v-model="userForm.display_name" /></el-form-item>
          <el-form-item label="密码"><el-input v-model="userForm.password" type="password" show-password /></el-form-item>
          <el-form-item label="部门编码"><el-input v-model="userForm.org_unit_code" /></el-form-item>
          <el-form-item label="部门名称"><el-input v-model="userForm.org_unit_name" /></el-form-item>
          <el-form-item label="岗位"><el-input v-model="userForm.job_title" /></el-form-item>
          <el-form-item label="外部ID"><el-input v-model="userForm.external_user_id" /></el-form-item>
          <el-form-item label="状态"><el-switch v-model="userForm.is_active" active-text="启用" inactive-text="停用" /></el-form-item>
          <el-form-item label="角色">
            <el-select v-model="userForm.role_ids" multiple filterable style="width: 100%">
              <el-option v-for="role in roles" :key="role.id" :label="role.name" :value="role.id" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="saving" @click="createUser">创建用户</el-button>
          </el-form-item>
        </el-form>
      </div>

      <div class="work-band">
        <div class="band-heading">
          <ShieldPlus :size="18" />
          <span>创建角色</span>
        </div>
        <el-form label-width="84px">
          <el-form-item label="名称"><el-input v-model="roleForm.name" /></el-form-item>
          <el-form-item label="说明"><el-input v-model="roleForm.description" /></el-form-item>
          <el-form-item label="权限">
            <el-select v-model="roleForm.permission_codes" multiple filterable style="width: 100%">
              <el-option v-for="permission in permissions" :key="permission.code" :label="permission.code" :value="permission.code" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="saving" @click="createRole">创建角色</el-button>
          </el-form-item>
        </el-form>
      </div>
    </div>

    <div class="work-band">
      <div class="band-heading">用户列表</div>
      <el-table v-loading="loading" :data="users" border>
        <el-table-column prop="email" label="邮箱" min-width="220" />
        <el-table-column prop="display_name" label="姓名" min-width="140" />
        <el-table-column prop="org_unit_code" label="部门编码" min-width="130" />
        <el-table-column prop="org_unit_name" label="部门名称" min-width="140" />
        <el-table-column prop="job_title" label="岗位" min-width="130" />
        <el-table-column prop="external_user_id" label="外部ID" min-width="130" />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'danger'">{{ row.is_active ? "启用" : "停用" }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="角色" min-width="180">
          <template #default="{ row }">
            <el-tag v-for="role in row.roles" :key="role.id" style="margin: 0 6px 6px 0">{{ role.name }}</el-tag>
            <span v-if="!row.roles.length">-</span>
          </template>
        </el-table-column>
        <el-table-column label="权限" min-width="260">
          <template #default="{ row }">
            <el-tag v-for="permission in row.permissions" :key="permission" type="info" style="margin: 0 6px 6px 0">
              {{ permission }}
            </el-tag>
            <span v-if="!row.permissions.length">-</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click="editUser(row)">编辑</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="two-column">
      <div class="work-band">
        <div class="band-heading">角色列表</div>
        <el-table v-loading="loading" :data="roles" border>
          <el-table-column prop="name" label="名称" min-width="140" />
          <el-table-column prop="description" label="说明" min-width="180" />
          <el-table-column label="权限" min-width="260">
            <template #default="{ row }">
              <el-tag v-for="permission in row.permission_codes" :key="permission" type="info" style="margin: 0 6px 6px 0">
                {{ permission }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="100">
            <template #default="{ row }">
              <el-button size="small" @click="editRole(row)">编辑</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div class="work-band">
        <div class="band-heading">权限字典</div>
        <el-table v-loading="loading" :data="permissions" border>
          <el-table-column prop="code" label="权限编码" min-width="180" />
          <el-table-column prop="description" label="说明" min-width="220" />
        </el-table>
      </div>
    </div>

    <el-dialog v-model="userEditor.visible" title="编辑用户" width="620px">
      <el-form label-width="84px">
        <el-form-item label="邮箱"><el-input v-model="userEditor.email" disabled /></el-form-item>
        <el-form-item label="姓名"><el-input v-model="userEditor.display_name" /></el-form-item>
        <el-form-item label="新密码"><el-input v-model="userEditor.password" type="password" show-password placeholder="留空则不修改" /></el-form-item>
        <el-form-item label="部门编码"><el-input v-model="userEditor.org_unit_code" /></el-form-item>
        <el-form-item label="部门名称"><el-input v-model="userEditor.org_unit_name" /></el-form-item>
        <el-form-item label="岗位"><el-input v-model="userEditor.job_title" /></el-form-item>
        <el-form-item label="外部ID"><el-input v-model="userEditor.external_user_id" /></el-form-item>
        <el-form-item label="状态"><el-switch v-model="userEditor.is_active" active-text="启用" inactive-text="停用" /></el-form-item>
        <el-form-item label="角色">
          <el-select v-model="userEditor.role_ids" multiple filterable style="width: 100%">
            <el-option v-for="role in roles" :key="role.id" :label="role.name" :value="role.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="userEditor.visible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveUser">
          <Save :size="16" />
          保存
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="roleEditor.visible" title="编辑角色" width="560px">
      <el-form label-width="84px">
        <el-form-item label="名称"><el-input v-model="roleEditor.name" /></el-form-item>
        <el-form-item label="说明"><el-input v-model="roleEditor.description" /></el-form-item>
        <el-form-item label="权限">
          <el-select v-model="roleEditor.permission_codes" multiple filterable style="width: 100%">
            <el-option v-for="permission in permissions" :key="permission.code" :label="permission.code" :value="permission.code" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="roleEditor.visible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveRole">
          <Save :size="16" />
          保存
        </el-button>
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.band-heading {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
  font-size: 16px;
  font-weight: 700;
  color: #172033;
}
</style>
