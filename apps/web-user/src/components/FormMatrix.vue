<script setup lang="ts">
import { computed } from 'vue'

interface Opt { id: string; label: string }
interface Row { id: string; label_zh: string; label_fr: string; label_original?: string; group_zh: string; free_label: boolean; limits?: Record<string, number> }
export interface Meta { rows: Row[]; persons: Opt[]; origin: Opt[]; recipients: Opt[]; access?: Opt[]; purpose?: boolean }
export interface RowValue {
  on: boolean; persons: string[]; persons_other: string; origin: string[]; origin_other: string
  recipients: string[]; recipients_other: string; purpose: string; duration: string; label_other: string
  access?: string[]; access_other?: string
}
const props = defineProps<{ meta: Meta; modelValue: Record<string, RowValue>; disabled?: boolean; language?: string }>()
const emit = defineEmits<{ 'update:modelValue': [v: Record<string, RowValue>] }>()

const NOISE_LABEL_RE = /^[.\s…_·\-—_/\\{}｛｝()（）[\]【】☐☑☒:;]+$/
function cleanOrigLabel(r: Row): string {
  const t = (r.label_original ?? r.label_fr ?? '')
    .replace(/[{}｛｝☐☑☒]/g, '')
    .replace(/^[.\s…_·\-—_/\\]+|[.\s…_·\-—_/\\]+$/g, '')
    .trim()
  if (!t || NOISE_LABEL_RE.test(t) || !/[A-Za-z0-9\u00C0-\u024F\u4e00-\u9fff\u0600-\u06ff]/.test(t)) return ''
  return t
}

const effectiveLang = computed(() => {
  const lang = (props.language || '').trim().toLowerCase()
  if (lang) return lang
  const sample = props.meta.rows.map((r) => r.label_original || r.label_fr || '').join(' ')
  if (/[\u0600-\u06FF]/.test(sample)) return 'ar'
  if (/[àâçéèêëîïôûùüÿœæ]/i.test(sample) || /\b(données|personnes|durée|finalité|jours)\b/i.test(sample)) return 'fr'
  if (/[A-Za-z]/.test(sample)) return 'en'
  return 'zh'
})

const purposePlaceholder = computed(() => {
  switch (effectiveLang.value) {
    case 'en': return '建议用英文填写处理目的（如 Remote diagnostics and OTA update）'
    case 'zh': return '请填写处理目的（如远程故障诊断与软件升级）'
    case 'ar': return '建议用阿拉伯语或法语填写处理目的'
    case 'fr': return '建议用法语填写处理目的'
    default: return '请按原表要求语种填写处理目的'
  }
})

const durationPlaceholder = computed(() => {
  switch (effectiveLang.value) {
    case 'en': return '如 180 days / 2 years'
    case 'zh': return '如 180 天 / 2 年'
    case 'ar': return '如 180 يوماً / 180 jours'
    case 'fr': return '如 180 jours / 2 ans'
    default: return '请按原表语种填写保存期限'
  }
})

const groups = computed(() => {
  const out: Array<{ title: string; rows: Row[] }> = []
  for (const r of props.meta.rows) {
    const g = out[out.length - 1]
    if (g && g.title === r.group_zh) g.rows.push(r)
    else out.push({ title: r.group_zh, rows: [r] })
  }
  return out
})
const empty = (): RowValue => ({ on: true, persons: [], persons_other: '', origin: [], origin_other: '', recipients: [], recipients_other: '', purpose: '', duration: '', label_other: '', access: [], access_other: '' })
const chosen = computed(() => props.meta.rows.filter((r) => props.modelValue[r.id]?.on))

function toggle(r: Row, on: boolean) {
  if (props.disabled) return
  const next = { ...props.modelValue }
  if (!on) { delete next[r.id]; emit('update:modelValue', next); return }
  // 新勾选一类时，沿用上一个已选类别的设置：同一场景里各类数据的人群、接收方、目的、期限通常一样
  const prev = chosen.value[chosen.value.length - 1]
  const base = prev ? { ...props.modelValue[prev.id], label_other: '' } : empty()
  next[r.id] = { ...empty(), ...base, on: true, persons: [...(base.persons || [])], origin: [...(base.origin || [])], recipients: [...(base.recipients || [])], access: [...(base.access || [])] }
  emit('update:modelValue', next)
}
function patch(r: Row, part: Partial<RowValue>) {
  if (props.disabled) return
  emit('update:modelValue', { ...props.modelValue, [r.id]: { ...props.modelValue[r.id], ...part } })
}
</script>

