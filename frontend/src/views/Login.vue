<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { apiRequest } from "../services/api";
import { type CurrentUser, useAppStore } from "../stores/app";

interface AuthToken {
  access_token: string;
  token_type: string;
  expires_in: number;
}

const appStore = useAppStore();
const route = useRoute();
const router = useRouter();
const form = reactive({ email: "", password: "" });
const loading = ref(false);
const error = ref("");
const isLoggedIn = computed(() => appStore.isAuthenticated);

async function refreshCurrentUser() {
  if (!appStore.authToken) {
    return;
  }
  try {
    const user = await apiRequest<CurrentUser>("/auth/me");
    appStore.setCurrentUser(user);
  } catch {
    appStore.clearAuth();
  }
}

async function login() {
  loading.value = true;
  error.value = "";
  try {
    const token = await apiRequest<AuthToken>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email: form.email, password: form.password })
    });
    appStore.setToken(token.access_token);
    const user = await apiRequest<CurrentUser>("/auth/me");
    appStore.setAuth(token.access_token, user);
    const requestedRedirect = typeof route.query.redirect === "string" ? route.query.redirect : "/";
    const redirect = requestedRedirect.startsWith("/") && !requestedRedirect.startsWith("//") ? requestedRedirect : "/";
    router.replace(redirect);
  } catch (err) {
    appStore.clearAuth();
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    loading.value = false;
  }
}

function logout() {
  appStore.clearAuth();
}

onMounted(refreshCurrentUser);
</script>

<template>
  <section>
    <div class="page-header">
      <div>
        <h1 class="page-title">登录</h1>
        <div class="page-subtitle">企业账号认证</div>
      </div>
    </div>
    <div class="two-column">
      <div class="work-band" style="max-width: 460px">
        <el-form label-width="72px" @submit.prevent>
          <el-form-item label="邮箱"><el-input v-model="form.email" autocomplete="username" /></el-form-item>
          <el-form-item label="密码">
            <el-input v-model="form.password" type="password" show-password autocomplete="current-password" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loading" @click="login">登录</el-button>
            <el-button v-if="isLoggedIn" @click="logout">退出</el-button>
          </el-form-item>
        </el-form>
        <el-alert v-if="error" :title="error" type="error" :closable="false" style="margin-top: 12px" />
      </div>
      <div class="work-band">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="状态">{{ isLoggedIn ? "已登录" : "未登录" }}</el-descriptions-item>
          <el-descriptions-item label="账号">{{ appStore.currentUser?.email || "-" }}</el-descriptions-item>
          <el-descriptions-item label="姓名">{{ appStore.currentUser?.display_name || "-" }}</el-descriptions-item>
          <el-descriptions-item label="角色">
            <el-tag v-for="role in appStore.currentUser?.roles || []" :key="role" style="margin-right: 6px">
              {{ role }}
            </el-tag>
            <span v-if="!appStore.currentUser?.roles.length">-</span>
          </el-descriptions-item>
          <el-descriptions-item label="权限">
            <el-tag
              v-for="permission in appStore.currentUser?.permissions || []"
              :key="permission"
              type="info"
              style="margin: 0 6px 6px 0"
            >
              {{ permission }}
            </el-tag>
            <span v-if="!appStore.currentUser?.permissions.length">-</span>
          </el-descriptions-item>
        </el-descriptions>
      </div>
    </div>
  </section>
</template>
