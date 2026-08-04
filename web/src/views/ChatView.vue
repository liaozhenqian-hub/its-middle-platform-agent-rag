<script setup lang="ts">
import {
  ChatLineRound,
  Check,
  Clock,
  ArrowDown,
  ArrowRight,
  CircleCheck,
  CircleClose,
  Document,
  Loading,
  Management,
  Promotion,
  RefreshLeft,
  UserFilled,
} from "@element-plus/icons-vue";
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";

import CitationPanel from "@/components/chat/CitationPanel.vue";
import IdentityHeader from "@/components/identity/IdentityHeader.vue";
import { useChatStore, type ChatMessage, type ChatScope } from "@/stores/chat";
import { useHistoryStore } from "@/stores/history";
import type { Citation } from "@/types/api";
import { citationDisplayName } from "@/utils/citations";
import { renderMarkdown } from "@/utils/markdown";
import { isNearScrollBottom } from "@/utils/scroll";

const store = useChatStore();
const history = useHistoryStore();
const prompt = ref("");
const transcript = ref<HTMLElement | null>(null);
const selectedCitation = ref<Citation | null>(null);
const citationDrawerOpen = ref(false);
const followOutput = ref(true);
const historyExpanded = ref(true);
const recentConversations = computed(() => history.items.slice(0, 8));

const scopeOptions = computed(() =>
  store.spaces.flatMap((space) => [
    { knowledgeSpaceId: space.id, domainId: null, label: space.name },
    ...space.domains
      .slice()
      .sort((left, right) => left.sort_order - right.sort_order)
      .map((domain) => ({
        knowledgeSpaceId: space.id,
        domainId: domain.id,
        label: domain.name,
      })),
  ]),
);

const selectedScopeKey = computed({
  get: () => scopeKey(store.scope),
  set: (value: string) => {
    const option = scopeOptions.value.find((item) => scopeKey(item) === value);
    if (option) chooseScope(option);
  },
});

function scopeKey(scope: ChatScope | null): string {
  if (!scope) return "";
  return `${scope.knowledgeSpaceId}:${scope.domainId ?? "all"}`;
}

function chooseScope(scope: ChatScope) {
  store.selectScope(scope);
  selectedCitation.value = null;
  followOutput.value = true;
}

function openCitation(citation: Citation) {
  selectedCitation.value = citation;
  citationDrawerOpen.value = true;
}

function isStreamingAssistant(message: ChatMessage): boolean {
  return (
    store.streaming &&
    message.role === "assistant" &&
    message.id === store.messages.at(-1)?.id
  );
}

async function send() {
  const value = prompt.value;
  if (!value.trim() || !store.scope || store.streaming) return;
  prompt.value = "";
  followOutput.value = true;
  await store.sendMessage(value);
  await history.load("", 1);
}

async function rate(message: ChatMessage, rating: "positive" | "negative") {
  let reason = "";
  let reasonCode = "";
  if (rating === "negative") {
    try {
      const result = await ElMessageBox.prompt("请选择分类并可补充说明：不准确、没理解问题、引用不相关、重复追问、太慢、格式不好、其他", "负面反馈", {
        confirmButtonText: "提交",
        cancelButtonText: "取消",
        inputPlaceholder: "例如：不准确：接口字段不对",
        inputValue: "不准确：",
        inputType: "textarea",
        inputValidator: (value) => !value || value.length <= 1000 || "最多输入 1000 字",
      });
      reason = result.value;
      const label = reason.split(/[：:]/, 1)[0].trim();
      reasonCode = ({
        "不准确": "inaccurate", "没理解问题": "misunderstood",
        "引用不相关": "irrelevant_citation", "重复追问": "reasked",
        "太慢": "too_slow", "格式不好": "bad_format", "其他": "other",
      } as Record<string, string>)[label] || "other";
    } catch {
      return;
    }
  }
  try {
    await store.submitFeedback(message.id, rating, reason, reasonCode);
    ElMessage.success("反馈已记录");
  } catch {
    ElMessage.error("反馈提交失败");
  }
}