<template>
  <div class="matrix" data-testid="form-matrix">
    <p v-if="!disabled" class="tip">先勾选涉及的数据类别，再为每一类说明涉及的人群、数据来源、接收方{{ meta.access?.length ? '、内部可访问的部门' : '' }}{{ meta.purpose !== false ? '、处理目的' : '' }}和保存期限。新勾选的类别会沿用上一类的设置，只需改不同的地方。</p>
    <div v-for="g in groups" :key="g.title" class="group">
      <h4>{{ g.title }}</h4>
      <div v-for="r in g.rows" :key="r.id" class="row" :class="{ on: modelValue[r.id]?.on }">
        <el-checkbox :model-value="!!modelValue[r.id]?.on" :disabled="disabled" :data-testid="`matrix-row-${r.id}`" @update:model-value="(v: boolean) => toggle(r, v)">
          {{ r.label_zh }}<small v-if="cleanOrigLabel(r)">{{ cleanOrigLabel(r) }}</small>
        </el-checkbox>
        <div v-if="modelValue[r.id]?.on" class="panel">
          <div v-if="r.free_label" class="line"><span>类别名称</span><el-input size="small" :disabled="disabled" :model-value="modelValue[r.id].label_other" :maxlength="r.limits?.label_other ?? 40" @update:model-value="(v: string) => patch(r, { label_other: v })" /></div>
          <div class="line"><span>涉及人群 *</span>
            <el-checkbox-group :disabled="disabled" :model-value="modelValue[r.id].persons" @update:model-value="(v: string[]) => patch(r, { persons: v })">
              <el-checkbox v-for="o in meta.persons" :key="o.id" :value="o.id">{{ o.label }}</el-checkbox>
            </el-checkbox-group>
            <el-input v-if="modelValue[r.id].persons.includes('autres')" size="small" :disabled="disabled" placeholder="其他人群（如路人）" :model-value="modelValue[r.id].persons_other" :maxlength="r.limits?.persons_other ?? 40" @update:model-value="(v: string) => patch(r, { persons_other: v })" />
          </div>
          <div class="line"><span>数据来源</span>
            <el-checkbox-group :disabled="disabled" :model-value="modelValue[r.id].origin" @update:model-value="(v: string[]) => patch(r, { origin: v })">
              <el-checkbox v-for="o in meta.origin" :key="o.id" :value="o.id">{{ o.label }}</el-checkbox>
            </el-checkbox-group>
            <el-input v-if="modelValue[r.id].origin.includes('autre')" size="small" :disabled="disabled" placeholder="其他来源（如车载传感器）" :model-value="modelValue[r.id].origin_other" :maxlength="r.limits?.origin_other ?? 40" @update:model-value="(v: string) => patch(r, { origin_other: v })" />
          </div>
          <div class="line"><span>接收方</span>
            <el-checkbox-group :disabled="disabled" :model-value="modelValue[r.id].recipients" @update:model-value="(v: string[]) => patch(r, { recipients: v })">
              <el-checkbox v-for="o in meta.recipients" :key="o.id" :value="o.id">{{ o.label }}</el-checkbox>
            </el-checkbox-group>
            <el-input v-if="modelValue[r.id].recipients.includes('autres')" size="small" :disabled="disabled" placeholder="其他接收方（如境外总部）" :model-value="modelValue[r.id].recipients_other" :maxlength="r.limits?.recipients_other ?? 80" @update:model-value="(v: string) => patch(r, { recipients_other: v })" />
          </div>
          <div v-if="meta.access?.length" class="line"><span>可访问部门</span>
            <el-checkbox-group :disabled="disabled" :model-value="modelValue[r.id].access || []" @update:model-value="(v: string[]) => patch(r, { access: v })">
              <el-checkbox v-for="o in meta.access" :key="o.id" :value="o.id">{{ o.label }}</el-checkbox>
            </el-checkbox-group>
            <el-input v-if="(modelValue[r.id].access || []).includes('autre')" size="small" :disabled="disabled" placeholder="其他部门" :model-value="modelValue[r.id].access_other" :maxlength="r.limits?.access_other ?? 40" @update:model-value="(v: string) => patch(r, { access_other: v })" />
          </div>
          <div v-if="meta.purpose !== false" class="line"><span>处理目的 *</span><el-input size="small" :disabled="disabled" :placeholder="purposePlaceholder" :model-value="modelValue[r.id].purpose" :maxlength="r.limits?.purpose ?? 220" show-word-limit @update:model-value="(v: string) => patch(r, { purpose: v })" /></div>
          <div class="line"><span>保存期限</span><el-input size="small" :disabled="disabled" :placeholder="durationPlaceholder" :model-value="modelValue[r.id].duration" :maxlength="r.limits?.duration ?? 30" @update:model-value="(v: string) => patch(r, { duration: v })" /></div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tip { color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.7; margin: 0 0 10px; }
.group h4 { margin: 14px 0 6px; font-size: 14px; }
.row { padding: 6px 10px; border: 1px solid transparent; border-radius: 8px; }
.row.on { border-color: var(--el-color-primary-light-7); background: #f7faff; margin: 4px 0; }
.row small { color: var(--el-text-color-secondary); margin-left: 8px; font-size: 13px; }
.panel { margin: 8px 0 4px 24px; display: flex; flex-direction: column; gap: 8px; }
.line { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.line > span { width: 72px; color: var(--el-text-color-regular); font-size: 13px; flex: none; }
.line .el-input { max-width: 420px; }
</style>
