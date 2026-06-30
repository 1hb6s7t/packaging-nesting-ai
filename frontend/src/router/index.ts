import { createRouter, createWebHistory, type RouteMeta, type RouteRecordRaw } from "vue-router";
import { useAppStore } from "../stores/app";

const Dashboard = () => import("../views/Dashboard.vue");
const Orders = () => import("../views/Orders.vue");
const Artworks = () => import("../views/Artworks.vue");
const ArtworkPreflight = () => import("../views/ArtworkPreflight.vue");
const BatchWorkbench = () => import("../views/BatchWorkbench.vue");
const Sheets = () => import("../views/Sheets.vue");
const NestingJobs = () => import("../views/NestingJobs.vue");
const RunMonitor = () => import("../views/RunMonitor.vue");
const Solutions = () => import("../views/Solutions.vue");
const SolutionCompare = () => import("../views/SolutionCompare.vue");
const Reports = () => import("../views/Reports.vue");
const AiAssistant = () => import("../views/AiAssistant.vue");
const Rules = () => import("../views/Rules.vue");
const SolverConfig = () => import("../views/SolverConfig.vue");
const Benchmark = () => import("../views/Benchmark.vue");
const ConversionLogs = () => import("../views/ConversionLogs.vue");
const Integrations = () => import("../views/Integrations.vue");
const OperationLogs = () => import("../views/OperationLogs.vue");
const Notifications = () => import("../views/Notifications.vue");
const Permissions = () => import("../views/Permissions.vue");
const Login = () => import("../views/Login.vue");

declare module "vue-router" {
  interface RouteMeta {
    label: string;
    public?: boolean;
    permissions?: string[];
    anyPermissions?: string[];
  }
}

export type RouteAccessState = {
  isAuthenticated: boolean;
  permissions: string[];
};

export function canAccessRoute(meta: RouteMeta, access: RouteAccessState): boolean {
  if (meta.public) {
    return true;
  }
  if (!access.isAuthenticated) {
    return false;
  }
  const permissionSet = new Set(access.permissions);
  if (permissionSet.has("*")) {
    return true;
  }
  if (meta.permissions?.some((permission) => !permissionSet.has(permission))) {
    return false;
  }
  if (meta.anyPermissions?.length && !meta.anyPermissions.some((permission) => permissionSet.has(permission))) {
    return false;
  }
  return true;
}

export const routes = [
  { path: "/", name: "dashboard", component: Dashboard, meta: { label: "Dashboard", public: true } },
  { path: "/login", name: "login", component: Login, meta: { label: "登录", public: true } },
  { path: "/orders", name: "orders", component: Orders, meta: { label: "订单池", permissions: ["orders:write"] } },
  { path: "/artworks", name: "artworks", component: Artworks, meta: { label: "版图上传", permissions: ["artworks:write"] } },
  { path: "/artworks/preflight", name: "preflight", component: ArtworkPreflight, meta: { label: "版图预检", public: true } },
  { path: "/batch", name: "batch-workbench", component: BatchWorkbench, meta: { label: "批量工作台", permissions: ["batch:write"] } },
  { path: "/sheets", name: "sheets", component: Sheets, meta: { label: "纸张规格", permissions: ["sheets:write"] } },
  { path: "/nesting/jobs", name: "nesting-jobs", component: NestingJobs, meta: { label: "拼版任务", permissions: ["nesting:write"] } },
  { path: "/nesting/monitor", name: "run-monitor", component: RunMonitor, meta: { label: "运行监控", permissions: ["audit:read"] } },
  {
    path: "/solutions",
    name: "solutions",
    component: Solutions,
    meta: {
      label: "方案列表",
      anyPermissions: ["solutions:write", "solutions:approve", "solutions:export", "solutions:archive"]
    }
  },
  {
    path: "/solutions/compare",
    name: "compare",
    component: SolutionCompare,
    meta: { label: "方案对比", anyPermissions: ["solutions:write", "solutions:approve", "solutions:export"] }
  },
  { path: "/reports", name: "reports", component: Reports, meta: { label: "成本报告", anyPermissions: ["audit:read", "solutions:write"] } },
  { path: "/ai", name: "ai", component: AiAssistant, meta: { label: "AI 助手", permissions: ["ai:use"] } },
  { path: "/rules", name: "rules", component: Rules, meta: { label: "规则配置", permissions: ["rules:manage"] } },
  { path: "/solvers", name: "solvers", component: SolverConfig, meta: { label: "Solver 配置", permissions: ["solvers:manage"] } },
  { path: "/benchmark", name: "benchmark", component: Benchmark, meta: { label: "基准测试", permissions: ["benchmark:write"] } },
  { path: "/conversion-logs", name: "conversion-logs", component: ConversionLogs, meta: { label: "转换日志", permissions: ["artworks:write"] } },
  { path: "/integrations", name: "integrations", component: Integrations, meta: { label: "系统集成", permissions: ["integrations:write"] } },
  { path: "/operation-logs", name: "operation-logs", component: OperationLogs, meta: { label: "操作日志", permissions: ["audit:read"] } },
  { path: "/notifications", name: "notifications", component: Notifications, meta: { label: "通知中心", anyPermissions: ["notifications:manage", "audit:read"] } },
  { path: "/permissions", name: "permissions", component: Permissions, meta: { label: "权限管理", permissions: ["rbac:manage"] } }
] satisfies RouteRecordRaw[];

const router = createRouter({
  history: createWebHistory(),
  routes
});

router.beforeEach((to) => {
  const appStore = useAppStore();
  const access = {
    isAuthenticated: appStore.isAuthenticated,
    permissions: appStore.currentUser?.permissions || []
  };
  if (canAccessRoute(to.meta, access)) {
    return true;
  }
  if (!appStore.isAuthenticated) {
    return { name: "login", query: { redirect: to.fullPath } };
  }
  return { name: "dashboard", query: { forbidden: String(to.name || to.path) } };
});

export default router;
