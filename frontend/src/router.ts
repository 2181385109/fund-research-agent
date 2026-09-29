import { createRouter, createWebHistory } from 'vue-router'
import { auth } from './stores/auth'
import ChatView from './views/ChatView.vue'
import KbView from './views/KbView.vue'
import LoginView from './views/LoginView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: LoginView, meta: { public: true } },
    { path: '/', redirect: '/chat' },
    { path: '/chat/:id?', name: 'chat', component: ChatView },
    { path: '/kb', name: 'kb', component: KbView },
    { path: '/:pathMatch(.*)*', redirect: '/chat' },
  ],
})

router.beforeEach((to) => {
  if (!to.meta.public && !auth.token) return { name: 'login', query: { next: to.fullPath } }
  if (to.name === 'login' && auth.token) return { name: 'chat' }
  return true
})
