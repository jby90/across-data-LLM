<script setup lang="ts">
import { computed, ref } from 'vue'

export interface Block { type: string; text?: string; items?: string[]; head?: string[]; rows?: string[][]; spans?: number[][][]; widths?: number[]; original?: boolean; no_head?: boolean }
interface RichToken { text: string; bold?: boolean; sup?: boolean }

const props = defineProps<{
  blocks: Block[]
  outline?: boolean
  hideTitle?: boolean
  editable?: boolean
  saving?: boolean
  clickableCites?: boolean
}>()
const emit = defineEmits<{
  (e: 'edit', payload: { block: number; text: string; item?: number; row?: number; col?: number }): void
  (e: 'cite', n: number): void
}>()

function citeNums(raw: string): number[] {
  return Array.from(raw.matchAll(/\[(\d+)\]/g), (m) => Number(m[1])).filter((n) => n > 0)
}
const uid = Math.random().toString(36).slice(2, 8)
const activeAnchor = ref('')
const editingTarget = ref<{ key: string; block: number; item?: number; row?: number; col?: number } | null>(null)
const editDraft = ref('')

const rowsOf = (b: Block) =>
  (b.rows || []).map((r, rIdx) => ({
    __rowIdx: rIdx,
    __cells: r,
  }))

function tableSpan(b: Block, p: { rowIndex: number; columnIndex: number }): [number, number] {
  const sp = b.spans?.[p.rowIndex]?.[p.columnIndex]
  if (sp && sp.length === 2) return [sp[0], sp[1]]
  return [1, 1]
}

function startEdit(key: string, currentText: string | undefined, target: { block: number; item?: number; row?: number; col?: number }) {
  if (!props.editable || props.saving) return
  editingTarget.value = { key, ...target }
  editDraft.value = currentText ?? ''
}

function cancelEdit() {
  editingTarget.value = null
  editDraft.value = ''
}

function submitEdit() {
  if (!editingTarget.value || !editDraft.value.trim()) return
  const { block, item, row, col } = editingTarget.value
  emit('edit', { block, text: editDraft.value.trim(), item, row, col })
  editingTarget.value = null
}

const tocItems = computed(() => {
  if (!props.outline) return []
  const items: { index: number; id: string; text: string; sub: boolean }[] = []
  ;(props.blocks || []).forEach((b, i) => {
    if ((b.type === 'heading' || b.type === 'subheading') && (b.text || '').trim()) {
      items.push({ index: i, id: `bv-${uid}-${i}`, text: (b.text || '').trim(), sub: b.type === 'subheading' })
    }
  })
  return items
})

