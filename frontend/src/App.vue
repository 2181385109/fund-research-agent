<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, RouterView, useRouter } from 'vue-router'
import { auth, logout } from './stores/auth'

const router = useRouter()
const loggedIn = computed(() => !!auth.token)

function onLogout() {
  logout()
  void router.push({ name: 'login' })
}
</script>

<template>
  <div class="app">
    <header v-if="loggedIn" class="topbar">
      <div class="brand">基金投研助手</div>
      <nav>
        <RouterLink to="/chat">对话</RouterLink>
        <RouterLink to="/kb">知识库</RouterLink>
      </nav>
      <div class="spacer" />
      <span class="who">{{ auth.username }}</span>
      <button class="link" type="button" @click="onLogout">退出</button>
    </header>
    <main class="main">
      <RouterView />
    </main>
  </div>
</template>
