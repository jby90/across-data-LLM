<script setup lang="ts">
import { userName } from '@cyshuhe/shared'
import { ElMessage } from 'element-plus'
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import AppIcon from '../components/AppIcon.vue'
import NotificationBell from '../components/NotificationBell.vue'
import PaperWatcher from '../components/PaperWatcher.vue'
import TermPopover from '../components/TermPopover.vue'
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
  is_trial?: boolean
}

const auth = useAuth()
const route = useRoute()
const router = useRouter()
const navOpen = ref(false)
const fromDocuments = computed(() => {
  const back = typeof route.query.back === 'string' ? route.query.back : ''
  if (back.startsWith('/documents')) return true
  if (back.startsWith('/admission') || back === '/forms') return false
  if (typeof route.query.task_id === 'string' && route.query.task_id && !route.query.project) return true
  return false
})
const fromAdmission = computed(() => {
  const back = typeof route.query.back === 'string' ? route.query.back : ''
  return back.startsWith('/admission') || Boolean(route.query.project)
})
const isActive = (path: string) =>
  route.path === path ||
  (path === '/documents' && (route.path === '/analysis' || (route.path.startsWith('/forms') && fromDocuments.value))) ||
  (path === '/admission' && route.path.startsWith('/forms') && !fromDocuments.value)

