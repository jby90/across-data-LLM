<script setup lang="ts">
import { userName } from '@cyshuhe/shared'
import { ElMessage } from 'element-plus'
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import AppIcon from '../components/AppIcon.vue'
import { useAuth } from '../auth'

interface SelfAccountInfo {
  id: string
  email: string
  display_name: string
  role: string
  role_zh: string
  org_id: string | null
  org_name: string | null
  signup_source: string
  can_export: boolean
}

const auth = useAuth()
const route = useRoute()
const router = useRouter()
const navOpen = ref(false)
const groups = [
  {
    label: '工作空间',
    items: [
      { path: '/', title: '工作概览', icon: 'grid' },
      { path: '/cockpit', title: '合规态势看板', icon: 'gauge', super: true },
    ],
  },
  {
    label: '法规与知识',
    items: [
      { path: '/documents', title: '法规文档', icon: 'book' },
      { path: '/intake-quality', title: '入库质量', icon: 'gauge', admin: true },
      { path: '/standards', title: '标准目录', icon: 'ruler', admin: true },
      { path: '/translations', title: '翻译与术语', icon: 'translate' },
      { path: '/knowledge-graph', title: '知识图谱', icon: 'graph', admin: true },
      { path: '/whitepapers', title: '国家合规简报', icon: 'brief', admin: true },
      { path: '/admission', title: '海外准入事项', icon: 'flow', admin: true },
    ],
  },
  {
    label: '运行与管理',
    items: [
      { path: '/updates', title: '更新与时效', icon: 'clock', admin: true },
      { path: '/opinion', title: '舆情监测', icon: 'wave', admin: true },
      { path: '/sites', title: '采集站点', icon: 'globe', admin: true },
      { path: '/orgs', title: '企业与账号', icon: 'building', super: true },
      { path: '/audit-logs', title: '审计记录', icon: 'audit', super: true },
      { path: '/document-templates', title: '合规文件模板', icon: 'template', admin: true },
      { path: '/agent-studio', title: '评估执行记录', icon: 'nodes', super: true },
    ],
  },
  {
    label: '帮助',
    items: [{ path: '/help', title: '使用帮助', icon: 'help' }],
  },
]
// 超级管理员看全部；运维管理员只维护基础数据，不看合规态势看板、企业与账号、审计记录、评估执行记录
const canManage = computed(() => auth.user?.role === 'platform_admin' || auth.user?.role === 'ops_admin')
const isSuper = computed(() => auth.user?.role === 'platform_admin')
const deskName = computed(() => (isSuper.value ? '超级管理员工作台' : '运维管理员工作台'))
const roleBadge = computed(() => (isSuper.value ? '超级管理员' : '运维管理员'))
const navigation = computed(() =>
  groups
    .map((group) => ({
      ...group,
      items: group.items
        .filter((item) => (!('admin' in item) || canManage.value) && (!('super' in item) || isSuper.value))
    }))
    .filter((group) => group.items.length),
)
const current = computed(() =>
  navigation.value.flatMap((group) => group.items).find((item) => item.path === route.path),
)
watch(
  () => route.path,
  () => {
    navOpen.value = false
  },
  { immediate: true },
)
function logout() {
  auth.logout()
  router.replace({ name: 'login' })
}

const accountDialog = reactive({
  open: false,
  loading: false,
  savingName: false,
  savingPwd: false,
  info: null as SelfAccountInfo | null,
  displayName: '',
  oldPassword: '',
  newPassword: '',
  confirmPassword: '',
})

async function openAccountSettings() {
  accountDialog.open = true
  accountDialog.oldPassword = ''
  accountDialog.newPassword = ''
  accountDialog.confirmPassword = ''
  accountDialog.loading = true
  try {
    const info = await api.get<SelfAccountInfo>('/auth/account')
    accountDialog.info = info
    accountDialog.displayName = info.display_name
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    accountDialog.loading = false
  }
}

async function saveDisplayName() {
  const clean = accountDialog.displayName.trim()
  if (!clean) {
    ElMessage.warning('请输入姓名')
    return
  }
  accountDialog.savingName = true
  try {
    const updated = await api.patch<SelfAccountInfo>('/auth/account', { display_name: clean })
    accountDialog.info = updated
    accountDialog.displayName = updated.display_name
    await auth.refreshMe()
    ElMessage.success('个人姓名已保存')
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    accountDialog.savingName = false
  }
}

