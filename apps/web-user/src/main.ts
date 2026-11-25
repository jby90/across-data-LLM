import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import { createPinia } from 'pinia'
import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import './style.css'

if (typeof window !== 'undefined') {
  window.addEventListener('vite:preloadError', (ev) => {
    ev.preventDefault()
    window.location.reload()
  })
}

createApp(App).use(createPinia()).use(router).use(ElementPlus, { locale: zhCn }).mount('#app')