const breadcrumbParent = computed<{ to: string; title: string } | null>(() => {
  if (route.path.startsWith('/forms')) {
    if (fromDocuments.value) return { to: '/documents', title: '合规评估' }
    if (fromAdmission.value) return { to: '/admission', title: '海外属地准入' }
    return null
  }
  if (route.path === '/admission' && route.query.tab === 'forms') {
    return { to: '/admission?tab=prepare', title: '海外属地准入' }
  }
  return null
})
const breadcrumbTitle = computed(() => {
  if (route.path.startsWith('/forms')) return '法定申报表'
  if (route.path === '/admission' && route.query.tab === 'forms') return '法定申报表'
  return current.value?.title || '工作概览'
})
const groups = [
  {
    label: '工作空间',
    items: [
      { path: '/', title: '工作概览', icon: 'grid' },
      { path: '/knowledge', title: '法规知识库', icon: 'book' },
      { path: '/documents', title: '合规评估', icon: 'shield' },
      { path: '/admission', title: '海外属地准入', icon: 'flow' },
      { path: '/notifications', title: '更新通知', icon: 'check' },
    ],
  },
  {
    label: '帮助',
    items: [{ path: '/help', title: '使用帮助', icon: 'book' }],
  },
]
const isPortalTrial = computed(() => auth.user?.signup_source === 'portal' && auth.user?.role === 'viewer')
const navigation = computed(() =>
  isPortalTrial.value
    ? [
        {
          label: '独立体验空间',
          items: [
            { path: '/', title: '工作概览', icon: 'grid' },
            { path: '/knowledge', title: '法规知识库', icon: 'book' },
            { path: '/analysis', title: '法规咨询助手', icon: 'shield' },
            { path: '/notifications', title: '更新通知', icon: 'check' },
          ],
        },
        {
          label: '帮助',
          items: [{ path: '/help', title: '使用帮助', icon: 'book' }],
        },
      ]
    : groups,
)
const current = computed(() =>
  navigation.value.flatMap((group) => group.items).find((item) => isActive(item.path)),
)
watch(
  () => route.path,
  () => {
    navOpen.value = false
  },
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
            :class="{ 'is-active': isActive(item.path) }"
            :aria-current="isActive(item.path) ? 'page' : undefined"
            ><AppIcon :name="item.icon" /><span>{{ item.title }}</span></RouterLink
          >
        </section>
      </nav>
      <div class="sidebar-footer"><strong>用户工作台</strong>汽车数据跨境合规平台</div>
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
          ><RouterLink v-if="route.path !== '/'" to="/" class="topbar-crumb-home">用户工作台</RouterLink
          ><span v-else>用户工作台</span><span class="topbar-divider">/</span
          ><template v-if="breadcrumbParent"
            ><RouterLink :to="breadcrumbParent.to" class="topbar-crumb-home">{{ breadcrumbParent.title }}</RouterLink
            ><span class="topbar-divider">/</span></template
          ><strong>{{ breadcrumbTitle }}</strong>
        </div>
        <div class="topbar-account" v-if="auth.user">
          <NotificationBell />
          <PaperWatcher />
          <el-tooltip
            v-if="auth.user.role === 'viewer'"
            :content="
              auth.user.signup_source === 'portal' && auth.user.role === 'viewer'
                ? '独立体验空间账号，可查阅法规知识库、标准目录与咨询助手'
                : auth.user.can_export
                  ? '可只读查看并下载本单位项目文件，不能新建或修改'
                  : '可只读查看本单位项目，不能新建、修改或下载文件'
            "
            placement="bottom"
            ><span class="viewer-tag" data-testid="viewer-tag">只读账号</span></el-tooltip
          ><el-dropdown trigger="click"
            ><button type="button" class="account-pill" aria-label="账号操作" data-testid="account-menu-trigger"
              ><AppIcon name="user" class="account-pill__icon" /><span class="account-pill__name">{{ userName(auth.user) }}</span><AppIcon name="chevron-down" class="account-pill__chevron" /></button
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

    <el-dialog v-model="accountDialog.open" title="账号设置" width="min(500px, 94vw)" destroy-on-close>
      <div v-loading="accountDialog.loading" class="acct-dialog" data-testid="account-settings-dialog">
        <div v-if="accountDialog.info" class="acct-summary">
          <div class="acct-row"><span>登录邮箱</span><strong>{{ accountDialog.info.email }}</strong></div>
          <div class="acct-row"><span>所属单位</span><strong>{{ accountDialog.info.org_name || '未绑定单位' }}</strong></div>
          <div class="acct-row">
            <span>账号角色</span>
            <strong>{{ accountDialog.info.role_zh }}</strong>
          </div>
          <div class="acct-row">
            <span>文书导出权限</span>
            <span>{{ accountDialog.info.can_export ? '已开启' : '未开启' }}</span>
          </div>
          <p v-if="accountDialog.info.role === 'viewer'" class="acct-upgrade-note">
            <template v-if="accountDialog.info.is_trial ?? accountDialog.info.signup_source === 'portal'">
              当前为独立体验空间账号，可查阅法规知识库、标准目录与咨询助手；如需办理企业合规评估与准入材料，请联系平台超级管理员开通企业用户权限。
            </template>
            <template v-else-if="accountDialog.info.can_export">
              当前账号为普通查看者（已开启文件下载权限），可只读查阅并下载本单位合规评估与属地准入材料，也可查阅法规知识库、标准目录并在咨询助手发起个人私密问答；如需新建或修改项目，请联系超级管理员开通企业用户权限。
            </template>
            <template v-else>
              当前账号为普通查看者（未开启文件下载权限），可只读查阅本单位合规评估与属地准入材料（不可下载正式文件），也可查阅法规知识库、标准目录并在咨询助手发起个人私密问答；如需开启文件下载或开通企业用户权限，请联系超级管理员办理。
            </template>
          </p>
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
    <TermPopover />
  </div>
</template>
<style scoped>
.skip-link {
  position: fixed;
  left: 250px;
  top: -60px;
  z-index: 120;
  padding: 10px 18px;
  background: white;
  border: 2px solid var(--cy-blue);
  border-radius: 8px;
}
.skip-link:focus {
  top: 10px;
}
@media (max-width: 800px) {
  .app-shell.is-nav-open .app-sidebar {
    z-index: 110;
  }
  .nav-scrim {
    z-index: 100;
  }
}
.viewer-tag { margin-left: 8px; font-size: 13px; padding: 2px 8px; border-radius: 10px; color: #475569; background: #f1f5f9; cursor: default; }
.acct-dialog { display: flex; flex-direction: column; gap: 16px; padding-bottom: 8px; }
.acct-summary { padding: 12px 16px; background: #f8fafc; border: 1px solid var(--cy-line); border-radius: 8px; display: flex; flex-direction: column; gap: 8px; }
.acct-row { display: flex; justify-content: space-between; align-items: center; gap: 12px; font-size: 14px; color: #4b5e6c; }
.acct-row strong { color: #20323f; font-weight: 600; }
.acct-upgrade-note { margin: 4px 0 0; padding-top: 8px; border-top: 1px solid var(--cy-line); font-size: 13px; line-height: 1.6; color: #4b5e6c; }
.acct-section h3 { margin: 0 0 12px; font-size: 15px; font-weight: 600; color: #20323f; }
.acct-inline { display: flex; gap: 8px; width: 100%; }
.acct-actions { display: flex; justify-content: flex-end; gap: 8px; padding: 10px 0 16px; }
</style>
