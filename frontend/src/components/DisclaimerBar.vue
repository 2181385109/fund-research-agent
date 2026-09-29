<script setup lang="ts">
import { FIXED_DISCLAIMER } from '../lib/constants'
// 风险提示：常驻显示，没有关闭 / 折叠入口（CLAUDE.md §2 红线 8）。
// 每条回答下面另外显示服务端随回答下发的 disclaimer 原文；这一条是页面级兜底，
// 保证即使某次请求在流开始之前就失败、没有收到 disclaimer 事件，页面上也始终有风险提示。
</script>

<template>
  <div class="disclaimer-bar" role="note" data-testid="disclaimer-bar">
    <strong>风险提示：</strong>{{ FIXED_DISCLAIMER }}
  </div>
</template>
