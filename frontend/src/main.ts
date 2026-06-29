import { createApp } from "vue";
import { createPinia } from "pinia";
import {
  ElAlert,
  ElButton,
  ElCheckbox,
  ElDescriptions,
  ElDescriptionsItem,
  ElDialog,
  ElDivider,
  ElEmpty,
  ElForm,
  ElFormItem,
  ElInput,
  ElInputNumber,
  ElLoading,
  ElOption,
  ElProgress,
  ElSelect,
  ElStep,
  ElSteps,
  ElSwitch,
  ElTable,
  ElTableColumn,
  ElTabPane,
  ElTabs,
  ElTag,
  ElTooltip,
  ElUpload,
} from "element-plus";
import "element-plus/dist/index.css";
import "./styles.css";
import App from "./App.vue";
import router from "./router";

const elementComponents = [
  ElAlert,
  ElButton,
  ElCheckbox,
  ElDescriptions,
  ElDescriptionsItem,
  ElDialog,
  ElDivider,
  ElEmpty,
  ElForm,
  ElFormItem,
  ElInput,
  ElInputNumber,
  ElOption,
  ElProgress,
  ElSelect,
  ElStep,
  ElSteps,
  ElSwitch,
  ElTable,
  ElTableColumn,
  ElTabPane,
  ElTabs,
  ElTag,
  ElTooltip,
  ElUpload,
];

const app = createApp(App);
for (const component of elementComponents) {
  if (component.name) {
    app.component(component.name, component);
  }
}
app.use(createPinia()).use(router).use(ElLoading).mount("#app");
