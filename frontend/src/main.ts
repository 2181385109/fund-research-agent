import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router'
import { installUnauthorizedHandler } from './stores/auth'
import './style.css'

installUnauthorizedHandler(() => {
  void router.replace({ name: 'login', query: { next: router.currentRoute.value.fullPath } })
})

createApp(App).use(router).mount('#app')