function resetConversation() {
  store.resetConversation();
  followOutput.value = true;
}

async function openConversation(conversationId: string) {
  const detail = await history.open(conversationId);
  if (!detail) return;
  store.restoreConversation(detail);
  selectedCitation.value = null;
  followOutput.value = true;
}

function handleTranscriptScroll() {
  if (transcript.value) followOutput.value = isNearScrollBottom(transcript.value);
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    void send();
  }
}

watch(
  () => [store.messages.length, store.messages.at(-1)?.content],
  async () => {
    await nextTick();
    if (
      followOutput.value &&
      transcript.value &&
      typeof transcript.value.scrollTo === "function"
    ) {
      transcript.value.scrollTo({ top: transcript.value.scrollHeight, behavior: "auto" });
    }
  },
);

onMounted(async () => {
  await Promise.all([
    store.restorePersistedConversation(),
    history.load("", 1),
  ]);
});
</script>

<template>
  <main class="chat-page">
    <header class="chat-header">
      <div class="brand-lockup">
        <span class="brand-mark"><ChatLineRound /></span>
        <div>
          <strong>中台知识工作台</strong>
          <span>{{ store.scope?.label || "知识范围" }}</span>
        </div>
      </div>
      <nav aria-label="主导航">
        <IdentityHeader />
        <router-link to="/memory">
          <el-tooltip content="我的记忆" placement="bottom">
            <el-button :icon="UserFilled" circle aria-label="我的记忆" />
          </el-tooltip>
        </router-link>
        <router-link to="/admin">
          <el-tooltip content="知识源管理" placement="bottom">
            <el-button :icon="Management" circle aria-label="知识源管理" />
          </el-tooltip>
        </router-link>
      </nav>
    </header>

    <div class="chat-mobile-scope">
      <el-select
        v-model="selectedScopeKey"
        :loading="store.spacesLoading"
        placeholder="知识范围"
        aria-label="知识范围"
      >
        <el-option
          v-for="option in scopeOptions"
          :key="scopeKey(option)"
          :label="option.label"
          :value="scopeKey(option)"
        />
      </el-select>
    </div>

    <div class="chat-layout">
      <aside class="scope-rail" aria-label="知识范围">
        <p class="rail-label">知识范围</p>
        <div v-if="store.spacesLoading" class="rail-loading">
          <el-icon class="is-loading"><Loading /></el-icon>
        </div>
        <template v-else>
          <button
            v-for="option in scopeOptions"
            :key="scopeKey(option)"
            type="button"
            class="scope-option"
            :class="{ 'scope-option--active': scopeKey(option) === selectedScopeKey }"
            @click="chooseScope(option)"
          >
            <span>{{ option.label }}</span>
            <el-icon v-if="scopeKey(option) === selectedScopeKey"><Check /></el-icon>
          </button>
        </template>
        <button
          v-if="store.messages.length"
          type="button"
          class="new-conversation"
          @click="resetConversation"
        >
          <el-icon><RefreshLeft /></el-icon>
          新对话
        </button>
        <section class="recent-history" aria-label="历史会话">
          <button
            type="button"
            class="recent-history__toggle"
            :aria-expanded="historyExpanded"
            @click="historyExpanded = !historyExpanded"
          >
            <span><el-icon><Clock /></el-icon>历史会话</span>
            <el-icon><ArrowDown v-if="historyExpanded" /><ArrowRight v-else /></el-icon>
          </button>
          <div v-if="historyExpanded" class="recent-history__body">
            <div v-if="history.loading" class="recent-history__loading">
              <el-icon class="is-loading"><Loading /></el-icon>
            </div>
            <button
              v-for="item in recentConversations"
              v-else
              :key="item.conversation_id"
              type="button"
              class="recent-history__item"
              :title="item.title"
              @click="openConversation(item.conversation_id)"
            >
              {{ item.title }}
            </button>
            <p v-if="!history.loading && !recentConversations.length" class="recent-history__empty">暂无历史会话</p>
          </div>
          <router-link v-if="historyExpanded" to="/history" class="recent-history__all">查看全部</router-link>
        </section>
      </aside>

      <section class="conversation-pane" aria-label="对话">
        <div
          ref="transcript"
          class="transcript"
          aria-live="polite"
          @scroll.passive="handleTranscriptScroll"
        >
          <div v-if="store.restoringConversation" class="conversation-empty" aria-label="正在恢复会话">
            <el-icon class="is-loading"><Loading /></el-icon>
            <p>正在恢复会话</p>
          </div>
          <div v-else-if="!store.messages.length" class="conversation-empty">
            <span class="conversation-empty__icon"><ChatLineRound /></span>
            <strong>{{ store.scope?.label || "中台" }}</strong>
            <p>等待问题</p>
          </div>

          <article
            v-for="message in store.messages"
            :key="message.id"
            class="message"
            :class="`message--${message.role}`"
          >
            <div class="message__author">{{ message.role === "user" ? "你" : message.agentName || (isStreamingAssistant(message) ? store.activeAgent : "") || "知识助手" }}</div>
            <div
              class="message__body"
              :class="{ 'message__body--streaming': isStreamingAssistant(message) }"
              v-html="renderMarkdown(message.content)"
            />
            <span
              v-if="isStreamingAssistant(message) && !message.content"
              class="message__typing"
              aria-label="正在生成回答"
            ><i /><i /><i /></span>
            <div v-if="message.citations.length" class="message__citations">
              <button
                v-for="citation in message.citations"
                :key="`${citation.source_type}-${citation.source_id}`"
                type="button"
                @click="openCitation(citation)"
              >
                <el-icon><Document /></el-icon>
                {{ citationDisplayName(citation) }}
              </button>
            </div>
            <div
              v-if="message.role === 'assistant' && message.qualityTurnId && !isStreamingAssistant(message)"
              class="message__feedback"
              aria-label="回答反馈"
            >
              <span>这条回答有帮助吗</span>
              <el-tooltip content="有帮助">
                <el-button
                  :icon="CircleCheck"
                  circle
                  size="small"
                  :loading="message.feedbackLoading"
                  :type="message.feedbackRating === 'positive' ? 'success' : ''"
                  :plain="message.feedbackRating !== 'positive'"
                  aria-label="有帮助"
                  @click="rate(message, 'positive')"
                />
              </el-tooltip>
              <el-tooltip content="不准确">
                <el-button
                  :icon="CircleClose"
                  circle
                  size="small"
                  :loading="message.feedbackLoading"
                  :type="message.feedbackRating === 'negative' ? 'danger' : ''"
                  :plain="message.feedbackRating !== 'negative'"
                  aria-label="不准确"
                  @click="rate(message, 'negative')"
                />
              </el-tooltip>
            </div>
          </article>

          <div v-if="store.activeTools.length && store.streaming" class="tool-progress">
            <span v-for="tool in store.activeTools" :key="tool.id">
              <el-icon :class="{ 'is-loading': tool.status === 'running' }">
                <Loading v-if="tool.status === 'running'" />
                <Check v-else />
              </el-icon>
              {{ tool.name }}
            </span>
          </div>
        </div>

        <div v-if="store.error" class="chat-error" role="alert">{{ store.error }}</div>
        <form class="composer" @submit.prevent="send">
          <el-input
            v-model="prompt"
            type="textarea"
            :autosize="{ minRows: 1, maxRows: 5 }"
            resize="none"
            placeholder="输入问题"
            :disabled="!store.scope || store.streaming"
            aria-label="问题"
            @keydown="handleKeydown"
          />
          <el-tooltip content="发送" placement="top">
            <el-button
              type="primary"
              :icon="Promotion"
              native-type="submit"
              :loading="store.streaming"
              :disabled="!prompt.trim() || !store.scope"
              circle
              aria-label="发送"
            />
          </el-tooltip>
        </form>
      </section>

      <CitationPanel class="desktop-citations" :citation="selectedCitation" />
    </div>

    <el-drawer v-model="citationDrawerOpen" title="引用详情" size="88%" class="mobile-citations">
      <CitationPanel :citation="selectedCitation" />
    </el-drawer>
  </main>
