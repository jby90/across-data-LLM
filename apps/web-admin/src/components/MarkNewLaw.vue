<script setup lang="ts">
// 法规详情里的“标记为新法规”：没有公布 / 施行日期、首次入库的法规不会推送给订阅的企业，
// 管理员确认它是新发布的，点这里推送一次（同一部只能标一次）。超级管理员和运维管理员都能点。
import { localTime } from '@cyshuhe/shared'
import { ElMessage } from 'element-plus'
import { computed, ref } from 'vue'
import { api } from '../api'

const props = defineProps<{
  doc: { id: string; relevance?: string; has_date?: boolean; news_marked_at?: string | null }
}>()
const emit = defineEmits<{ (e: 'done'): void }>()

const dialog = ref(false)
// 官方公布日期不能晚于今天
const afterToday = (d: Date) => d.getTime() > Date.now()
const published = ref('')
const saving = ref(false)
// 只给“会向企业展示、又没有日期、还没标过”的法规显示按钮；标过的显示结果
const shown = computed(() => ['relevant', 'related'].includes(props.doc.relevance ?? 'relevant') && (!props.doc.has_date || !!props.doc.news_marked_at))
const marked = computed(() => !!props.doc.news_marked_at)

async function mark() {
  saving.value = true
  try {
    await api.post(`/admin/documents/${props.doc.id}/mark-new`, { published_date: published.value || null })
    ElMessage.success('已标记为新法规，订阅的企业会收到一次推送')
    dialog.value = false
    published.value = ''
    emit('done')
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div v-if="shown" class="mark-new" data-testid="mark-new">
    <template v-if="marked">
      <span class="note" data-testid="mark-new-done">已于 {{ localTime(doc.news_marked_at ?? null) }} 标记为新法规，已推送给订阅的企业。</span>
    </template>
    <template v-else>
      <el-button size="small" data-testid="mark-new-btn" @click="dialog = true">标记为新法规</el-button>
      <span class="note">未带官方公布日期的法规首次入库默认不自动推送；确认属新发布法规后可点击此处向订阅企业推送。</span>
    </template>
    <el-dialog v-model="dialog" title="标记为新法规" width="min(440px, 94vw)" append-to-body>
      <p class="note">标记后将按企业订阅范围推送一次（附影响分析与应对建议），每部法规仅推送一次。</p>
      <el-form label-position="top">
        <el-form-item label="官方公布日期（选填）">
          <el-date-picker v-model="published" type="date" value-format="YYYY-MM-DD" placeholder="选择日期" style="width: 220px" :disabled-date="afterToday" data-testid="mark-new-date" />
          <span class="note">填写后将同步写入该法规的公布日期。</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" data-testid="mark-new-save" @click="mark">标记并推送</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.mark-new { display: flex; align-items: center; gap: 12px; margin: 0 0 12px; padding: 10px 14px; background: var(--el-fill-color-lighter); border-radius: 8px; }
.note { font-size: 13px; color: var(--el-text-color-secondary); line-height: 1.7; }
</style>
