import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import { createPinia } from 'pinia'
import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import './style.css'

// 防抖包装浏览器 ResizeObserver，消除 Element Plus 表格在首屏挂载与尺寸重算时的 ResizeObserver 循环通知控制台告警
if (typeof window !== 'undefined' && typeof window.ResizeObserver === 'function') {
  const NativeResizeObserver = window.ResizeObserver
  class SafeResizeObserver extends NativeResizeObserver {
    private _raf = 0
    private _pending = new Map<Element, ResizeObserverEntry>()
    constructor(callback: ResizeObserverCallback) {
      super((entries, observer) => {
        for (const entry of entries) {
          this._pending.set(entry.target, entry)
        }
        if (this._raf) return
        this._raf = window.requestAnimationFrame(() => {
          this._raf = 0
          const batch = [...this._pending.values()]
          this._pending.clear()
          if (batch.length > 0) {
            callback(batch, observer)
          }
        })
      })
    }
    override unobserve(target: Element): void {
      this._pending.delete(target)
      super.unobserve(target)
    }
    override disconnect(): void {
      if (this._raf) {
        window.cancelAnimationFrame(this._raf)
        this._raf = 0
      }
      this._pending.clear()
      super.disconnect()
    }
  }
  window.ResizeObserver = SafeResizeObserver
}

createApp(App).use(createPinia()).use(router).use(ElementPlus, { locale: zhCn }).mount('#app')