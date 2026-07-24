<script setup lang="ts">
import {
  ArrowLeft,
  ChatDotRound,
  Clock,
  Delete,
  Edit,
  Plus,
  Search,
} from "@element-plus/icons-vue";
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";

import IdentityHeader from "@/components/identity/IdentityHeader.vue";
import { useChatStore } from "@/stores/chat";
import { useHistoryStore } from "@/stores/history";
import { renderMarkdown } from "@/utils/markdown";

const history = useHistoryStore();
const chat = useChatStore();
const router = useRouter();
const query = ref("");

const pageCount = computed(() => Math.max(1, Math.ceil(history.total / history.pageSize)));

onMounted(async () => {
  await Promise.all([history.load(), chat.spaces.length ? Promise.resolve() : chat.loadSpaces()]);
});

async function search() {
  await history.load(query.value, 1);
}

async function open(conversationId: string) {
  await history.open(conversationId);
}

async function continueConversation() {
  if (!history.selected) return;
  chat.restoreConversation(history.selected);
  await router.push("/chat");
}

async function rename(conversationId: string, currentTitle: string) {
  try {
    const result = await ElMessageBox.prompt("输入便于查找的会话标题", "重命名会话", {
      inputValue: currentTitle,
      inputValidator: (value) => {
        const length = value.trim().length;
        return (length > 0 && length <= 100) || "标题需要在 1 到 100 个字符之间";
      },
      confirmButtonText: "保存",
      cancelButtonText: "取消",
    });
    await history.rename(conversationId, result.value);
    ElMessage.success("标题已更新");
  } catch {
    // Cancelling the dialog leaves the title unchanged.
  }
}

async function remove(conversationId: string, title: string) {
  try {
    await ElMessageBox.confirm(
      `删除“${title}”后将无法恢复。`,
      "删除历史会话",
      { type: "warning", confirmButtonText: "删除", cancelButtonText: "取消" },
    );
    await history.remove(conversationId);
    ElMessage.success("会话已删除");
  } catch {
    // Cancelling the dialog leaves the conversation unchanged.
  }
}

function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      }).format(date);
}
</script>

<template>
  <main class="history-page">
    <header class="history-header">
      <router-link to="/chat">
        <el-button :icon="ArrowLeft" circle aria-label="返回问答" />
      </router-link>
      <div class="history-heading">
        <h1>历史会话</h1>
        <p>仅展示当前身份发起的网页会话，长期记忆请在“我的记忆”中管理。</p>
      </div>
      <IdentityHeader />
      <el-button type="primary" :icon="Plus" @click="chat.resetConversation(); router.push('/chat')">
        新对话
      </el-button>
    </header>

    <el-alert v-if="history.error" :title="history.error" type="error" :closable="false" show-icon />

    <section class="history-layout">
      <aside class="history-list" aria-label="会话列表">
        <form class="history-search" @submit.prevent="search">
          <el-input v-model="query" clearable placeholder="搜索标题或最近内容" aria-label="搜索历史会话" />
          <el-button :icon="Search" circle :loading="history.loading" aria-label="搜索" native-type="submit" />
        </form>

        <div v-loading="history.loading" class="history-items">
          <el-empty v-if="!history.loading && !history.items.length" description="暂无历史会话" />
          <article
            v-for="item in history.items"
            :key="item.conversation_id"
            class="history-item"
            :class="{ 'history-item--active': history.selected?.conversation_id === item.conversation_id }"
          >
            <button class="history-item__main" type="button" @click="open(item.conversation_id)">
              <strong>{{ item.title }}</strong>
              <span>{{ item.preview }}</span>
              <small><el-icon><Clock /></el-icon>{{ formatTime(item.updated_at) }} · {{ item.message_count }} 条消息</small>
            </button>
            <div class="history-item__actions">
              <el-tooltip content="重命名">
                <el-button :icon="Edit" circle text size="small" aria-label="重命名" @click="rename(item.conversation_id, item.title)" />
              </el-tooltip>
              <el-tooltip content="删除">
                <el-button :icon="Delete" circle text type="danger" size="small" aria-label="删除" :loading="history.actionLoading === item.conversation_id" @click="remove(item.conversation_id, item.title)" />
              </el-tooltip>
            </div>
          </article>
        </div>

        <el-pagination
          v-if="pageCount > 1"
          small
          background
          layout="prev, pager, next"
          :page-count="pageCount"
          :current-page="history.page"
          @current-change="history.load(query, $event)"
        />
      </aside>

      <section v-loading="history.detailLoading" class="history-detail" aria-label="会话内容">
        <el-empty v-if="!history.selected" description="选择一条会话查看完整内容" />
        <template v-else>
          <div class="detail-header">
            <div>
              <h2>{{ history.selected.title }}</h2>
              <p>{{ history.selected.messages.length }} 条消息</p>
            </div>
            <el-button type="primary" :icon="ChatDotRound" @click="continueConversation">继续对话</el-button>
          </div>
          <div class="detail-transcript">
            <article
              v-for="message in history.selected.messages"
              :key="message.id"
              class="detail-message"
              :class="`detail-message--${message.role}`"
            >
              <strong>{{ message.role === "user" ? "你" : "知识助手" }}</strong>
              <div v-html="renderMarkdown(message.content)" />
              <small>{{ formatTime(message.created_at) }}</small>
            </article>
          </div>
        </template>
      </section>
    </section>
  </main>