function jumpTo(id: string) {
  activeAnchor.value = id
  const el = document.getElementById(id)
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

const BOLD_RE = /\*\*([^*]+)\*\*/g
const CITE_RE = /((?:\[\d+\])+)/g

function richTokens(raw?: string): RichToken[] {
  const s = raw || ''
  if (!s) return []
  const segs: { text: string; bold: boolean }[] = []
  let last = 0
  for (const m of s.matchAll(BOLD_RE)) {
    if (m.index! > last) segs.push({ text: s.slice(last, m.index), bold: false })
    segs.push({ text: m[1], bold: true })
    last = m.index! + m[0].length
  }
  if (last < s.length) segs.push({ text: s.slice(last), bold: false })

  const out: RichToken[] = []
  let prevChar = ''
  for (const seg of segs) {
    let pos = 0
    for (const cm of seg.text.matchAll(CITE_RE)) {
      if (cm.index! > pos) {
        const chunk = seg.text.slice(pos, cm.index)
        out.push({ text: chunk, bold: seg.bold })
        prevChar = chunk[chunk.length - 1]
      }
      const cite = cm[1]
      const isRef = Boolean(prevChar && !/\s/.test(prevChar) && prevChar !== '[')
      out.push({ text: cite, bold: !isRef && seg.bold, sup: isRef })
      prevChar = cite[cite.length - 1]
      pos = cm.index! + cm[0].length
    }
    if (pos < seg.text.length) {
      const tail = seg.text.slice(pos)
      out.push({ text: tail, bold: seg.bold })
      prevChar = tail[tail.length - 1]
    }
  }
  return out
}

function minColWidth(b: Block, i: number, h: string): number {
  const pct = b.widths?.[i]
  if (pct && pct > 0) return Math.max(54, Math.round(pct * 5))
  const label = (h || '').trim()
  if (label === '编号' || label === '序号' || label === '步骤' || label === '项次') return 54
  if (
    label === '条款' ||
    label === '备注' ||
    label === '状态' ||
    label === '结论' ||
    label === '可能性' ||
    label === '严重程度' ||
    label === '风险等级' ||
    label.includes('确认') ||
    label.includes('核签') ||
    label.includes('结果')
  ) {
    return 76
  }
  if (
    label.includes('内容') ||
    label.includes('摘录') ||
    label.includes('建议') ||
    label.includes('动作') ||
    label.includes('说明') ||
    label.includes('措施') ||
    label.includes('要求') ||
    label.includes('描述')
  ) {
    return 145
  }
  return Array.from(label).length <= 3 ? 76 : 96
}

function isProsePara(raw?: string): boolean {
  const s = (raw || '').replace(/\*\*/g, '').trim()
  if (!s) return false
  const cjk = s.match(/[\u4e00-\u9fff]/g) || []
  if (cjk.length < 6) return false
  if (/^(?:[•·\[]|数据处理流程图|国家互联网信息办公室|个人信息处理者：|地址：|联系人及联系方式：|境外接收方：|所在国家或者地区：|负责人及联系方式：)/.test(s)) return false
  if (s.length <= 45 && /[：:]$/.test(s)) return false
  if (s.includes(' → ') && !s.endsWith('。')) return false
  return true
}
</script>

<template>
  <div :class="['bv-wrap', { 'has-outline': outline && tocItems.length >= 2 }]">
    <aside v-if="outline && tocItems.length >= 2" class="bv-toc" data-testid="blocks-outline">
      <div class="toc-title">目录大纲</div>
      <nav>
        <button
          v-for="item in tocItems"
          :key="item.id"
          type="button"
          :class="['toc-link', { sub: item.sub, active: activeAnchor === item.id }]"
          @click="jumpTo(item.id)"
        >
          {{ item.text }}
        </button>
      </nav>
    </aside>
    <article class="doc">
      <template v-for="(b, i) in blocks" :key="i">
        <template v-if="!(hideTitle && b.type === 'title')">
          <h2 v-if="b.type === 'title'" dir="auto" class="d-title">{{ b.text }}</h2>
          <h3 v-else-if="b.type === 'heading'" :id="`bv-${uid}-${i}`" dir="auto" class="d-heading">{{ b.text }}</h3>
          <h4 v-else-if="b.type === 'subheading'" :id="`bv-${uid}-${i}`" dir="auto" class="d-subheading">{{ b.text }}</h4>
          <details v-else-if="b.type === 'paragraph' && b.original" class="d-orig">
            <summary>原文</summary>
            <p dir="auto">{{ String(b.text || '').replace(/^原文：/, '') }}</p>
          </details>
          <div v-else-if="b.type === 'paragraph' && editable && editingTarget?.key === `p-${i}`" class="bv-inline-edit" data-testid="bv-inline-editor">
            <el-input v-model="editDraft" type="textarea" :autosize="{ minRows: 2, maxRows: 8 }" maxlength="4000" data-testid="bv-inline-input" />
            <div class="bv-inline-acts">
              <el-button size="small" type="primary" :loading="saving" data-testid="bv-inline-save" @click.stop="submitEdit">保存修改</el-button>
              <el-button size="small" @click.stop="cancelEdit">取消</el-button>
            </div>
          </div>
          <p
            v-else-if="b.type === 'paragraph'"
            dir="auto"
            :class="{ 'd-prose': isProsePara(b.text), 'is-editable': editable }"
            :data-testid="editable ? `bv-edit-p-${i}` : undefined"
            @click="startEdit(`p-${i}`, b.text, { block: i })"
          >
            <template v-for="(tk, ti) in richTokens(b.text)" :key="ti">
              <template v-if="tk.sup && clickableCites">
                <sup
                  v-for="cn in citeNums(tk.text)"
                  :key="cn"
                  class="d-sup is-clickable"
                  role="button"
                  tabindex="0"
                  @click.stop="emit('cite', cn)"
                  @keydown.enter.stop="emit('cite', cn)"
                >[{{ cn }}]</sup>
              </template>
              <sup v-else-if="tk.sup" class="d-sup">{{ tk.text }}</sup>
              <strong v-else-if="tk.bold">{{ tk.text }}</strong>
              <template v-else>{{ tk.text }}</template>
            </template>
          </p>
          <ul v-else-if="b.type === 'list'">
            <li v-for="(it, j) in b.items" :key="j" dir="auto">
              <div v-if="editable && editingTarget?.key === `l-${i}-${j}`" class="bv-inline-edit" data-testid="bv-inline-editor">
                <el-input v-model="editDraft" type="textarea" :autosize="{ minRows: 2, maxRows: 6 }" maxlength="4000" data-testid="bv-inline-input" />
                <div class="bv-inline-acts">
                  <el-button size="small" type="primary" :loading="saving" data-testid="bv-inline-save" @click.stop="submitEdit">保存修改</el-button>
                  <el-button size="small" @click.stop="cancelEdit">取消</el-button>
                </div>
              </div>
              <span
                v-else
                :class="{ 'is-editable': editable }"
                :data-testid="editable ? `bv-edit-l-${i}-${j}` : undefined"
                @click="startEdit(`l-${i}-${j}`, it, { block: i, item: j })"
              >
                <template v-for="(tk, ti) in richTokens(it)" :key="ti">
                  <template v-if="tk.sup && clickableCites">
                    <sup
                      v-for="cn in citeNums(tk.text)"
                      :key="cn"
                      class="d-sup is-clickable"
                      role="button"
                      tabindex="0"
                      @click.stop="emit('cite', cn)"
                      @keydown.enter.stop="emit('cite', cn)"
                    >[{{ cn }}]</sup>
                  </template>
                  <sup v-else-if="tk.sup" class="d-sup">{{ tk.text }}</sup>
                  <strong v-else-if="tk.bold">{{ tk.text }}</strong>
                  <template v-else>{{ tk.text }}</template>
                </template>
              </span>
            </li>
          </ul>
          <div v-else-if="b.type === 'table'" class="bv-table-wrap">
            <el-table :data="rowsOf(b)" :span-method="(p: any) => tableSpan(b, p)" :show-header="!b.no_head" size="small" border style="width: 100%">
              <el-table-column v-for="(h, k) in b.head" :key="`${k}-${h}`" :label="h" :min-width="minColWidth(b, k, h)">
                <template #default="{ row }">
                  <div v-if="editable && editingTarget?.key === `t-${i}-${row.__rowIdx}-${k}`" class="bv-inline-edit" data-testid="bv-inline-editor">
                    <el-input v-model="editDraft" type="textarea" :autosize="{ minRows: 2, maxRows: 6 }" maxlength="4000" data-testid="bv-inline-input" />
                    <div class="bv-inline-acts">
                      <el-button size="small" type="primary" :loading="saving" data-testid="bv-inline-save" @click.stop="submitEdit">保存修改</el-button>
                      <el-button size="small" @click.stop="cancelEdit">取消</el-button>
                    </div>
                  </div>
                  <span
                    v-else
                    dir="auto"
                    :class="['cell', { 'is-editable': editable, 'is-todo': /待补充|To be completed|À compléter|يُستكمل/i.test(String(row.__cells[k] || '')) }]"
                    :data-testid="editable ? `bv-edit-t-${i}-${row.__rowIdx}-${k}` : undefined"
                    @click="startEdit(`t-${i}-${row.__rowIdx}-${k}`, row.__cells[k], { block: i, row: row.__rowIdx, col: k })"
                  >
                    <template v-for="(tk, ti) in richTokens(row.__cells[k])" :key="ti">
                      <sup v-if="tk.sup" class="d-sup">{{ tk.text }}</sup>
                      <strong v-else-if="tk.bold">{{ tk.text }}</strong>
                      <template v-else>{{ tk.text }}</template>
                    </template>
                  </span>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </template>
      </template>
    </article>
  </div>
</template>

<style scoped>
.bv-wrap.has-outline { display: grid; grid-template-columns: 220px minmax(0, 1fr); gap: 22px; align-items: start; }
.bv-toc { position: sticky; top: 8px; max-height: calc(100vh - 96px); overflow-y: auto; border: 1px solid #e2e8f0; border-radius: 8px; padding: 10px 10px; background: #f8fafc; }
.toc-title { font-size: 13px; font-weight: 600; color: #334155; padding: 2px 6px 8px; border-bottom: 1px solid #e2e8f0; margin-bottom: 6px; }
.bv-toc nav { display: flex; flex-direction: column; gap: 2px; }
.toc-link { border: none; background: transparent; text-align: left; font: inherit; font-size: 13px; line-height: 1.45; color: #475569; padding: 5px 6px; border-radius: 5px; cursor: pointer; }
.toc-link.sub { padding-left: 16px; font-size: 13px; color: #64748b; }
.toc-link:hover, .toc-link.active { background: #eff6ff; color: #1d4ed8; }
@media (max-width: 820px) { .bv-wrap.has-outline { grid-template-columns: 1fr; } .bv-toc { position: static; max-height: 180px; } }
.doc { line-height: 1.9; min-width: 0; width: 100%; }
.d-title { font-size: 18px; margin: 18px 0 10px; }
.d-heading { font-size: 16px; margin: 24px 0 8px; scroll-margin-top: 24px; }
.d-subheading { font-size: 15px; margin: 16px 0 6px; color: #0f172a; scroll-margin-top: 24px; }
.doc p { margin: 8px 0; text-align: justify; }
.doc p.d-prose { text-indent: 2em; }
.doc :is(h2, h3, h4, p, li, .cell):dir(rtl) { font-family: 'Noto Naskh Arabic', var(--cy-font); text-align: right; text-indent: 0; }
.doc ul { margin: 6px 0 10px; padding-left: 22px; }
.doc li { margin: 4px 0; }
.bv-table-wrap { width: 100%; max-width: 100%; overflow-x: auto; margin: 10px 0 14px; }
.doc :deep(.el-table) { --el-table-border-color: #1e293b; --el-table-header-bg-color: #ffffff; --el-table-header-text-color: #000000; --el-table-text-color: #000000; width: 100% !important; margin: 0; border: 1px solid #1e293b; }
.doc :deep(.el-table th.el-table__cell) { font-weight: 600; color: #000000; background: #ffffff; border-bottom: 1.5px solid #1e293b; height: auto; padding: 8px 0; }
.doc :deep(.el-table th.el-table__cell > .cell) { white-space: normal; word-break: keep-all; overflow-wrap: break-word; line-height: 1.45; padding: 0 8px; }
.doc :deep(.el-table--small td.el-table__cell > .cell) { padding: 0 8px; }
.d-sup { font-size: 13px; color: #0f172a; font-weight: 600; margin-left: 1px; line-height: 0; }
.d-sup.is-clickable { cursor: pointer; padding: 0 1px; border-radius: 3px; }
.d-sup.is-clickable:hover { background: #dbeafe; text-decoration: underline; }
.d-orig { margin: 0 0 10px; font-size: 13px; color: #64748b; }
.d-orig summary { cursor: pointer; }
.cell { display: block; white-space: pre-wrap; word-break: break-word; line-height: 1.7; min-height: 22px; }
.cell.is-todo { background: #fff6e5; color: #9a5b00; padding: 2px 6px; border-radius: 4px; }
.is-editable { cursor: pointer; border-radius: 4px; transition: background 0.15s, box-shadow 0.15s; }
.is-editable:hover { background: #eff6ff; box-shadow: 0 0 0 2px #dbeafe; }
.bv-inline-edit { margin: 4px 0; padding: 8px; background: #f8fafc; border: 1px solid #93c5fd; border-radius: 6px; }
.bv-inline-acts { display: flex; gap: 8px; margin-top: 6px; justify-content: flex-end; }
</style>
