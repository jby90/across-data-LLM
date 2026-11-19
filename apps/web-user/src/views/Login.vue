<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuth } from '../auth'
import AppIcon from '../components/AppIcon.vue'

const route = useRoute()
const router = useRouter()
const auth = useAuth()
const mode = ref<'login' | 'register'>('login')
const form = reactive({ email: '', password: '' })
const regForm = reactive({ display_name: '', org_name: '', email: '', password: '' })
const loading = ref(false)

// 管理端地址：生产环境用 VITE_ADMIN_URL 配置；开发环境默认同主机的 5175 端口
const adminUrl: string =
  import.meta.env.VITE_ADMIN_URL ??
  (import.meta.env.DEV ? `${location.protocol}//${location.hostname}:5175` : '')

onMounted(async () => {
  const ticket = typeof route.query.portal_ticket === 'string' ? route.query.portal_ticket.trim() : ''
  if (!ticket) return
  loading.value = true
  try {
    await auth.exchangePortalTicket(ticket)
    ElMessage.success('已通过门户账号登录')
    await router.replace({ name: 'home' })
  } catch (e) {
    ElMessage.error((e as Error).message || '门户登录已失效，请使用账号密码登录')
  } finally {
    loading.value = false
  }
})

async function submit() {
  if (loading.value) return
  if (mode.value === 'register') {
    if (!regForm.display_name.trim() || !regForm.org_name.trim() || !regForm.email.trim() || !regForm.password) {
      ElMessage.warning('请完整填写姓名、单位全称、邮箱和密码')
      return
    }
    if (regForm.password.length < 12) {
      ElMessage.warning('密码长度至少 12 位')
      return
    }
    loading.value = true
    try {
      await auth.register({
        display_name: regForm.display_name.trim(),
        org_name: regForm.org_name.trim(),
        email: regForm.email.trim(),
        password: regForm.password,
      })
      ElMessage.success('体验账号已开通，可在线查阅法规知识库、标准目录与咨询助手')
      await router.replace({ name: 'home' })
    } catch (e) {
      ElMessage.error((e as Error).message)
    } finally {
      loading.value = false
    }
    return
  }
  if (!form.email.trim() || !form.password) {
    ElMessage.warning('请输入邮箱和密码')
    return
  }
  loading.value = true
  try {
    await auth.login(form.email.trim(), form.password)
    await router.replace({ name: 'home' })
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    loading.value = false
  }
}
</script>
<template>
  <div class="login-page">
    <section class="login-story">
      <div class="app-brand">
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
          <div class="brand-caption">汽车数据跨境合规平台</div>
        </div>
      </div>
      <div class="login-story-copy">
        <div class="eyebrow">全球汽车数据合规服务</div>
        <h1>让数据流动，<br />让决策有据。</h1>
        <p>连接全球法规、业务场景与合规行动，<br />让每一次跨境决策，都有清晰的依据。</p>
      </div>
      <svg class="login-art" viewBox="0 0 600 600" fill="none" aria-hidden="true">
        <circle cx="300" cy="300" r="270" stroke="currentColor" />
        <circle cx="300" cy="300" r="220" stroke="currentColor" />
        <ellipse cx="300" cy="300" rx="120" ry="270" stroke="currentColor" />
        <ellipse cx="300" cy="300" rx="270" ry="100" stroke="currentColor" />
        <path d="M30 300h540 M300 30v540 M105 105l390 390 M105 495l390-390" stroke="currentColor" />
        <circle cx="300" cy="30" r="8" fill="currentColor" />
        <circle cx="570" cy="300" r="8" fill="currentColor" />
      </svg>
      <div class="login-story-footer">法规知识库 · 合规评估 · 海外属地准入</div>
    </section>
    <main class="login-main">
      <div class="login-form">
        <div class="eyebrow">用户工作台</div>
        <div class="mode-tabs" role="tablist" aria-label="登录或注册">
          <button
            type="button"
            class="mode-tab"
            :class="{ 'is-active': mode === 'login' }"
            data-testid="tab-user-login"
            @click="mode = 'login'"
          >
            账号登录
          </button>
          <button
            type="button"
            class="mode-tab"
            :class="{ 'is-active': mode === 'register' }"
            data-testid="tab-user-register"
            @click="mode = 'register'"
          >
            注册体验账号
          </button>
        </div>

        <template v-if="mode === 'login'">
          <h2>欢迎回到车研数合</h2>
          <p class="login-description">登录企业或个人体验账号，查阅全球法规与办理合规事项。</p>
          <el-form label-position="top" @submit.prevent="submit">
            <el-form-item label="邮箱"
              ><el-input
                v-model="form.email"
                type="email"
                autocomplete="username"
                placeholder="请输入登录邮箱"
                aria-label="邮箱"
                ><template #prefix><AppIcon name="mail" /></template></el-input
            ></el-form-item>
            <el-form-item label="密码"
              ><el-input
                v-model="form.password"
                type="password"
                show-password
                autocomplete="current-password"
                placeholder="请输入密码"
                aria-label="密码"
                ><template #prefix><AppIcon name="lock" /></template></el-input
            ></el-form-item>
            <el-button class="login-submit" type="primary" native-type="submit" :loading="loading"
              >登录工作台<AppIcon name="arrow" style="margin: 0 0 0 10px"
            /></el-button>
          </el-form>
        </template>

        <template v-else>
          <h2>注册平台体验账号</h2>
          <p class="login-description">
            注册后可查阅法规知识库、标准目录、国家合规简报与咨询助手；如需办理企业合规评估与准入材料，请联系超级管理员开通企业用户权限。
          </p>
          <el-form label-position="top" @submit.prevent="submit">
            <el-form-item label="姓名">
              <el-input
                v-model="regForm.display_name"
                placeholder="请输入真实姓名"
                aria-label="姓名"
                data-testid="register-name"
              />
            </el-form-item>
            <el-form-item label="单位全称">
              <el-input
                v-model="regForm.org_name"
                placeholder="请输入所属企业或科研机构全称"
                aria-label="单位全称"
                data-testid="register-org"
              />
            </el-form-item>
            <el-form-item label="登录邮箱">
              <el-input
                v-model="regForm.email"
                type="email"
                autocomplete="email"
                placeholder="请输入工作或常用邮箱"
                aria-label="登录邮箱"
                data-testid="register-email"
              >
                <template #prefix><AppIcon name="mail" /></template>
              </el-input>
            </el-form-item>
            <el-form-item label="设置密码（至少 12 位）">
              <el-input
                v-model="regForm.password"
                type="password"
                show-password
                autocomplete="new-password"
                placeholder="请设置至少 12 位登录密码"
                aria-label="设置密码"
                data-testid="register-password"
              >
                <template #prefix><AppIcon name="lock" /></template>
              </el-input>
            </el-form-item>
            <el-button
              class="login-submit"
              type="primary"
              native-type="submit"
              :loading="loading"
              data-testid="register-submit"
            >
              注册并进入工作台<AppIcon name="arrow" style="margin: 0 0 0 10px" />
            </el-button>
          </el-form>
        </template>

        <div class="login-switch">
          超级管理员或运维管理员？<template v-if="adminUrl"
            ><a :href="adminUrl" data-testid="switch-to-admin">前往管理端<AppIcon name="arrow" /></a
          ></template>
        </div>
      </div>
    </main>
  </div>
</template>

<style scoped>
.mode-tabs {
  display: inline-flex;
  gap: 4px;
  padding: 4px;
  margin-bottom: 16px;
  background: #f1f5f9;
  border-radius: 8px;
}
.mode-tab {
  border: 0;
  background: transparent;
  color: #475569;
  font-size: 14px;
  font-weight: 400;
  padding: 6px 14px;
  border-radius: 6px;
  cursor: pointer;
}
.mode-tab.is-active {
  background: #ffffff;
  color: #0f172a;
  font-weight: 600;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06);
}
</style>
