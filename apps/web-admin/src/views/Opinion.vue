<script setup lang="ts">
import { localTime } from '@cyshuhe/shared'
import { ElMessage } from 'element-plus'
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import { useAuth } from '../auth'

const auth = useAuth()
const isSuperAdmin = computed(() => auth.user?.role === 'platform_admin')

interface Item {
  id: string
  title: string
  publisher: string
  kind: string
  url: string
  published_on: string | null
  level: string | null
  level_label: string
  reasons: string[]
  scale_unknown: boolean
}
interface Detail extends Item {
  evidence: string[]
  excerpt: string
}
interface Source {
  id: string
  publisher: string
  kind: string
  entry_url: string
  last_run_at: string | null
  last_status: string | null
  last_note: string | null
}

const KIND: Record<string, string> = { regulator: '监管部门', industry: '行业协会', media: '官方媒体' }
const TAG: Record<string, string> = { high: 'danger', medium: 'warning', low: 'info' }

const items = ref<Item[]>([])
// 第 7.5 项二轮：舆情条目多，分页显示，每页 30 条；表头固定；日期不折行
const opPage = ref(1)
const OP_PAGE = 30
const pagedItems = computed(() => items.value.slice((opPage.value - 1) * OP_PAGE, opPage.value * OP_PAGE))
watch(items, () => { opPage.value = 1 })
const sources = ref<Source[]>([])
const counts = ref<Record<string, number>>({})
const recent30dCount = ref<number | null>(null)
const totalCount = ref(0)
const level = ref('')
const kind = ref('')
const keyword = ref('')
const loading = ref(false)
const detail = ref<Detail | null>(null)
const showSources = ref(false)
const showDigest = ref(false)

interface DigestConfig {
  enabled: boolean
  weekday: number
  hour_utc: number
  lookback_days: number
  min_level: 'high' | 'medium' | 'low'
  recipients: string[]
  smtp_host: string
  smtp_port: number
  smtp_user: string
  smtp_from: string
  last_sent_at: string | null
  next_run_at: string | null
}
interface DigestPreview {
  subject: string
  period_label: string
  item_count: number
  html_body: string
}
interface DigestRun {
  id: string
  subject: string
  item_count: number
  delivery_mode: string
  recipients: string[]
  status: string
  note: string
  created_at: string | null
}
const digestCfg = ref<DigestConfig>({
  enabled: true, weekday: 1, hour_utc: 1, lookback_days: 7, min_level: 'low',
  recipients: [], smtp_host: '', smtp_port: 587, smtp_user: '', smtp_from: '',
  last_sent_at: null, next_run_at: null,
})
const recipientsInput = ref('')
const digestPreview = ref<DigestPreview | null>(null)
const digestRuns = ref<DigestRun[]>([])
const digestBusy = ref('')

async function openDigestDrawer() {
  showDigest.value = true
  digestBusy.value = 'load'
  try {
    const [cfg, prev, runs] = await Promise.all([
      api.get<DigestConfig>('/api/admin/opinion/digest-config'),
      api.get<DigestPreview>('/api/admin/opinion/digest-preview?days=7'),
      api.get<DigestRun[]>('/api/admin/opinion/digest-runs?limit=10'),
    ])
    digestCfg.value = cfg
    recipientsInput.value = (cfg.recipients || []).join(', ')
    digestPreview.value = prev
    digestRuns.value = runs
  } catch (e) {
    ElMessage.error((e as Error).message || '加载周报配置失败')
  } finally {
    digestBusy.value = ''
  }
}

async function saveDigestConfig() {
  digestBusy.value = 'save'
  try {
    const rcpts = recipientsInput.value.split(/[,;，；\s]+/).map((s) => s.trim()).filter((s) => s.includes('@'))
    const saved = await api.put<DigestConfig>('/api/admin/opinion/digest-config', {
      ...digestCfg.value,
      recipients: rcpts,
    })
    digestCfg.value = saved
    recipientsInput.value = saved.recipients.join(', ')
    digestPreview.value = await api.get<DigestPreview>(
      `/api/admin/opinion/digest-preview?days=${saved.lookback_days}&min_level=${saved.min_level}`,
    )
    ElMessage.success('周报定时推送配置已保存')
  } catch (e) {
    ElMessage.error((e as Error).message || '保存失败')
  } finally {
    digestBusy.value = ''
  }
}