</template>

<style scoped>
.chat-page {
  height: 100dvh;
  min-height: 600px;
  background: var(--surface-base);
  overflow: hidden;
}

.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 64px;
  padding: 0 20px;
  background: var(--surface-raised);
  border-bottom: 1px solid var(--border-subtle);
}

.chat-header nav {
  display: flex;
  gap: 8px;
}

.brand-lockup {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 11px;
}

.brand-mark,
.conversation-empty__icon {
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  width: 34px;
  height: 34px;
  color: #fff;
  background: var(--accent-strong);
  border-radius: 7px;
}

.brand-mark svg,
.conversation-empty__icon svg {
  width: 19px;
}

.brand-lockup div {
  display: grid;
  min-width: 0;
}

.brand-lockup strong {
  color: var(--text-primary);
  font-size: 15px;
  letter-spacing: 0;
}

.brand-lockup span:last-child {
  color: var(--text-muted);
  font-size: 12px;
}

.chat-layout {
  display: grid;
  grid-template-columns: 216px minmax(420px, 1fr) 300px;
  height: calc(100dvh - 64px);
}

.scope-rail {
  display: flex;
  flex-direction: column;
  padding: 20px 12px;
  background: var(--surface-soft);
  border-right: 1px solid var(--border-subtle);
  min-height: 0;
}

