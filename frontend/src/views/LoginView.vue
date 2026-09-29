<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError } from '../api/http'
import { login, register } from '../stores/auth'

const router = useRouter()
const route = useRoute()

const mode = ref<'login' | 'register'>('login')
const username = ref('')
const password = ref('')
const busy = ref(false)
const error = ref('')

async function submit() {
  error.value = ''
  busy.value = true
  try {
    if (mode.value === 'login') await login(username.value.trim(), password.value)
    else await register(username.value.trim(), password.value)
    const next = typeof route.query.next === 'string' && route.query.next.startsWith('/') ? route.query.next : '/chat'
    await router.replace(next)
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : String(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <form class="card login" @submit.prevent="submit">
      <h1>基金投研助手</h1>
      <p class="muted">面向医药医疗与科技主题公募基金的披露文件与数据问答。</p>
      <label>
        用户名
        <input v-model="username" autocomplete="username" required minlength="3" maxlength="32" placeholder="3–32 位字母、数字、下划线" />
      </label>
      <label>
        密码
        <input
          v-model="password"
          type="password"
          :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
          required
          minlength="8"
          maxlength="72"
          placeholder="至少 8 位"
        />
      </label>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <button class="primary" type="submit" :disabled="busy">{{ mode === 'login' ? '登录' : '注册并登录' }}</button>
      <button class="link" type="button" @click="mode = mode === 'login' ? 'register' : 'login'">
        {{ mode === 'login' ? '没有账号？注册' : '已有账号？登录' }}
      </button>
      <p class="muted small">
        以上内容基于公开披露信息整理，仅供学习研究，不构成投资建议。基金有风险，投资需谨慎。
      </p>
    </form>
  </div>
</template>
