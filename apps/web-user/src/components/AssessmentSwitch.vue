<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()
const tabs = [
  { path: '/documents', label: '生成合规文件', hint: '选好出入境国家和业务，按出境路径筛选并生成所需合规文件' },
  { path: '/analysis', label: '咨询助手', hint: '问法规、问怎么做、上传现有制度文件或《隐私政策》对照' },
]
</script>

<template>
  <h1 class="assess-h1">合规评估</h1>
  <nav class="assess-switch" aria-label="合规评估">
    <button
      v-for="t in tabs"
      :key="t.path"
      type="button"
      :class="{ on: route.path === t.path }"
      :title="t.hint"
      :data-testid="`switch-${t.path.slice(1)}`"
      @click="router.push(t.path)"
    >{{ t.label }}</button>
  </nav>
</template>

<style scoped>
/* 第 7.5 项二轮 规则 12：页面级切换统一用下划线标签页 */
.assess-h1 { margin: 0 0 12px; font-size: 24px; }
.assess-switch { display: flex; gap: 24px; border-bottom: 1px solid var(--cy-line); margin-bottom: 16px; }
.assess-switch button { border: 0; border-bottom: 3px solid transparent; background: transparent; padding: 10px 2px; margin-bottom: -1px; font-size: 15px; color: var(--cy-text-2); cursor: pointer; }
.assess-switch button.on { color: var(--el-color-primary); border-bottom-color: var(--el-color-primary); font-weight: 600; }
</style>
