<script setup lang="ts">
import { computed, reactive, watch } from 'vue'

export interface CopilotProposal {
  key: string
  label: string
  value: string | string[]
  display_value: string
  previous_value: string | string[] | null
  previous_display: string
  note?: string
  quote?: string
  target?: 'route' | 'answer' | 'form' | 'admission'
}

const props = defineProps<{
  proposals: CopilotProposal[]
  appliedKeys?: string[]
  undone?: boolean
  busy?: boolean
  disabled?: boolean
}>()

const emit = defineEmits<{
  apply: [items: CopilotProposal[]]
  undo: [items: CopilotProposal[]]
}>()

const checked = reactive(new Set<string>())

watch(
  () => props.proposals,
  (list) => {
    checked.clear()
    for (const p of list || []) checked.add(p.key)
  },
  { immediate: true },
)

const isApplied = computed(() => Boolean(props.appliedKeys?.length) && !props.undone)
const selectedList = computed(() => (props.proposals || []).filter((p) => checked.has(p.key)))
const appliedList = computed(() => {
  const keys = new Set(props.appliedKeys || [])
  return (props.proposals || []).filter((p) => keys.has(p.key))
})

function toggleOne(key: string, val: boolean) {
  if (val) checked.add(key)
  else checked.delete(key)
}

function toggleAll() {
  if (selectedList.value.length === props.proposals.length) checked.clear()
  else for (const p of props.proposals) checked.add(p.key)
}
</script>

<template>
  <div v-if="proposals.length" class="cf-card" :class="{ applied: isApplied }" data-testid="copilot-card">
    <div class="cf-head">
      <template v-if="isApplied">
        <span class="cf-badge ok" data-testid="copilot-applied-badge">已联动填入 {{ appliedList.length }} 项</span>
        <el-button
          link
          type="primary"
          size="small"
          :loading="busy"
          :disabled="disabled"
          data-testid="copilot-undo"
          @click="emit('undo', appliedList)"
        >撤回本次填入</el-button>
      </template>
      <template v-else>
        <b class="cf-title">识别到可填入的填写项（{{ proposals.length }} 项）</b>
        <button
          v-if="proposals.length > 1 && !disabled"
          type="button"
          class="cf-select-all"
          @click="toggleAll"
        >{{ selectedList.length === proposals.length ? '取消全选' : '全选' }}</button>
      </template>
    </div>

    <ul class="cf-list">
      <li v-for="p in proposals" :key="p.key" class="cf-item" :data-testid="`copilot-item-${p.key}`">
        <el-checkbox
          :model-value="isApplied ? Boolean(appliedKeys?.includes(p.key)) : checked.has(p.key)"
          :disabled="disabled || busy || isApplied"
          @change="(v: unknown) => toggleOne(p.key, Boolean(v))"
        />
        <div class="cf-body">
          <div class="cf-line">
            <span class="cf-label">{{ p.label }}</span>
            <span v-if="p.previous_display" class="cf-prev">替换原值：{{ p.previous_display }}</span>
          </div>
          <div class="cf-val" dir="auto">{{ p.display_value }}</div>
          <div v-if="p.note" class="cf-note">{{ p.note }}</div>
        </div>
      </li>
    </ul>

    <div v-if="!isApplied" class="cf-foot">
      <el-button
        type="primary"
        size="small"
        :loading="busy"
        :disabled="disabled || !selectedList.length"
        data-testid="copilot-apply"
        @click="emit('apply', selectedList)"
      >一键填入（{{ selectedList.length }} 项）</el-button>
      <span v-if="undone" class="cf-undone" data-testid="copilot-undone-tip">已撤回，可重新勾选填入</span>
    </div>
  </div>
</template>

<style scoped>
.cf-card {
  margin-top: 8px;
  padding: 10px 12px;
  border-radius: 8px;
  background: #f8fbff;
  border: 1px solid #bfdbfe;
  font-size: 13px;
  line-height: 1.55;
  white-space: normal;
}
.cf-card.applied {
  background: #f3faf7;
  border-color: #a7f3d0;
}
.cf-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.cf-title {
  color: #1e3a8a;
  font-size: 13px;
}
.cf-badge.ok {
  color: #047857;
  font-weight: 600;
}
.cf-select-all {
  border: 0;
  background: transparent;
  color: #2563eb;
  font-size: 13px;
  cursor: pointer;
  padding: 0;
}
.cf-select-all:hover {
  text-decoration: underline;
}
.cf-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.cf-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 6px;
  background: #fff;
  border: 1px solid #e2e8f0;
}
.cf-item :deep(.el-checkbox) {
  height: 20px;
  margin-right: 0;
}
.cf-body {
  flex: 1;
  min-width: 0;
}
.cf-line {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px;
}
.cf-label {
  font-weight: 600;
  color: #0f172a;
}
.cf-prev {
  font-size: 13px;
  color: #b45309;
  background: #fffbeb;
  border-radius: 4px;
  padding: 0 5px;
}
.cf-val {
  color: #1e293b;
  margin-top: 2px;
  word-break: break-word;
}
.cf-note {
  color: #64748b;
  font-size: 13px;
  margin-top: 2px;
}
.cf-foot {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 8px;
}
.cf-undone {
  font-size: 13px;
  color: #64748b;
}
</style>