async function sendDigestNow() {
  digestBusy.value = 'send'
  try {
    const rcpts = recipientsInput.value.split(/[,;，；\s]+/).map((s) => s.trim()).filter((s) => s.includes('@'))
    const res = await api.post<DigestRun>('/api/admin/opinion/digest-send', {
      recipients: rcpts.length ? rcpts : null,
    })
    digestRuns.value = await api.get<DigestRun[]>('/api/admin/opinion/digest-runs?limit=10')
    ElMessage.success(`已生成并推送周报（收录 ${res.item_count} 条重点动态），用户端更新通知已同步展示`)
  } catch (e) {
    ElMessage.error((e as Error).message || '推送失败')
  } finally {
    digestBusy.value = ''
  }
}

async function load() {
  loading.value = true
  try {
    const p = new URLSearchParams({ limit: '500' })
    if (level.value) p.set('level', level.value)
    if (kind.value) p.set('kind', kind.value)
    if (keyword.value.trim()) p.set('q', keyword.value.trim())
    const [list, sum] = await Promise.all([
      api.get<Item[]>(`/api/admin/opinion/items?${p.toString()}`),
      api.get<{ counts: Record<string, number>; total_counts?: Record<string, number>; total?: number }>('/api/admin/opinion/summary?days=30'),
    ])
    items.value = list
    counts.value = sum.total_counts || sum.counts
    recent30dCount.value = Object.values(sum.counts || {}).reduce((acc, n) => acc + Number(n || 0), 0)
    totalCount.value = sum.total ?? list.length
  } catch (e) {
    ElMessage.error((e as Error).message || '加载失败')
  } finally {
    loading.value = false
  }
}

async function open(row: Item) {
  try {
    detail.value = await api.get<Detail>(`/api/admin/opinion/items/${row.id}`)
  } catch (e) {
    ElMessage.error((e as Error).message || '加载失败')
  }
}

async function loadSources() {
  showSources.value = true
  sources.value = await api.get<Source[]>('/api/admin/opinion/sources')
}

const when = (s: string | null) => localTime(s) || '—'

onMounted(load)
</script>