.rail-label {
  margin: 0 8px 10px;
  color: var(--text-muted);
  font-size: 11px;
  font-weight: 700;
}

.rail-loading {
  padding: 18px;
  color: var(--text-muted);
  text-align: center;
}

.scope-option,
.new-conversation {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  min-height: 38px;
  padding: 8px 10px;
  color: var(--text-secondary);
  background: transparent;
  border: 0;
  border-radius: 6px;
  font: inherit;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}

.scope-option:hover,
.new-conversation:hover {
  background: var(--surface-hover);
}

.scope-option--active {
  color: var(--accent-strong);
  background: var(--accent-soft);
  font-weight: 700;
}

.new-conversation {
  justify-content: flex-start;
  gap: 8px;
  margin-top: 0;
  border-top: 1px solid var(--border-subtle);
  border-radius: 0;
}

.recent-history {
  flex: 0 0 auto;
  min-height: 44px;
  margin-top: auto;
  padding-top: 8px;
  border-top: 1px solid var(--border-subtle);
}

.recent-history__toggle,
.recent-history__item {
  width: 100%;
  color: var(--text-secondary);
  background: transparent;
  border: 0;
  font: inherit;
  cursor: pointer;
}

.recent-history__toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 34px;
  padding: 6px 8px;
  font-size: 12px;
  font-weight: 700;
}

.recent-history__toggle span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.recent-history__body {
  max-height: min(240px, 28vh);
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
}

