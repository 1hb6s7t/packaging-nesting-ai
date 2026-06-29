<script setup lang="ts">
import { reactive, ref } from "vue";
import { apiRequest } from "../services/api";

const form = reactive({
  sheet_id: "sheet_889_1194",
  width: 889,
  height: 1194,
  margin_top: 10,
  margin_right: 10,
  margin_bottom: 10,
  margin_left: 10,
  gripper_mm: 12,
  material: "white_card",
  thickness: "350gsm",
  cost_per_sheet: 4.2
});
const rows = ref<Array<Record<string, unknown>>>([]);

async function save() {
  await apiRequest("/sheets", { method: "POST", body: JSON.stringify(form) });
  rows.value = await apiRequest("/sheets");
}
</script>

<template>
  <section>
    <div class="page-header">
      <div>
        <h1 class="page-title">纸张规格</h1>
        <div class="page-subtitle">可印刷区域、咬口、材料、成本</div>
      </div>
      <el-button type="primary" @click="save">保存</el-button>
    </div>
    <div class="two-column">
      <div class="work-band">
        <el-form label-width="110px">
          <el-form-item label="纸张 ID"><el-input v-model="form.sheet_id" /></el-form-item>
          <el-form-item label="宽 / 高">
            <el-input-number v-model="form.width" :min="1" />
            <el-input-number v-model="form.height" :min="1" style="margin-left: 8px" />
          </el-form-item>
          <el-form-item label="边距">
            <el-input-number v-model="form.margin_top" :min="0" />
            <el-input-number v-model="form.margin_right" :min="0" style="margin-left: 8px" />
            <el-input-number v-model="form.margin_bottom" :min="0" style="margin-left: 8px" />
            <el-input-number v-model="form.margin_left" :min="0" style="margin-left: 8px" />
          </el-form-item>
          <el-form-item label="咬口"><el-input-number v-model="form.gripper_mm" :min="0" /></el-form-item>
          <el-form-item label="材料"><el-input v-model="form.material" /></el-form-item>
          <el-form-item label="厚度"><el-input v-model="form.thickness" /></el-form-item>
          <el-form-item label="单张成本"><el-input-number v-model="form.cost_per_sheet" :min="0" /></el-form-item>
        </el-form>
      </div>
      <div class="work-band">
        <el-table :data="rows" border>
          <el-table-column prop="sheet_id" label="ID" />
          <el-table-column prop="width" label="宽" />
          <el-table-column prop="height" label="高" />
          <el-table-column prop="material" label="材料" />
        </el-table>
      </div>
    </div>
  </section>
</template>

