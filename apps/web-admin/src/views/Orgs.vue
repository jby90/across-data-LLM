<script setup lang="ts">
// 企业与账号：先列企业，展开一家看这家企业下的账号（门户注册的和超级管理员开通的），
// 在企业下面新建账号、设调用外部模型时隐去的词；管理员账号单列在下面；停用的企业不显示。
// 四级角色（需求 2.7）：管理端 超级管理员 / 运维管理员，企业下 企业用户 / 普通查看者（只读，下载单独开）
import { ROLE_HINT, ROLE_LABEL, type OrgOut, type Role, type UserOut, userName } from '@cyshuhe/shared'
import { useAuth } from '../auth'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'

const orgs = ref<OrgOut[]>([])
const users = ref<UserOut[]>([])
const newOrg = ref('')
const showInactiveOrgs = ref(false)
const editing = ref<OrgOut | null>(null)
const termsText = ref('')
const saving = ref(false)
const expanded = ref<string[]>([])

const usersOf = (orgId: string) => users.value.filter((u) => u.org_id === orgId)
const memberCountOf = (orgId: string) => usersOf(orgId).filter((u) => u.is_active && u.role === 'member').length
const viewerCountOf = (orgId: string) => usersOf(orgId).filter((u) => u.is_active && u.role === 'viewer').length
const inactiveOrgCountOf = (orgId: string) => usersOf(orgId).filter((u) => !u.is_active).length
const admins = computed(() => users.value.filter((u) => u.role === 'platform_admin' || u.role === 'ops_admin'))
const activeOrgIds = computed(() => new Set(orgs.value.filter((o) => o.is_active).map((o) => o.id)))
const roleSummary = computed(() => {
  const countRole = (r: Role) =>
    users.value.filter((u) => u.role === r && u.is_active && (r === 'platform_admin' || r === 'ops_admin' || (u.org_id && activeOrgIds.value.has(u.org_id)))).length
  return (['platform_admin', 'ops_admin', 'member', 'viewer'] as Role[]).map((r) => ({
    role: r,
    label: ROLE_LABEL[r],
    hint: ROLE_HINT[r],
    side: r === 'platform_admin' || r === 'ops_admin' ? '管理端' : '用户端',
    count: countRole(r),
  }))
})
const opsAdminCount = computed(() => admins.value.filter((u) => u.is_active && u.role === 'ops_admin').length)
const superAdminCount = computed(() => admins.value.filter((u) => u.is_active && u.role === 'platform_admin').length)
const inactiveAdminCount = computed(() => admins.value.filter((u) => !u.is_active).length)
const hasAnyDialogOpen = computed(() => dialog.open || pwdDialog.open || !!editing.value)
const me = useAuth()
const SOURCE: Record<string, string> = { admin: '超级管理员开通', portal: '门户注册' }
const ORG_ROLE_DISPLAY: Record<'member' | 'viewer', string> = {
  member: '企业用户（编辑者）',
  viewer: '普通查看者（只读用户）',
}
const ORG_ROLE_SCOPE_TEXT: Record<'member' | 'viewer', string> = {
  member: '编辑者 · 可新建评估与修改报告',
  viewer: '只读用户 · 仅限查阅，禁止新建或改报告',
}
const orgRoleFilter = reactive<Record<string, 'all' | 'member' | 'viewer'>>({})
function filteredUsersOf(orgId: string): UserOut[] {
  const f = orgRoleFilter[orgId] || 'all'
  const list = usersOf(orgId)
  return f === 'all' ? list : list.filter((u) => u.role === f)
}

async function load() {
  const orgQuery = showInactiveOrgs.value ? '?include_inactive=true' : ''
  const userQuery = showInactiveOrgs.value ? '?include_inactive_orgs=true' : ''
  ;[orgs.value, users.value] = await Promise.all([
    api.get<OrgOut[]>(`/admin/orgs${orgQuery}`),
    api.get<UserOut[]>(`/admin/users${userQuery}`),
  ])
  if (!expanded.value.length && orgs.value.length <= 3) {
    expanded.value = orgs.value.filter((o) => o.is_active).map((o) => o.id)
  }
}