<template>
  <div class="management-page">
    <div class="page-heading">
      <div>
        <h1>舆情监测</h1>
        <p>来自网信办、汽车行业主管部门、行业协会和官方媒体的车载数据、智能网联汽车安全、数据出境相关资讯，按关注程度高、中、低排序。</p>
      </div>
      <div class="page-actions">
        <el-button type="primary" plain data-testid="opinion-digest-btn" @click="openDigestDrawer">合规周报与邮件推送</el-button>
        <el-button @click="loadSources">来源与采集情况</el-button>
      </div>
    </div>

    <div class="summary">
      <div class="card high"><div class="n">{{ counts.high || 0 }}</div><div class="t">高风险 · 法定红线与执法预警</div></div>
      <div class="card medium"><div class="n">{{ counts.medium || 0 }}</div><div class="t">中风险 · 新规标准与监管动向</div></div>
      <div class="card low"><div class="n">{{ counts.low || 0 }}</div><div class="t">低风险 · 行业常规合规动态</div></div>
      <div class="card"><div class="n">{{ totalCount }}</div><div class="t">累计收录合规资讯</div></div>
    </div>
    <div class="grading-guide">
      <div><strong>法定分级依据：</strong>对照《汽车数据安全管理若干规定（试行）》《促进和规范数据跨境流动规定》《数据出境安全评估办法》及《个人信息保护法》条文规则自动判定：</div>
      <div class="guide-items">
        <span><el-tag type="danger" size="small">高</el-tag> 涉及重要数据或车外人脸车牌违规处理、超出法定数量标准数据出境、监管约谈处罚或数据泄露事件</span>
        <span><el-tag type="warning" size="small">中</el-tag> 主管部门发布汽车数据、智能网联或跨境流动新规、强制性标准、征求意见稿或敏感座舱定位风险提示</span>
        <span><el-tag type="info" size="small">低</el-tag> 汽车数据安全、车联网与智能驾驶领域常规政策宣贯与行业资讯，未触及法定违规情形</span>
      </div>
    </div>

    <div class="filters">
      <el-select v-model="level" placeholder="全部风险" clearable style="width: 130px" @change="load">
        <el-option label="高" value="high" /><el-option label="中" value="medium" /><el-option label="低" value="low" />
      </el-select>
      <el-select v-model="kind" placeholder="全部来源类型" clearable style="width: 150px" @change="load">
        <el-option v-for="(n, k) in KIND" :key="k" :label="n" :value="k" />
      </el-select>
      <el-input v-model="keyword" placeholder="搜索标题" clearable style="width: 220px" @keyup.enter="load" @clear="load" />
      <el-button :type="showDigest ? 'default' : 'primary'" @click="load">查询</el-button>
    </div>

    <el-table :data="pagedItems" v-loading="loading" border max-height="calc(100vh - 260px)" style="cursor: pointer" @row-click="open">
      <el-table-column label="风险" width="76" class-name="cy-col-nowrap">
        <template #default="{ row }">
          <el-tag :type="TAG[row.level || ''] || 'info'" size="small">{{ row.level_label }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="title" label="标题" min-width="270" show-overflow-tooltip />
      <el-table-column label="来源" width="168" show-overflow-tooltip>
        <template #default="{ row }">{{ row.publisher }}</template>
      </el-table-column>
      <el-table-column label="发布日期" width="118" class-name="cy-col-nowrap">
        <template #default="{ row }">{{ row.published_on || '—' }}</template>
      </el-table-column>
      <el-table-column label="预警提示" min-width="220" show-overflow-tooltip>
        <template #default="{ row }">
          <span v-if="row.level === 'low'" class="muted">未触及重点关注事项</span>
          <span v-else>{{ row.reasons.join('；') || '—' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="96" fixed="right" class-name="cy-col-nowrap">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click.stop="open(row)">查看详情</el-button>
        </template>
      </el-table-column>
      <template #empty>没有符合条件的资讯</template>
    </el-table>
    <el-pagination v-if="items.length > OP_PAGE" v-model:current-page="opPage" :page-size="OP_PAGE" :total="items.length" layout="total, prev, pager, next" class="pager" />

    <el-drawer :model-value="!!detail" :title="detail?.title" size="min(560px, 94vw)" @close="detail = null">
      <div v-if="detail">
        <p class="muted">{{ detail.publisher }} · {{ detail.published_on || '日期未标注' }}</p>
        <p>
          风险等级：<el-tag :type="TAG[detail.level || ''] || 'info'">{{ detail.level_label }}</el-tag>
        </p>
        <div v-if="detail.reasons.length">
          <h4>判断依据</h4>
          <ul><li v-for="r in detail.reasons" :key="r">{{ r }}</li></ul>
        </div>
        <div v-if="detail.evidence.length">
          <h4>文中相关表述</h4>
          <blockquote v-for="e in detail.evidence" :key="e">{{ e }}</blockquote>
        </div>
        <h4>正文摘录</h4>
        <p class="excerpt">{{ detail.excerpt }}</p>
        <el-link :href="detail.url" target="_blank" type="primary">查看原文</el-link>
      </div>
    </el-drawer>

    <el-dialog v-model="showSources" title="来源与采集情况" width="min(760px, 94vw)">
      <el-table :data="sources" size="small">
        <el-table-column label="来源" width="150" prop="publisher" />
        <el-table-column label="类型" width="90"><template #default="{ row }">{{ KIND[row.kind] }}</template></el-table-column>
        <el-table-column label="上次采集" width="160"><template #default="{ row }">{{ when(row.last_run_at) }}</template></el-table-column>
        <el-table-column label="结果" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.last_status === 'failed'" class="bad">采集失败：{{ row.last_note }}</span>
            <span v-else>{{ row.last_note || '尚未采集' }}</span>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <el-drawer v-model="showDigest" title="汽车数据合规周报 · 定时生成与邮件推送" size="min(760px, 94vw)" data-testid="opinion-digest-drawer">
      <div v-loading="digestBusy === 'load'" class="digest-pane">
        <div class="digest-form">
          <div class="digest-row2">
            <label>定时周报推送：<el-switch v-model="digestCfg.enabled" /></label>
            <label>推送周期：
              <el-select v-model="digestCfg.weekday" size="small" style="width: 110px">
                <el-option :value="1" label="每周一" /><el-option :value="3" label="每周三" /><el-option :value="5" label="每周五" />
              </el-select>
            </label>
            <label>统计天数：<el-input-number v-model="digestCfg.lookback_days" size="small" :min="1" :max="30" /></label>
          </div>
          <div class="digest-field">
            <span>订阅邮箱列表（支持多邮箱群发，逗号分隔；留空则同步至企业用户端更新通知页面的行业合规周报专栏）：</span>
            <el-input v-model="recipientsInput" size="small" placeholder="compliance@oem.com, legal@oem.com" />
          </div>
          <div v-if="isSuperAdmin" class="digest-row2">
            <el-input v-model="digestCfg.smtp_host" size="small" style="flex: 1 1 200px" placeholder="发信邮件服务器（选填，如 mail.corp.com）" />
            <label style="display: inline-flex; align-items: center; gap: 4px">端口：<el-input-number v-model="digestCfg.smtp_port" size="small" :min="1" :max="65535" controls-position="right" style="width: 110px" /></label>
            <el-input v-model="digestCfg.smtp_user" size="small" style="flex: 1 1 160px" placeholder="发信登录账号（选填）" />
            <el-input v-model="digestCfg.smtp_from" size="small" style="flex: 1 1 180px" placeholder="对外显示发件邮箱（选填）" />
          </div>
          <div class="digest-actions">
            <el-button size="small" :loading="digestBusy === 'save'" data-testid="opinion-digest-save" @click="saveDigestConfig">保存推送策略</el-button>
            <el-button size="small" type="primary" :loading="digestBusy === 'send'" data-testid="opinion-digest-send" @click="sendDigestNow">立即生成并推送本期周报</el-button>
            <span v-if="digestCfg.next_run_at" class="muted" style="font-size: 13px">下次自动推送：{{ when(digestCfg.next_run_at) }}</span>
          </div>
        </div>

        <div v-if="digestPreview" class="digest-preview-wrap" data-testid="opinion-digest-preview">
          <h4>本期周报内容预览（{{ digestPreview.period_label }} · 共 {{ digestPreview.item_count }} 条）</h4>
          <div class="digest-html-frame" v-html="digestPreview.html_body" />
        </div>

        <div v-if="digestRuns.length" class="digest-history">
          <h4>最近推送记录</h4>
          <el-table :data="digestRuns" size="small" border>
            <el-table-column label="生成时间" width="150" class-name="cy-col-nowrap"><template #default="{ row }">{{ when(row.created_at) }}</template></el-table-column>
            <el-table-column prop="subject" label="周报标题" min-width="180" show-overflow-tooltip />
            <el-table-column prop="item_count" label="条数" width="64" class-name="cy-col-nowrap" />
            <el-table-column prop="note" label="投递状态与归档" min-width="150" show-overflow-tooltip />
          </el-table>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.management-page { min-width: 0; max-width: 100%; }
.page-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; flex-wrap: wrap; }
.page-heading > div:first-child { flex: 1 1 460px; min-width: 0; }
.page-actions { display: flex; gap: 10px; flex-shrink: 0; flex-wrap: wrap; }
.summary { display: flex; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; }
.card { border: 1px solid #e2e8f0; border-radius: 6px; padding: 10px 18px; min-width: 170px; background: #fff; }
.grading-guide { margin: 0 0 14px; padding: 10px 14px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; font-size: 13px; color: #334155; line-height: 1.75; }
.guide-items { display: flex; flex-direction: column; gap: 4px; margin-top: 4px; }
.card .n { font-size: 24px; font-weight: 600; }
.card .t { color: #64748b; font-size: 13px; }
.card.high .n { color: #dc2626; }
.card.medium .n { color: #d97706; }
.filters { display: flex; gap: 10px; margin-bottom: 14px; }
.muted { color: #64748b; }
.bad { color: #dc2626; }
blockquote { margin: 6px 0; padding: 6px 12px; border-left: 3px solid #cbd5e1; color: #334155; }
.excerpt { white-space: pre-wrap; color: #475569; line-height: 1.7; }
.pager { margin-top: 16px; justify-content: flex-end; }
.digest-pane { display: flex; flex-direction: column; gap: 16px; }
.digest-form { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px 14px; display: flex; flex-direction: column; gap: 10px; font-size: 13px; }
.digest-row2 { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.digest-field { display: flex; flex-direction: column; gap: 4px; }
.digest-actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.digest-preview-wrap h4, .digest-history h4 { margin: 0 0 8px; font-size: 14px; color: #0f172a; }
.digest-html-frame { max-height: 360px; overflow: auto; border: 1px solid #e2e8f0; border-radius: 8px; background: #f8fafc; padding: 8px; }
</style>
