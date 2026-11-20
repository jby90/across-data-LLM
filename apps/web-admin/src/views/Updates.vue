<script setup lang="ts">
import { COUNTRY_NAMES, localTime, serverFilename } from '@cyshuhe/shared'
import { ElMessage } from 'element-plus'
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'

interface Discovery { id: string; country_code: string; title: string; source_url: string; discovered_at: string; first_parsed_at: string | null; history_unknown: boolean; overdue: boolean; elapsed_hours: number | null }
interface Change { id: string; document_title: string; country_code: string; source_url: string; detected_at: string; kind: string; summary: string; affected_dims?: string[]; changes: Array<{ kind: string; clause_number?: string; section_path?: string }>; graph_edge_ids: string[]; content_hashes: Record<string,string> }
interface Sla { pending_updates: number; overdue_72h: number; resolved_updates: number; late_notification_records: number; intake: { pending: number; completed: number; overdue_30d: number; history_unknown: number; untracked_documents: number; definition: string }; countries: string[] }
interface SlaReport {
  response_72h: { total_impacts: number; pending_updates: number; resolved_updates: number; overdue_72h: number; on_time_rate_pct: number; has_triggers?: boolean }
  patrol_timeliness: { active_sites: number; healthy_sites: number; failed_sites: number; on_schedule_rate_pct: number }
  intake_30d: { tracked: number; completed: number; pending: number; overdue_30d: number; on_time_rate_pct: number; in_library_documents_total?: number; has_triggers?: boolean }
  linkage: { countries_covered: number; whitepapers_published: number; admission_entries_total: number; enterprise_notices_pushed: number }
}
const router = useRouter()
const sla = ref<Sla | null>(null), report = ref<SlaReport | null>(null), changes = ref<Change[]>([]), discoveries = ref<Discovery[]>([])
const busy = ref(false), exporting = ref(false), error = ref(''), country = ref(''), page = ref(1), total = ref(0)
const changePage = ref(1), changeTotal = ref(0)
const kindLabel: Record<string,string> = { bytes_only: '原文版式更新（条文未变）', text_change_pending_review: '条文修订待确认', source_integrity_pending: '来源文本核对中', parsing_pending: '条文拆分处理中' }
const clauseKindLabel: Record<string, string> = {
  added: '新增条款',
  removed: '废止条款',
  modified: '修订条款',
  changed: '条文内容修订',
  unchanged: '未变更',
  alignment_pending: '章节编号调整待比对',
  order_or_layout_changed: '条文顺序或排版调整',
  clause_hash_mismatch: '条文校验待复核',
  parse_not_available: '新旧文本待拆分',
}
const cleanSummary = (s: string) => (s || '').replace(/归档字节变化/g, '官方原文版式更新').replace(/待核对/g, '待确认')
const countryZh = (code: string) => COUNTRY_NAMES[code] || code
const date = (value: string | null) => localTime(value) || '未记录'
async function loadDiscoveries() {
  const result = await api.get<{ items: Discovery[]; total: number }>(`/admin/updates/discoveries?offset=${(page.value-1)*25}&limit=25${country.value ? `&country=${encodeURIComponent(country.value)}` : ''}`)
  discoveries.value = result.items; total.value = result.total
}
async function load() {
  busy.value = true; error.value = ''
  try {
    const [metrics, rep] = await Promise.all([
      api.get<Sla>('/admin/updates/sla'),
      api.get<SlaReport>('/admin/updates/report').catch(() => null),
      loadChanges(),
      loadDiscoveries(),
    ])
    sla.value = metrics
    report.value = rep
  } catch(e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
async function exportReport() {
  exporting.value = true
  try {
    const res = await fetch('/api/admin/updates/report/export.xlsx', { headers: { Authorization: `Bearer ${api.token()}` } })
    if (!res.ok) throw new Error('导出统计报表失败')
    const url = URL.createObjectURL(await res.blob())
    const a = document.createElement('a')
    a.href = url
    a.download = serverFilename(res, '法规更新与时效统计报表.xlsx')
    document.body.appendChild(a)
    a.click()
    a.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 30000)
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    exporting.value = false
  }
}
async function loadChanges() {
  try {
    const records = await api.get<{items: Change[]; total: number}>(`/admin/updates/changes?offset=${(changePage.value-1)*25}&limit=25`)
    changes.value = records.items; changeTotal.value = records.total
  } catch(e) { error.value = (e as Error).message }
}
async function changeDiscoveryPage() { try { await loadDiscoveries() } catch(e) { error.value = (e as Error).message } }
async function filter() { page.value = 1; try { await loadDiscoveries() } catch(e) { error.value = (e as Error).message } }
onMounted(load)
</script>
<template>
  <div class="management-page" v-loading="busy">
    <header class="page-heading"><div><h1>更新与时效</h1><p>跟踪各国官方法规条文变化、受影响的企业合规文件提醒及新资料收录入库进展。</p></div><div class="page-actions"><el-button plain @click="router.push('/whitepapers')">国家合规简报增量管理</el-button><el-button type="primary" plain :loading="exporting" data-testid="updates-export-xlsx" @click="exportReport">导出时效统计报表（Excel）</el-button><el-button @click="load">刷新状态</el-button></div></header>
    <el-alert v-if="error" type="error" :closable="false" :title="`更新状态加载失败：${error}`" />
    <div class="sla-cards" data-testid="update-sla-summary">
      <article>
        <span>{{ (report?.response_72h.total_impacts ?? 0) > 0 ? `条文修订按期确认率（待确认 ${sla?.pending_updates ?? 0} · 超期 ${sla?.overdue_72h ?? 0}）` : `条文修订确认进度（待确认 ${sla?.pending_updates ?? 0} · 超期 ${sla?.overdue_72h ?? 0}）` }}</span>
        <strong>{{ (report?.response_72h.total_impacts ?? 0) > 0 ? `${report!.response_72h.on_time_rate_pct}%` : (sla?.pending_updates ?? 0) > 0 ? `${sla!.pending_updates} 项待确认` : '全部已确认' }}</strong>
      </article>
      <article>
        <span>采集站点按期检查率（在用 {{ report?.patrol_timeliness.active_sites ?? '—' }} 个官方站点）</span>
        <strong>{{ report ? ((report.patrol_timeliness.active_sites ?? 0) > 0 ? `${report.patrol_timeliness.on_schedule_rate_pct}%` : '暂无在用站点') : '—' }}</strong>
      </article>
      <article>
        <span>{{ (report?.intake_30d.tracked ?? 0) > 0 ? `近 30 日新收录法规（全库 ${report?.intake_30d.in_library_documents_total ?? (total || sla?.intake.completed || 0)} 份 · 近 30 日新收录 ${report!.intake_30d.tracked} 份）` : `近 30 日新收录法规（全库已收录 ${report?.intake_30d.in_library_documents_total ?? (total || sla?.intake.completed || 0)} 份）` }}</span>
        <strong>{{ (report?.intake_30d.tracked ?? 0) > 0 ? ((report!.intake_30d.pending ?? 0) > 0 ? `${report!.intake_30d.pending} 份待入库` : `${report!.intake_30d.on_time_rate_pct}%`) : '0 份待入库' }}</strong>
      </article>
      <article>
        <span>简报与准入覆盖（合规简报 {{ report?.linkage.whitepapers_published ?? 0 }} 国 · 准入事项 {{ report?.linkage.admission_entries_total ?? 0 }} 项）</span>
        <strong>{{ report?.linkage.enterprise_notices_pushed ?? sla?.resolved_updates ?? 0 }} <small style="font-size:14px;font-weight:400">条更新提醒</small></strong>
      </article>
    </div>
    <el-alert type="info" :closable="false" title="当官方来源法规发生条文修订时，系统保留既有条文、知识图谱与企业历史文件，提示管理员确认修订内容；确认后向引用该条文的企业项目推送更新提醒，并可按受影响章节更新国家合规简报。" />
    <section class="updates-section"><h2>法规条文修订记录</h2>
      <el-empty v-if="!changes.length && !busy" description="当前各国已收录法规均为现行有效版本，暂无待确认的条文修订" />
      <details v-for="change in changes" :key="change.id" class="change-record" data-testid="source-change-record">
        <summary><span>{{ countryZh(change.country_code) }} · {{ change.document_title }}</span><el-tag :type="change.kind === 'bytes_only' ? 'info' : 'warning'">{{ kindLabel[change.kind] || '修订待确认' }}</el-tag></summary>
        <p>{{ cleanSummary(change.summary) }}</p><p>检测时间：{{ date(change.detected_at) }} · 涉及知识图谱关联规则 {{ change.graph_edge_ids.length }} 条</p>
        <div class="change-links">
          <a :href="change.source_url" target="_blank" rel="noopener noreferrer">查看官方来源</a>
          <el-button v-if="change.country_code !== 'CN'" size="small" type="primary" link @click="router.push({ path: '/whitepapers', query: { country: change.country_code, sync: '1', dims: (change.affected_dims?.length ? change.affected_dims : ['D1']).join(',') } })">按受影响章节更新该国简报 →</el-button>
        </div>
        <ul v-if="change.changes.length"><li v-for="(clause,index) in change.changes" :key="index">{{ clause.section_path || '正文章节' }} · {{ clause.clause_number || '对应条款' }} · {{ clauseKindLabel[clause.kind] || '条文变更待确认' }}</li></ul>
      </details>
      <el-pagination v-if="changeTotal > 25" v-model:current-page="changePage" :page-size="25" :total="changeTotal" layout="prev, pager, next" @current-change="loadChanges" />
    </section>
    <section class="updates-section"><div class="section-heading"><h2>法规资料入库与时效记录</h2><el-select v-model="country" clearable placeholder="全部国别" aria-label="跟踪国别" style="width:160px" @change="filter"><el-option v-for="code in sla?.countries || []" :key="code" :label="countryZh(code)" :value="code" /></el-select></div>
      <p>记录各国官方法规、技术标准与申报表格从发现收录到完成条款切分入库的时效状态。</p>
      <el-table :data="discoveries" border empty-text="暂无入库跟踪记录">
        <el-table-column label="国别" width="90"><template #default="{ row }">{{ countryZh(row.country_code) }}</template></el-table-column>
        <el-table-column prop="title" label="法规文件" min-width="260" show-overflow-tooltip />
        <el-table-column label="收录时间" min-width="170"><template #default="{ row }">{{ date(row.discovered_at) }}</template></el-table-column>
        <el-table-column label="完成入库时间" min-width="170"><template #default="{ row }">{{ row.first_parsed_at ? date(row.first_parsed_at) : row.history_unknown ? date(row.discovered_at) : '入库处理中' }}</template></el-table-column>
        <el-table-column label="当前状态" min-width="140"><template #default="{ row }"><el-tag :type="row.overdue ? 'danger' : (row.first_parsed_at || row.history_unknown) ? 'success' : 'warning'">{{ row.overdue ? '入库超期' : (row.first_parsed_at || row.history_unknown) ? '已入库生效' : '入库处理中' }}</el-tag></template></el-table-column>
      </el-table>
      <el-pagination v-if="total > 25" v-model:current-page="page" :page-size="25" :total="total" layout="prev, pager, next" @current-change="changeDiscoveryPage" />
    </section>
  </div>
</template>
<style scoped>
.management-page{min-width:0;max-width:100%}
.page-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}
.page-heading>div:first-child{flex:1 1 460px;min-width:0}
.page-actions{display:flex;gap:10px;flex-shrink:0;flex-wrap:wrap}
.sla-cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:18px;margin:24px 0}.sla-cards article{padding:20px;border:1px solid var(--el-border-color-light);border-radius:14px;background:white;min-width:0}.sla-cards span{font-size:13px;color:var(--el-text-color-secondary)}.sla-cards strong{display:block;margin-top:12px;font-size:24px;color:#17344d}
.updates-section{margin-top:32px}.updates-section h2{font-size:18px}.updates-section p{line-height:1.7;color:var(--el-text-color-secondary);font-size:13px}.change-record{background:white;border:1px solid var(--el-border-color-light);border-radius:12px;padding:20px;margin:12px 0}.change-record>summary{cursor:pointer;display:flex;gap:14px;align-items:center;flex-wrap:wrap}.change-record>summary>span{flex:1;min-width:240px;font-weight:600;font-size:15px}.change-record li{margin:8px 0;font-size:13px}.change-record pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size: 13px;max-height:300px;overflow:auto;background:#f5f7fa;padding:16px}.section-heading{display:flex;justify-content:space-between;gap:16px;align-items:center;margin-bottom:12px}
@media(max-width:900px){.sla-cards{grid-template-columns:repeat(2,minmax(0,1fr))}}
.change-links{display:flex;gap:16px;align-items:center;margin:6px 0}
</style>
