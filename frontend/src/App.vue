<script setup lang="ts">
import {
  Bell,
  BarChart3,
  Bot,
  Boxes,
  ClipboardList,
  FileClock,
  FileInput,
  Gauge,
  GitCompare,
  KeyRound,
  LayoutDashboard,
  ListChecks,
  LogIn,
  MonitorCog,
  PackageCheck,
  PanelsTopLeft,
  ScrollText,
  Settings2,
  ShieldCheck,
  TableProperties,
  Upload,
} from "@lucide/vue";
import { computed, onBeforeUnmount, onMounted } from "vue";
import { RouterLink, RouterView, useRoute, useRouter } from "vue-router";
import { canAccessRoute, routes } from "./router";
import { apiRequest, AUTH_FAILURE_EVENT } from "./services/api";
import { type CurrentUser, useAppStore } from "./stores/app";

const route = useRoute();
const router = useRouter();
const appStore = useAppStore();
const routeByPath = new Map(routes.map((routeRecord) => [routeRecord.path, routeRecord]));

const nav = [
  { path: "/", label: "Dashboard", icon: LayoutDashboard },
  { path: "/orders", label: "订单池", icon: ClipboardList },
  { path: "/artworks", label: "版图上传", icon: Upload },
  { path: "/artworks/preflight", label: "版图预检", icon: FileInput },
  { path: "/sheets", label: "纸张规格", icon: TableProperties },
  { path: "/nesting/jobs", label: "拼版任务", icon: Boxes },
  { path: "/nesting/monitor", label: "运行监控", icon: Gauge },
  { path: "/solutions", label: "方案列表", icon: PanelsTopLeft },
  { path: "/solutions/compare", label: "方案对比", icon: GitCompare },
  { path: "/reports", label: "成本报告", icon: ScrollText },
  { path: "/ai", label: "AI 助手", icon: Bot },
  { path: "/rules", label: "规则配置", icon: ListChecks },
  { path: "/solvers", label: "Solver 配置", icon: Settings2 },
  { path: "/benchmark", label: "基准测试", icon: BarChart3 },
  { path: "/conversion-logs", label: "转换日志", icon: FileClock },
  { path: "/integrations", label: "系统集成", icon: MonitorCog },
  { path: "/operation-logs", label: "操作日志", icon: PackageCheck },
  { path: "/notifications", label: "通知中心", icon: Bell },
  { path: "/permissions", label: "权限管理", icon: ShieldCheck },
  { path: "/login", label: "登录", icon: LogIn },
];

const visibleNav = computed(() => {
  const access = {
    isAuthenticated: appStore.isAuthenticated,
    permissions: appStore.currentUser?.permissions || []
  };
  return nav.filter((item) => {
    if (item.path === "/login") {
      return !appStore.isAuthenticated;
    }
    const routeRecord = routeByPath.get(item.path);
    return routeRecord ? canAccessRoute(routeRecord.meta, access) : false;
  });
});

async function refreshSession() {
  if (!appStore.authToken) {
    return;
  }
  try {
    appStore.setCurrentUser(await apiRequest<CurrentUser>("/auth/me"));
  } catch {
    appStore.clearAuth();
  }
}

function logout() {
  appStore.clearAuth();
  router.push("/login");
}

function handleAuthFailure() {
  const redirect = route.name === "login" ? "/" : route.fullPath;
  appStore.clearAuth();
  if (route.name !== "login") {
    router.replace({ name: "login", query: { redirect } });
  }
}

onMounted(() => {
  window.addEventListener(AUTH_FAILURE_EVENT, handleAuthFailure);
  refreshSession();
});
onBeforeUnmount(() => window.removeEventListener(AUTH_FAILURE_EVENT, handleAuthFailure));
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">PN</div>
        <div>
          <div class="brand-name">包装拼版优化</div>
          <div class="brand-subtitle">Enterprise Nesting</div>
        </div>
      </div>
      <nav class="nav-list">
        <RouterLink
          v-for="item in visibleNav"
          :key="item.path"
          :to="item.path"
          class="nav-item"
          :class="{ active: route.path === item.path }"
        >
          <component :is="item.icon" :size="18" />
          <span>{{ item.label }}</span>
        </RouterLink>
      </nav>
      <div class="auth-panel">
        <div class="auth-title">
          <KeyRound :size="16" />
          <span>{{ appStore.isAuthenticated ? "已登录" : "未登录" }}</span>
        </div>
        <div class="auth-email">{{ appStore.currentUser?.email || "需要登录" }}</div>
        <RouterLink v-if="!appStore.isAuthenticated" to="/login" class="auth-link">登录</RouterLink>
        <el-button v-else size="small" @click="logout">退出</el-button>
      </div>
    </aside>
    <main class="main-pane">
      <RouterView />
    </main>
  </div>
</template>