async function savePassword() {
  if (!accountDialog.oldPassword || !accountDialog.newPassword) {
    ElMessage.warning('请完整填写当前密码与新密码')
    return
  }
  if (accountDialog.newPassword.length < 12) {
    ElMessage.warning('新密码长度至少为 12 位')
    return
  }
  if (accountDialog.newPassword !== accountDialog.confirmPassword) {
    ElMessage.warning('两次输入的新密码不一致')
    return
  }
  accountDialog.savingPwd = true
  try {
    await api.post('/auth/password', {
      old_password: accountDialog.oldPassword,
      new_password: accountDialog.newPassword,
    })
    accountDialog.oldPassword = ''
    accountDialog.newPassword = ''
    accountDialog.confirmPassword = ''
    ElMessage.success('登录密码已修改')
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    accountDialog.savingPwd = false
  }
}
</script>
<template>
  <div class="app-shell" :class="{ 'is-nav-open': navOpen }" @keydown.esc="navOpen = false">
    <a class="skip-link" href="#main-content">跳转至主要内容</a>
    <button v-if="navOpen" class="nav-scrim" aria-label="关闭导航" @click="navOpen = false" />
    <aside class="app-sidebar" aria-label="主导航">
      <RouterLink to="/" class="app-brand">
        <span class="brand-mark"
          ><svg viewBox="0 0 28 28" fill="none" aria-hidden="true">
            <path
              d="M5 9l9-5 9 5v10l-9 5-9-5V9z M5 9l9 5 9-5 M14 14v10"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linejoin="round"
            /></svg
        ></span>
        <div>
          <div class="brand-name">车研数合</div>
          <div class="brand-caption">汽车数据跨境合规</div>
        </div>
      </RouterLink>
      <nav>
        <section v-for="group in navigation" :key="group.label" class="nav-group">
          <div class="nav-group-label">{{ group.label }}</div>
          <RouterLink
            v-for="item in group.items"
            :key="item.path"
            :to="item.path"
            class="nav-link"
            :class="{ 'is-active': route.path === item.path }"
            :aria-current="route.path === item.path ? 'page' : undefined"
            ><AppIcon :name="item.icon" /><span>{{ item.title }}</span></RouterLink
          >
        </section>
      </nav>
      <div class="sidebar-footer"><strong>{{ deskName }}</strong>汽车数据跨境合规平台<template v-if="canManage"><br /><RouterLink to="/licenses">开源许可声明</RouterLink></template></div>
    </aside>
    <div class="app-body">
      <header class="app-topbar">
        <div class="topbar-context">
          <button
            class="mobile-nav-button"
            :aria-expanded="navOpen"
            aria-label="展开导航"
            @click="navOpen = !navOpen"
          >
            <AppIcon name="menu" /></button
          ><RouterLink
            v-if="route.path !== '/'"
            to="/"
            class="topbar-back"
            aria-label="返回工作概览"
            data-testid="back-to-overview"
            ><AppIcon name="arrow-left" /><span class="topbar-back__full">返回工作概览</span><span class="topbar-back__short">概览</span></RouterLink
          ><RouterLink v-if="route.path !== '/'" to="/" class="topbar-crumb-home">{{ deskName }}</RouterLink
          ><span v-else>{{ deskName }}</span><span class="topbar-divider">/</span
          ><strong>{{ current?.title || '工作概览' }}</strong>
        </div>
        <div class="topbar-account" v-if="auth.user">
          <span v-if="userName(auth.user) !== roleBadge" class="account-role-badge" data-testid="topbar-role-badge">{{ roleBadge }}</span
          ><el-dropdown trigger="click"
            ><button type="button" class="account-pill" aria-label="账号操作" data-testid="account-menu-trigger"
              ><AppIcon name="user" class="account-pill__icon" /><span class="account-pill__name" :data-testid="userName(auth.user) === roleBadge ? 'topbar-role-badge' : undefined">{{ userName(auth.user) }}</span><AppIcon name="chevron-down" class="account-pill__chevron" /></button
            ><template #dropdown
              ><el-dropdown-menu
                ><el-dropdown-item data-testid="open-account-settings" @click="openAccountSettings"
                  ><AppIcon name="users" />账号设置</el-dropdown-item
                ><el-dropdown-item divided data-testid="logout-btn" @click="logout"
                  ><AppIcon name="logout" />退出登录</el-dropdown-item
                ></el-dropdown-menu
              ></template
            ></el-dropdown
          >
        </div>
      </header>
      <main id="main-content" class="app-content" tabindex="-1">
        <RouterView v-slot="{ Component }"
          ><div :key="route.path" class="app-page">
            <component :is="Component" /></div
        ></RouterView>
      </main>
    </div>

    <el-dialog v-model="accountDialog.open" title="账号设置" width="min(480px, 94vw)" destroy-on-close>
      <div v-loading="accountDialog.loading" class="acct-dialog" data-testid="account-settings-dialog">
        <div v-if="accountDialog.info" class="acct-summary">
          <div class="acct-row"><span>登录邮箱</span><strong>{{ accountDialog.info.email }}</strong></div>
          <div class="acct-row"><span>管理端角色</span><strong>{{ accountDialog.info.role_zh }}</strong></div>
        </div>

        <section class="acct-section">
          <h3>个人资料</h3>
          <el-form label-position="top" @submit.prevent="saveDisplayName">
            <el-form-item label="姓名">
              <div class="acct-inline">
                <el-input v-model="accountDialog.displayName" maxlength="100" data-testid="account-display-name" />
                <el-button :loading="accountDialog.savingName" data-testid="account-save-name" @click="saveDisplayName">保存姓名</el-button>
              </div>
            </el-form-item>
          </el-form>
        </section>

        <section class="acct-section">
          <h3>修改登录密码</h3>
          <el-form label-position="top" @submit.prevent="savePassword">
            <el-form-item label="当前密码">
              <el-input v-model="accountDialog.oldPassword" type="password" show-password autocomplete="current-password" data-testid="account-old-password" />
            </el-form-item>
            <el-form-item label="新密码（至少 12 位）">
              <el-input v-model="accountDialog.newPassword" type="password" show-password autocomplete="new-password" data-testid="account-new-password" />
            </el-form-item>
            <el-form-item label="确认新密码">
              <el-input v-model="accountDialog.confirmPassword" type="password" show-password autocomplete="new-password" data-testid="account-confirm-password" />
            </el-form-item>
            <div class="acct-actions">
              <el-button @click="accountDialog.open = false">关闭</el-button>
              <el-button type="primary" :loading="accountDialog.savingPwd" data-testid="account-save-password" @click="savePassword">更新密码</el-button>
            </div>
          </el-form>
        </section>
      </div>
    </el-dialog>
  </div>