async function renameOrg(org: OrgOut) {
  let nextName = ''
  try {
    const r = await ElMessageBox.prompt('请输入更新后的企业全称', '修改企业名称', {
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputValue: org.name,
      inputPattern: /\S.{1,}/,
      inputErrorMessage: '企业名称至少 2 个字符',
    })
    nextName = (r as { value: string }).value.trim()
  } catch {
    return
  }
  if (!nextName || nextName === org.name) return
  try {
    await api.patch(`/admin/orgs/${org.id}`, { name: nextName })
    ElMessage.success('企业名称已更新')
    await load()
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function toggleOrgActive(org: OrgOut, nextActive: boolean) {
  if (!nextActive) {
    try {
      await ElMessageBox.confirm(
        `停用后「${org.name}」下属账号将暂停登录，历史评估项目与审计留痕完整保留；勾选显示已停用企业可随时重新启用。`,
        '确认停用企业？',
        { confirmButtonText: '停用企业', cancelButtonText: '取消', type: 'warning' },
      )
    } catch {
      return
    }
  }
  try {
    await api.patch(`/admin/orgs/${org.id}`, { is_active: nextActive })
    ElMessage.success(nextActive ? '企业已重新启用' : '企业已停用')
    await load()
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function createOrg() {
  if (!newOrg.value.trim()) return
  try {
    const org = await api.post<OrgOut>('/admin/orgs', { name: newOrg.value.trim() })
    newOrg.value = ''
    ElMessage.success('已创建')
    await load()
    expanded.value = [org.id]
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

function editTerms(org: OrgOut) {
  editing.value = org
  termsText.value = (org.llm_redaction_terms || []).join('\n')
}

async function saveTerms() {
  if (!editing.value || saving.value) return
  const terms = termsText.value.split('\n').map((x) => x.trim()).filter(Boolean)
  const bad = terms.find((x) => x.length < 2 || x.length > 200)
  if (bad !== undefined) return ElMessage.warning(bad.length < 2 ? `词条「${bad}」过短，每项至少 2 个字符` : `单条词条不超过 200 个字符，请适当拆分`)
  if (terms.length > 100) return ElMessage.warning(`保护词条上限为 100 项，当前已输入 ${terms.length} 项`)
  saving.value = true
  try {
    await api.patch(`/admin/orgs/${editing.value.id}/redaction`, {
      terms: termsText.value.split('\n').map((s) => s.trim()).filter(Boolean),
    })
    editing.value = null
    await load()
    ElMessage.success('保护清单已保存，后续模型调用将按新规则自动脱敏')
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    saving.value = false
  }
}

// 新建账号：在某家企业下建企业用户或普通查看者，或新建管理员（超级 / 运维）
const dialog = reactive({ open: false, org: null as OrgOut | null, email: '', display_name: '', password: '', role: 'member' as Role, can_export: false })
function openAccount(org: OrgOut | null, presetRole?: Role) {
  Object.assign(dialog, { open: true, org, email: '', display_name: '', password: '', role: presetRole || (org ? 'member' : 'ops_admin'), can_export: false })
}
const ROLES_HERE = computed<Role[]>(() => (dialog.org ? ['member', 'viewer'] : ['ops_admin', 'platform_admin']))
// 改已有账号的角色、查看者的下载权限、启用或停用
async function updateUser(u: UserOut, body: { role?: Role; can_export?: boolean; is_active?: boolean }) {
  try {
    await api.patch(`/admin/users/${u.id}`, body)
    await load()
    if (body.role !== undefined) ElMessage.success(`已改为${ROLE_LABEL[body.role]}`)
    else if (body.is_active !== undefined) ElMessage.success(body.is_active ? '账号已启用' : '账号已停用')
    else ElMessage.success(body.can_export ? '已允许下载' : '已关闭下载')
  } catch (e) {
    ElMessage.error((e as Error).message)
    await load()
  }
}

const pwdDialog = reactive({ open: false, user: null as UserOut | null, newPassword: '', saving: false })
function openResetPwd(u: UserOut) {
  pwdDialog.user = u
  pwdDialog.newPassword = ''
  pwdDialog.open = true
}
async function submitResetPwd() {
  if (!pwdDialog.user) return
  if (pwdDialog.newPassword.length < 12) {
    ElMessage.warning('新密码长度至少为 12 位')
    return
  }
  pwdDialog.saving = true
  try {
    await api.patch(`/admin/users/${pwdDialog.user.id}`, { new_password: pwdDialog.newPassword })
    pwdDialog.open = false
    ElMessage.success(`已重置 ${pwdDialog.user.email} 的登录密码`)
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    pwdDialog.saving = false
  }
}

async function submitAccount() {
  const cleanEmail = dialog.email.trim()
  const fallbackName = cleanEmail.split('@')[0] || '用户'
  try {
    await api.post('/admin/users', {
      email: cleanEmail,
      display_name: dialog.display_name.trim() || fallbackName,
      password: dialog.password,
      role: dialog.role,
      org_id: dialog.org ? dialog.org.id : null,
      can_export: dialog.role === 'viewer' && dialog.can_export,
    })
    ElMessage.success('已开通')
    const org = dialog.org
    dialog.open = false
    await load()
    if (org && !expanded.value.includes(org.id)) expanded.value = [...expanded.value, org.id]
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

onMounted(load)
</script>

<template>
  <div class="management-page">
    <header class="page-heading">
      <div>
        <h1>企业与账号</h1>
        <p>管理后台四类权限角色账号：管理端设超级管理员、运维管理员；企业下细分为<strong>企业用户（编辑者，可新建与修改报告）</strong>与<strong>普通查看者（只读用户，仅限查阅、禁止新建或修改，控制并发与误改风险）</strong>，门户自助注册体验账号归属独立体验空间隔离管理。</p>
      </div>
    </header>

    <section class="role-cards" data-testid="role-overview">
      <div v-for="item in roleSummary" :key="item.role" class="role-card">
        <div class="role-card__head">
          <strong>{{ item.label }}<small class="role-side">{{ item.side }}</small></strong>
          <span v-if="item.count > 0" class="cy-status cy-status--done">{{ item.count }} 个</span>
          <span v-else class="cy-status cy-status--muted">暂未开通</span>
        </div>
        <p class="role-card__hint">{{ item.hint }}</p>
        <div v-if="item.count === 0" class="role-card__act">
          <el-button v-if="item.role === 'ops_admin'" link type="primary" size="small" @click="openAccount(null, 'ops_admin')">新建运维管理员</el-button>
          <el-button v-else-if="item.role === 'viewer' && orgs.length" link type="primary" size="small" @click="openAccount(orgs[0], 'viewer')">在企业下开通查看者</el-button>
        </div>
      </div>
    </section>

    <div class="toolbar">
      <el-input v-model="newOrg" placeholder="输入公司全称" aria-label="新企业名称" @keyup.enter="createOrg" />
      <el-button :type="hasAnyDialogOpen ? 'default' : 'primary'" @click="createOrg">创建企业</el-button>
      <el-checkbox v-model="showInactiveOrgs" data-testid="org-show-inactive" @change="load">显示已停用企业</el-checkbox>
    </div>

    <el-table :data="orgs" row-key="id" :expand-row-keys="expanded" border data-testid="org-table"
      @expand-change="(_: OrgOut, rows: OrgOut[]) => (expanded = rows.map((r) => r.id))">
      <el-table-column type="expand">
        <template #default="{ row }">
          <div class="org-detail" :data-testid="`org-detail-${row.id}`">
            <div class="detail-head">
              <h3>{{ row.name }} 账号列表（编辑者 {{ memberCountOf(row.id) }} · 只读用户 {{ viewerCountOf(row.id) }}<template v-if="inactiveOrgCountOf(row.id) > 0">，停用 {{ inactiveOrgCountOf(row.id) }} 个</template>）</h3>
              <div class="detail-head-actions">
                <el-radio-group :model-value="orgRoleFilter[row.id] || 'all'" size="small" :data-testid="`org-role-filter-${row.id}`" @update:model-value="(v: string | number | boolean | undefined) => (orgRoleFilter[row.id] = (v as 'all' | 'member' | 'viewer') || 'all')">
                  <el-radio-button value="all">全部</el-radio-button>
                  <el-radio-button value="member">编辑者</el-radio-button>
                  <el-radio-button value="viewer">只读用户</el-radio-button>
                </el-radio-group>
                <el-button size="small" :data-testid="`org-add-viewer-${row.id}`" @click="openAccount(row, 'viewer')">新增只读用户</el-button>
                <el-button size="small" type="primary" plain data-testid="org-add-user" @click="openAccount(row, 'member')">新增企业账号</el-button>
              </div>
            </div>
            <el-table v-if="filteredUsersOf(row.id).length" :data="filteredUsersOf(row.id)" size="small" data-testid="org-users">
              <el-table-column label="姓名" width="132"><template #default="{ row: u }">{{ userName(u) }}</template></el-table-column>
              <el-table-column prop="email" label="登录邮箱" min-width="190" show-overflow-tooltip />
              <el-table-column label="角色" width="196"><template #default="{ row: u }">
                <el-select :model-value="u.role" size="small" style="width: 176px" :data-testid="`user-role-${u.email}`" @change="(r: Role) => updateUser(u, { role: r })">
                  <el-option v-for="r in (['member', 'viewer'] as const)" :key="r" :label="ORG_ROLE_DISPLAY[r]" :value="r" />
                </el-select></template></el-table-column>
              <el-table-column label="权限边界" min-width="210"><template #default="{ row: u }"><span class="muted">{{ ORG_ROLE_SCOPE_TEXT[(u.role === 'viewer' ? 'viewer' : 'member')] }}</span></template></el-table-column>
              <el-table-column label="下载文件" width="96"><template #default="{ row: u }">
                <el-switch v-if="u.role === 'viewer'" :model-value="!!u.can_export" :data-testid="`user-export-${u.email}`" @change="(v: string | number | boolean) => updateUser(u, { can_export: !!v })" />
                <el-switch v-else :model-value="true" disabled aria-label="企业用户都可以下载" /></template></el-table-column>
              <el-table-column v-if="new Set(users.map((x) => x.signup_source || 'admin')).size > 1" label="来源" width="128"><template #default="{ row: u }">{{ SOURCE[u.signup_source || 'admin'] }}</template></el-table-column>
              <el-table-column label="启用状态" width="124" class-name="cy-col-nowrap">
                <template #default="{ row: u }">
                  <el-switch :model-value="u.is_active" :data-testid="`user-active-${u.email}`" @change="(v: string | number | boolean) => updateUser(u, { is_active: !!v })" />
                  <span class="state-inline">{{ u.is_active ? '正常' : '已停用' }}</span>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="124" fixed="right" class-name="cy-col-nowrap">
                <template #default="{ row: u }">
                  <el-button link type="primary" size="small" class="org-op-link" :data-testid="`user-reset-pwd-${u.email}`" @click="openResetPwd(u)">重置密码</el-button>
                </template>
              </el-table-column>
            </el-table>
            <p v-else class="empty">该企业暂无账号</p>
            <p v-if="usersOf(row.id).length && viewerCountOf(row.id) === 0" class="role-empty-note">该企业暂未开通普通查看者账号；可点击右上角新增企业账号开通，或调整现有账号角色。</p>
            <div class="detail-head terms">
              <h3>模型调用敏感词保护清单</h3>
              <el-button size="small" @click="editTerms(row)">设置</el-button>
            </div>
            <p class="terms-line">企业名称默认受保护；{{ (row.llm_redaction_terms || []).length ? `已配置 ${row.llm_redaction_terms.length} 项专有名词：${row.llm_redaction_terms.join('、')}` : '可按需补充车型代号、内部项目名称等专有名词' }}</p>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="企业名称（公司全称）" min-width="240">
        <template #default="{ row }">
          <span>{{ row.name }}</span>
          <el-tag v-if="!row.is_active" size="small" type="info" style="margin-left: 8px">已停用</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="账号分布" width="250"><template #default="{ row }">企业用户 {{ memberCountOf(row.id) }} · 普通查看者 {{ viewerCountOf(row.id) }}<template v-if="inactiveOrgCountOf(row.id) > 0">（停用 {{ inactiveOrgCountOf(row.id) }} 个）</template></template></el-table-column>
      <el-table-column label="操作" width="168" fixed="right" class-name="cy-col-nowrap">
        <template #default="{ row }">
          <el-button link type="primary" size="small" class="org-op-link" :data-testid="`org-rename-${row.id}`" @click.stop="renameOrg(row)">修改名称</el-button>
          <el-button v-if="row.is_active" link type="danger" size="small" class="org-op-link" :data-testid="`org-toggle-${row.id}`" @click.stop="toggleOrgActive(row, false)">停用</el-button>
          <el-button v-else link type="primary" size="small" class="org-op-link" :data-testid="`org-toggle-${row.id}`" @click.stop="toggleOrgActive(row, true)">启用</el-button>
        </template>
      </el-table-column>
    </el-table>

    <section class="admins">
      <div class="detail-head">
        <h2>管理员<small class="section-count">（超级管理员 {{ superAdminCount }} · 运维管理员 {{ opsAdminCount }}<template v-if="inactiveAdminCount > 0">，停用 {{ inactiveAdminCount }} 个</template>）</small></h2>
        <el-button size="small" data-testid="admin-add" @click="openAccount(null)">新建管理员</el-button>
      </div>
      <el-table :data="admins" border size="small" data-testid="admin-users">
        <el-table-column label="姓名" width="140"><template #default="{ row }">{{ userName(row) }}</template></el-table-column>
        <el-table-column prop="email" label="登录邮箱" min-width="200" show-overflow-tooltip />
        <el-table-column label="角色" width="180"><template #default="{ row }">
          <span v-if="row.id === me.user?.id">{{ ROLE_LABEL[row.role as Role] }}（当前登录）</span>
          <el-select v-else :model-value="row.role" size="small" style="width: 128px" @change="(r: Role) => updateUser(row, { role: r })">
            <el-option v-for="r in (['platform_admin', 'ops_admin'] as Role[])" :key="r" :label="ROLE_LABEL[r]" :value="r" />
          </el-select></template></el-table-column>
        <el-table-column label="权限范围" min-width="220"><template #default="{ row }"><span class="muted">{{ ROLE_HINT[row.role as Role] }}</span></template></el-table-column>
        <el-table-column label="启用状态" width="124" class-name="cy-col-nowrap">
          <template #default="{ row }">
            <el-switch :model-value="row.is_active" :disabled="row.id === me.user?.id" @change="(v: string | number | boolean) => updateUser(row, { is_active: !!v })" />
            <span class="state-inline">{{ row.is_active ? '正常' : '已停用' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="124" fixed="right" class-name="cy-col-nowrap">
          <template #default="{ row }">
            <el-button link type="primary" size="small" class="org-op-link" @click="openResetPwd(row)">重置密码</el-button>
          </template>
        </el-table-column>
      </el-table>
      <p v-if="opsAdminCount === 0" class="role-empty-note">当前还没有开通运维管理员账号；可点右上角新建管理员并选择运维管理员开通。</p>
    </section>

    <el-dialog v-model="dialog.open" :title="dialog.org ? `${dialog.org.name} · 新建企业账号` : '新建管理员'" width="min(480px, 94vw)">
      <el-form label-position="top">
        <el-form-item label="角色">
          <el-radio-group v-model="dialog.role" class="role-pick" data-testid="account-role">
            <el-radio v-for="r in ROLES_HERE" :key="r" :value="r"><strong>{{ (r === 'member' || r === 'viewer') ? ORG_ROLE_DISPLAY[r] : ROLE_LABEL[r] }}</strong><small>{{ r === 'viewer' ? '仅限查看本企业的评估项目、合规文件、简报与属地准入；无权新建或修改企业项目，避免多人误改与并发压力' : ROLE_HINT[r] }}</small></el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="dialog.role === 'viewer'"><el-checkbox v-model="dialog.can_export" data-testid="account-can-export">允许下载评估文件、申请材料和简报</el-checkbox></el-form-item>
        <el-form-item label="姓名（选填，留空默认取邮箱前缀）"><el-input v-model="dialog.display_name" data-testid="account-name-input" /></el-form-item>
        <el-form-item label="邮箱"><el-input v-model="dialog.email" data-testid="account-email-input" /></el-form-item>
        <el-form-item label="初始密码（至少 12 位）"><el-input v-model="dialog.password" type="password" show-password data-testid="account-password-input" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog.open = false">取消</el-button>
        <el-button type="primary" data-testid="account-submit" @click="submitAccount">开通</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="pwdDialog.open" :title="pwdDialog.user ? `重置登录密码 · ${pwdDialog.user.email}` : '重置登录密码'" width="min(440px, 94vw)">
      <el-form label-position="top" @submit.prevent="submitResetPwd">
        <el-form-item label="新密码（至少 12 位）">
          <el-input v-model="pwdDialog.newPassword" type="password" show-password data-testid="reset-password-input" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="pwdDialog.open = false">取消</el-button>
        <el-button type="primary" :loading="pwdDialog.saving" data-testid="reset-password-submit" @click="submitResetPwd">确认重置</el-button>
      </template>
    </el-dialog>

    <el-dialog :model-value="!!editing" :title="editing ? `${editing.name} · 模型调用敏感词保护清单` : ''" width="min(520px, 94vw)" @close="editing = null">
      <p>向外部大模型发送请求前，所列专有名词将自动替换为占位代号，返回结果后再行还原。企业名称默认受保护，可按需补充内部车型代号、项目名称等专有名词。</p>
      <el-input v-model="termsText" type="textarea" :rows="6" aria-label="模型调用敏感词保护清单" placeholder="每行输入一项专有名词" />
      <template #footer>
        <el-button @click="editing = null">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveTerms">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.role-cards { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 24px; }
@media (max-width: 1180px) { .role-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.role-card { background: #fff; border: 1px solid var(--cy-line); border-radius: 8px; padding: 16px; display: flex; flex-direction: column; gap: 8px; }
.role-card__head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.role-card__head strong { font-size: 15px; font-weight: 600; color: #20323f; display: inline-flex; align-items: center; gap: 8px; }
.role-side { font-size: 13px; font-weight: 400; color: #64748b; }
.role-card__hint { margin: 0; font-size: 13px; line-height: 1.6; color: #4b5e6c; flex: 1; }
.role-card__act { padding-top: 4px; }
.org-detail { padding: 8px 24px 16px; }
.detail-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin: 8px 0; }
.detail-head-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.detail-head h3, .detail-head h2 { margin: 0; font-size: 15px; }
.section-count { font-size: 13px; font-weight: 400; color: #64748b; margin-left: 4px; }
.role-empty-note { margin: 8px 0 0; font-size: 13px; line-height: 1.6; color: #4b5e6c; }
.detail-head.terms { margin-top: 20px; }
.terms-line, .empty { margin: 4px 0; color: var(--el-text-color-regular); }
.admins { margin-top: 24px; }
.muted { color: var(--el-text-color-secondary); font-size: 13px; }
.state-inline { margin-left: 6px; font-size: 13px; color: #4b5e6c; }
.org-op-link.el-button.is-link { padding: 2px 4px; }
.role-pick { display: flex; flex-direction: column; align-items: flex-start; gap: 10px; }
.role-pick :deep(.el-radio) { height: auto; align-items: flex-start; margin-right: 0; }
.role-pick :deep(.el-radio__label) { display: flex; flex-direction: column; gap: 2px; white-space: normal; line-height: 1.5; }
.role-pick small { color: var(--el-text-color-secondary); font-size: 13px; font-weight: 400; }
</style>