.recent-history__item {
  display: block;
  min-height: 32px;
  padding: 6px 8px;
  overflow: hidden;
  border-radius: 5px;
  font-size: 12px;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.recent-history__item:hover {
  background: var(--surface-hover);
}

.recent-history__loading,
.recent-history__empty {
  margin: 0;
  padding: 10px 8px;
  color: var(--text-muted);
  font-size: 11px;
  text-align: center;
}

.recent-history__all {
  display: block;
  padding: 7px 8px 2px;
  color: var(--accent-strong);
  font-size: 11px;
  text-decoration: none;
}

.conversation-pane {
  display: grid;
  grid-template-rows: minmax(0, 1fr) auto auto;
  height: 100%;
  min-height: 0;
  min-width: 0;
  background: var(--surface-base);
  overflow: hidden;
}

.transcript {
  min-height: 0;
  padding: 24px clamp(20px, 4vw, 64px);
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
}

.conversation-empty {
  display: grid;
  justify-items: center;
  align-content: center;
  min-height: 100%;
  color: var(--text-muted);
}

.conversation-empty strong {
  margin-top: 14px;
  color: var(--text-primary);
  font-size: 16px;
}

.conversation-empty p {
  margin: 4px 0;
  font-size: 12px;
}

.message {
  max-width: 840px;
  margin: 0 auto 24px;
}

.message__author {
  margin-bottom: 7px;
  color: var(--text-muted);
  font-size: 11px;
  font-weight: 700;
}

.message__body {
  color: var(--text-primary);
  font-size: 14px;
  line-height: 1.75;
  white-space: normal;
  overflow-wrap: anywhere;
}

.message__body--streaming:not(:empty)::after {
  display: inline-block;
  width: 2px;
  height: 1em;
  margin-left: 3px;
  vertical-align: -0.12em;
  background: var(--accent-strong);
  content: "";
  animation: caret-blink 0.9s steps(1) infinite;
}

.message__typing {
  display: inline-flex;
  align-items: center;
  width: 32px;
  height: 22px;
  gap: 4px;
}

.message__typing i {
  width: 5px;
  height: 5px;
  background: var(--text-muted);
  border-radius: 50%;
  animation: typing-pulse 1.1s ease-in-out infinite;
}

.message__typing i:nth-child(2) {
  animation-delay: 0.14s;
}

.message__typing i:nth-child(3) {
  animation-delay: 0.28s;
}

.message__body :deep(p) {
  margin: 0 0 10px;
}

.message__body :deep(p:last-child) {
  margin-bottom: 0;
}

.message__body :deep(h1),
.message__body :deep(h2),
.message__body :deep(h3),
.message__body :deep(h4) {
  margin: 20px 0 8px;
  color: var(--text-primary);
  line-height: 1.45;
  letter-spacing: 0;
}

.message__body :deep(h1) {
  font-size: 20px;
}

.message__body :deep(h2) {
  padding-bottom: 6px;
  border-bottom: 1px solid var(--border-subtle);
  font-size: 17px;
}

.message__body :deep(h3),
.message__body :deep(h4) {
  font-size: 15px;
}

.message__body :deep(h1:first-child),
.message__body :deep(h2:first-child),
.message__body :deep(h3:first-child) {
  margin-top: 0;
}

.message__body :deep(ul),
.message__body :deep(ol) {
  margin: 8px 0 12px;
  padding-left: 22px;
}

.message__body :deep(li) {
  margin: 4px 0;
  padding-left: 2px;
}

.message__body :deep(code) {
  padding: 2px 5px;
  color: #9c2f2f;
  background: #f4f5f7;
  border: 1px solid #e6e8eb;
  border-radius: 4px;
  font-family: Consolas, "Courier New", monospace;
  font-size: 12px;
}

.message__body :deep(pre) {
  max-width: 100%;
  margin: 10px 0 14px;
  padding: 12px 14px;
  overflow-x: auto;
  color: #e7edf4;
  background: #20262e;
  border: 1px solid #343c46;
  border-radius: 6px;
  line-height: 1.6;
}

.message__body :deep(pre code) {
  padding: 0;
  color: inherit;
  background: transparent;
  border: 0;
}

.message__body :deep(strong) {
  font-weight: 700;
}

.message__body :deep(a) {
  color: var(--accent-strong);
  text-decoration: underline;
  text-underline-offset: 3px;
}

.message__body :deep(hr) {
  height: 1px;
  margin: 18px 0;
  background: var(--border-subtle);
  border: 0;
}

.message__body :deep(blockquote) {
  margin: 12px 0;
  padding: 8px 12px;
  color: var(--text-secondary);
  background: var(--surface-soft);
  border-left: 3px solid var(--accent-strong);
}

.message__body :deep(blockquote p) {
  margin: 0;
}

.message__body :deep(.markdown-table) {
  max-width: 100%;
  margin: 12px 0 16px;
  overflow-x: auto;
  border: 1px solid var(--border-strong);
  border-radius: 6px;
  scrollbar-gutter: stable;
}

.message__body :deep(table) {
  width: 100%;
  min-width: 620px;
  border-collapse: collapse;
  background: var(--surface-raised);
  font-size: 13px;
  line-height: 1.55;
}

.message__body :deep(th),
.message__body :deep(td) {
  padding: 9px 11px;
  vertical-align: top;
  text-align: left;
  border-right: 1px solid var(--border-subtle);
  border-bottom: 1px solid var(--border-subtle);
}

.message__body :deep(th:last-child),
.message__body :deep(td:last-child) {
  border-right: 0;
}

.message__body :deep(tr:last-child td) {
  border-bottom: 0;
}

.message__body :deep(th) {
  color: var(--text-primary);
  background: var(--surface-soft);
  font-weight: 700;
}

.message__body :deep(tbody tr:nth-child(even)) {
  background: #fafbfc;
}

@keyframes caret-blink {
  50% {
    opacity: 0;
  }
}

@keyframes typing-pulse {
  0%,
  60%,
  100% {
    opacity: 0.35;
    transform: translateY(0);
  }
  30% {
    opacity: 1;
    transform: translateY(-2px);
  }
}

.message--user .message__body {
  width: fit-content;
  max-width: min(680px, 88%);
  margin-left: auto;
  padding: 10px 13px;
  background: #e9f2ff;
  border: 1px solid #d6e6fb;
  border-radius: 8px;
}

.message--user .message__author {
  text-align: right;
}

.message__citations {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 12px;
}

.message__citations button {
  display: inline-flex;
  align-items: center;
  max-width: 100%;
  min-height: 30px;
  gap: 5px;
  padding: 5px 8px;
  color: var(--text-secondary);
  background: var(--surface-raised);
  border: 1px solid var(--border-strong);
  border-radius: 4px;
  font: inherit;
  font-size: 11px;
  cursor: pointer;
}

.message__feedback {
  display: flex;
  align-items: center;
  min-height: 32px;
  gap: 6px;
  margin-top: 10px;
  color: var(--text-muted);
  font-size: 11px;
}

.message__feedback span {
  margin-right: 2px;
}

.tool-progress {
  display: flex;
  flex-wrap: wrap;
  max-width: 760px;
  gap: 8px;
  margin: -12px auto 20px;
  color: var(--text-muted);
  font-size: 11px;
}

.tool-progress span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.chat-error {
  padding: 8px 24px;
  color: var(--danger-strong);
  background: var(--danger-soft);
  font-size: 12px;
  text-align: center;
}

.composer {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 40px;
  align-items: end;
  gap: 10px;
  width: min(840px, calc(100% - 40px));
  margin: 0 auto;
  padding: 14px 0 18px;
  border-top: 1px solid var(--border-subtle);
}

.composer :deep(.el-textarea__inner) {
  min-height: 40px !important;
  padding: 10px 12px;
  border-radius: 6px;
  box-shadow: 0 0 0 1px var(--border-strong) inset;
}

.chat-mobile-scope,
.mobile-citations {
  display: none;
}

@media (max-width: 1080px) {
  .chat-layout {
    grid-template-columns: 190px minmax(380px, 1fr) 260px;
  }
}

@media (max-width: 820px) {
  .chat-page {
    min-height: 500px;
  }

  .chat-header {
    height: 58px;
    padding: 0 14px;
  }

  .chat-mobile-scope {
    display: block;
    height: 52px;
    padding: 8px 14px;
    background: var(--surface-raised);
    border-bottom: 1px solid var(--border-subtle);
  }

  .chat-mobile-scope :deep(.el-select) {
    width: 100%;
  }

  .chat-layout {
    display: block;
    height: calc(100dvh - 110px);
  }

  .scope-rail,
  .desktop-citations {
    display: none;
  }

  .conversation-pane {
    height: 100%;
  }

  .transcript {
    padding: 18px 16px;
  }

  .composer {
    width: calc(100% - 28px);
    padding-bottom: calc(12px + env(safe-area-inset-bottom));
  }

  .mobile-citations {
    display: block;
  }

  :deep(.mobile-citations .el-drawer__body) {
    padding: 0;
  }

  :deep(.mobile-citations.el-drawer) {
    max-width: 380px;
  }

  :deep(.mobile-citations .citation-panel) {
    border-left: 0;
  }
}
</style>