</template>
<style scoped>
.skip-link {
  position: fixed;
  left: 250px;
  top: -60px;
  z-index: 100;
  padding: 10px 18px;
  background: white;
  border: 2px solid var(--cy-blue);
  border-radius: 8px;
}
.skip-link:focus {
  top: 10px;
}
.nav-count { margin-left: auto; min-width: 18px; padding: 0 6px; border-radius: 9px; background: #dc2626; color: #fff; font-size: 13px; font-style: normal; line-height: 18px; text-align: center; }
.acct-dialog { display: flex; flex-direction: column; gap: 16px; }
.acct-summary { padding: 12px 16px; background: #f8fafc; border: 1px solid var(--cy-line); border-radius: 8px; display: flex; flex-direction: column; gap: 8px; }
.acct-row { display: flex; justify-content: space-between; align-items: center; gap: 12px; font-size: 14px; color: #4b5e6c; }
.acct-row strong { color: #20323f; font-weight: 600; }
.acct-section h3 { margin: 0 0 12px; font-size: 15px; font-weight: 600; color: #20323f; }
.acct-inline { display: flex; gap: 8px; width: 100%; }
.acct-actions { display: flex; justify-content: flex-end; gap: 8px; padding-top: 4px; }
.account-role-badge { display: inline-flex; align-items: center; padding: 2px 10px; border-radius: 999px; background: #eef4ff; color: #245bdb; border: 1px solid #d6e4ff; font-size: 13px; font-weight: 600; line-height: 1.4; }
</style>
