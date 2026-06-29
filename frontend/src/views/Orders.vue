<script setup lang="ts">
import { ref } from "vue";
import { apiRequest } from "../services/api";

const sample = JSON.stringify(
  {
    orders: [
      {
        order_id: "O001",
        product_name: "彩盒彩盒 A",
        category: "box",
        is_repeat_order: true,
        quote_amount: 6800,
        contacted: true,
        quantity: 1200,
        material: "white_card",
        thickness: "350gsm"
      }
    ]
  },
  null,
  2
);
const payload = ref(sample);
const rows = ref<Array<Record<string, unknown>>>([]);
const error = ref("");
const fileImportResult = ref<Record<string, unknown> | null>(null);

async function importOrders() {
  error.value = "";
  try {
    const result = await apiRequest<{ orders: Array<Record<string, unknown>> }>("/orders/import", {
      method: "POST",
      body: payload.value
    });
    rows.value = result.orders;
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  }
}

async function importOrderFile(file: File) {
  error.value = "";
  const form = new FormData();
  form.append("file", file);
  try {
    const result = await apiRequest<{ orders: Array<Record<string, unknown>> }>("/orders/import-file", {
      method: "POST",
      body: form
    });
    fileImportResult.value = result as unknown as Record<string, unknown>;
    rows.value = result.orders;
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  }
  return false;
}
</script>

<template>
  <section>
    <div class="page-header">
      <div>
        <h1 class="page-title">订单池</h1>
        <div class="page-subtitle">Excel/CSV Adapter 对齐的订单字段</div>
      </div>
      <el-button type="primary" @click="importOrders">导入</el-button>
    </div>
    <div class="two-column">
      <div class="work-band">
        <el-upload :auto-upload="false" :before-upload="importOrderFile" accept=".csv,.xlsx,.xlsm">
          <el-button>上传 CSV/XLSX</el-button>
        </el-upload>
        <el-input v-model="payload" type="textarea" :rows="18" />
        <el-alert v-if="error" :title="error" type="error" :closable="false" style="margin-top: 12px" />
      </div>
      <div class="work-band">
        <pre v-if="fileImportResult" class="pre-wrap">{{ fileImportResult }}</pre>
        <el-table :data="rows" border>
          <el-table-column prop="order_id" label="订单号" />
          <el-table-column prop="product_name" label="产品" />
          <el-table-column prop="material" label="材料" />
          <el-table-column prop="thickness" label="厚度" />
          <el-table-column prop="priority_score" label="评分" />
        </el-table>
      </div>
    </div>
  </section>
</template>