</template>

<style scoped>
.history-page { width: min(1320px, calc(100% - 32px)); min-height: 100vh; margin: 0 auto; padding: 20px 0 36px; }
.history-header { display: grid; grid-template-columns: 40px minmax(0, 1fr) auto auto; align-items: center; gap: 14px; padding-bottom: 18px; border-bottom: 1px solid var(--border-subtle); }
.history-heading h1, .history-heading p, .detail-header h2, .detail-header p { margin: 0; }
.history-heading h1 { font-size: 22px; }
.history-heading p, .detail-header p { margin-top: 4px; color: var(--text-muted); font-size: 13px; }
.history-page > .el-alert { margin-top: 16px; }
.history-layout { display: grid; grid-template-columns: minmax(280px, 360px) minmax(0, 1fr); min-height: calc(100vh - 130px); margin-top: 18px; border: 1px solid var(--border-subtle); background: var(--surface-raised); }
.history-list { min-width: 0; border-right: 1px solid var(--border-subtle); background: var(--surface-base); }
.history-search { display: grid; grid-template-columns: minmax(0, 1fr) 36px; gap: 8px; padding: 14px; border-bottom: 1px solid var(--border-subtle); }
.history-items { min-height: 180px; }
.history-item { display: grid; grid-template-columns: minmax(0, 1fr) auto; border-bottom: 1px solid var(--border-subtle); }
.history-item:hover, .history-item--active { background: var(--surface-hover); }
.history-item--active { box-shadow: inset 3px 0 var(--accent-strong); }
.history-item__main { min-width: 0; padding: 14px 8px 14px 16px; border: 0; background: transparent; color: inherit; text-align: left; cursor: pointer; }
.history-item__main strong, .history-item__main span, .history-item__main small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.history-item__main strong { font-size: 14px; }
.history-item__main span { margin-top: 6px; color: var(--text-secondary); font-size: 13px; }
.history-item__main small { display: flex; align-items: center; gap: 5px; margin-top: 9px; color: var(--text-muted); font-size: 11px; }
.history-item__actions { display: flex; flex-direction: column; justify-content: center; padding-right: 6px; }
.history-list > .el-pagination { justify-content: center; padding: 14px; }
.history-detail { min-width: 0; min-height: 420px; }
.detail-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 18px 20px; border-bottom: 1px solid var(--border-subtle); }
.detail-header h2 { font-size: 17px; overflow-wrap: anywhere; }
.detail-transcript { height: calc(100vh - 220px); min-height: 360px; overflow-y: auto; padding: 20px; }
.detail-message { width: min(88%, 820px); margin-bottom: 18px; }
.detail-message--user { margin-left: auto; }
.detail-message > strong { display: block; margin-bottom: 6px; color: var(--text-muted); font-size: 12px; }
.detail-message > div { padding: 12px 14px; border: 1px solid var(--border-subtle); border-radius: 6px; background: var(--surface-soft); line-height: 1.7; overflow-wrap: anywhere; }
.detail-message--user > div { border-color: #cbdff3; background: var(--accent-soft); }
.detail-message small { display: block; margin-top: 5px; color: var(--text-muted); font-size: 11px; }
.detail-message--user small, .detail-message--user > strong { text-align: right; }
.detail-message :deep(p:first-child) { margin-top: 0; }
.detail-message :deep(p:last-child) { margin-bottom: 0; }
@media (max-width: 820px) {
  .history-page { width: calc(100% - 20px); padding-top: 12px; }
  .history-header { grid-template-columns: 36px minmax(0, 1fr); align-items: start; }
  .history-header > :nth-child(3), .history-header > :nth-child(4) { grid-column: 2; justify-self: end; }
  .history-layout { grid-template-columns: 1fr; }
  .history-list { border-right: 0; border-bottom: 1px solid var(--border-subtle); }
  .history-detail { min-height: 360px; }
  .detail-transcript { height: auto; max-height: 65vh; min-height: 300px; }
  .detail-message { width: 96%; }
}
</style>
