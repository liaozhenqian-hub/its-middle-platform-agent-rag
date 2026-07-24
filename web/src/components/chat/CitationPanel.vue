<script setup lang="ts">
import { CopyDocument, Link } from "@element-plus/icons-vue";
import { computed, onBeforeUnmount, ref, watch } from "vue";

import { citationHasDetail, fetchCitationDetail } from "@/api/citations";
import type { Citation, CitationDetail } from "@/types/api";
import {
  citationDisplayName,
  citationLabel,
  citationPermalink,
  visibleCitationMetadata,
} from "@/utils/citations";
import { renderMarkdown } from "@/utils/markdown";

const props = defineProps<{ citation: Citation | null }>();
const detailCache = new Map<string, CitationDetail>();
const detail = ref<CitationDetail | null>(null);
const fullDetail = ref<CitationDetail | null>(null);
const showFull = ref(false);
const loading = ref(false);
const fullLoading = ref(false);
const error = ref<string | null>(null);
const copied = ref(false);
let controller: AbortController | null = null;
let copyTimer: ReturnType<typeof setTimeout> | null = null;

const effectiveCitation = computed<Citation | null>(() => {
  if (!props.citation) return null;
  const selectedDetail = displayedDetail.value;
  if (!selectedDetail) return props.citation;
  return {
    ...props.citation,
    title: selectedDetail.title || props.citation.title,
    domain: selectedDetail.domain || props.citation.domain,
    metadata: { ...props.citation.metadata, ...selectedDetail.metadata },
  };
});
const displayedDetail = computed(() =>
  showFull.value && fullDetail.value ? fullDetail.value : detail.value,
);
const permalink = computed(() =>
  effectiveCitation.value ? citationPermalink(effectiveCitation.value) : null,
);
const metadataRows = computed(() =>
  effectiveCitation.value ? visibleCitationMetadata(effectiveCitation.value) : [],
);
const isCode = computed(() => effectiveCitation.value?.source_type === "code");
const isDocument = computed(() => effectiveCitation.value?.source_type === "product_document");
const sourceLink = computed(() => {
  if (isDocument.value && displayedDetail.value?.document_url) {
    return displayedDetail.value.document_url;
  }
  return permalink.value;
});

function cacheKey(citation: Citation, view: "section" | "full" = "section"): string {
  return `${citation.source_type}:${citation.source_id}:${view}`;
}

async function loadDetail(citation: Citation | null): Promise<void> {
  controller?.abort();
  controller = null;
  detail.value = null;
  fullDetail.value = null;
  showFull.value = false;
  error.value = null;
  copied.value = false;
  if (!citation || !citationHasDetail(citation)) {
    loading.value = false;
    return;
  }

  const cached = detailCache.get(cacheKey(citation, "section"));
  if (cached) {
    detail.value = cached;
    loading.value = false;
    return;
  }

  controller = new AbortController();
  const activeController = controller;
  loading.value = true;
  try {
    const result = await fetchCitationDetail(citation, activeController.signal);
    if (activeController.signal.aborted) return;
    detailCache.set(cacheKey(citation, "section"), result);
    detail.value = result;
  } catch (reason) {
    if (activeController.signal.aborted) return;
    error.value = reason instanceof Error ? reason.message : "引用详情加载失败";
  } finally {
    if (controller === activeController) {
      loading.value = false;
      controller = null;
    }
  }
}

async function toggleFullDocument(): Promise<void> {
  const citation = props.citation;
  if (!citation || !detail.value?.full_text_available) return;
  if (showFull.value) {
    showFull.value = false;
    return;
  }
  if (fullDetail.value) {
    showFull.value = true;
    return;
  }
  const cached = detailCache.get(cacheKey(citation, "full"));
  if (cached) {
    fullDetail.value = cached;
    showFull.value = true;
    return;
  }
  const fullController = new AbortController();
  controller?.abort();
  controller = fullController;
  fullLoading.value = true;
  error.value = null;
  try {
    const result = await fetchCitationDetail(citation, fullController.signal, "full");
    if (fullController.signal.aborted) return;
    detailCache.set(cacheKey(citation, "full"), result);
    fullDetail.value = result;
    showFull.value = true;
  } catch (reason) {
    if (!fullController.signal.aborted) {
      error.value = reason instanceof Error ? reason.message : "文档全文加载失败";
    }
  } finally {
    if (controller === fullController) controller = null;
    fullLoading.value = false;
  }
}

async function copyExcerpt(): Promise<void> {
  if (!displayedDetail.value?.excerpt) return;
  await navigator.clipboard.writeText(displayedDetail.value.excerpt);
  copied.value = true;
  if (copyTimer) clearTimeout(copyTimer);
  copyTimer = setTimeout(() => (copied.value = false), 1600);
}

watch(() => props.citation, loadDetail, { immediate: true });
onBeforeUnmount(() => {
  controller?.abort();
  if (copyTimer) clearTimeout(copyTimer);
});
</script>

<template>
  <section class="citation-panel" aria-label="引用详情">
    <template v-if="effectiveCitation">
      <div class="citation-panel__header">
        <div class="citation-panel__eyebrow">
          <span class="citation-panel__type">{{ citationLabel(effectiveCitation) }}</span>
          <span v-if="effectiveCitation.domain" class="citation-panel__domain">
            {{ effectiveCitation.domain }}
          </span>
        </div>
        <h2>{{ citationDisplayName(effectiveCitation) }}</h2>
        <dl v-if="metadataRows.length" class="citation-panel__metadata">
          <template v-for="([label, value], index) in metadataRows" :key="`${label}-${index}`">
            <dt>{{ label }}</dt>
            <dd>{{ value }}</dd>
          </template>
        </dl>
      </div>

      <div v-if="loading" class="citation-panel__status">正在加载命中内容...</div>
      <div v-else-if="error" class="citation-panel__status citation-panel__status--error">
        {{ error }}
      </div>
      <section v-else-if="displayedDetail" class="citation-panel__excerpt-section">
        <div class="citation-panel__excerpt-heading">
          <h3>{{ isDocument ? (showFull ? "文档全文" : "命中章节") : "命中内容" }}</h3>
          <div class="citation-panel__heading-actions">
            <button
              v-if="isDocument && detail?.full_text_available"
              type="button"
              class="citation-panel__icon-button"
              title="查看完整产品文档"
              :disabled="fullLoading"
              @click="toggleFullDocument"
            >{{ fullLoading ? "加载中" : (showFull ? "收起全文" : "查看全文") }}</button>
            <button type="button" class="citation-panel__icon-button" title="复制命中内容" @click="copyExcerpt">
              <el-icon><CopyDocument /></el-icon>
              <span>{{ copied ? "已复制" : "复制" }}</span>
            </button>
          </div>
        </div>
        <div
          v-if="isDocument"
          class="citation-panel__excerpt citation-panel__document-body"
          v-html="renderMarkdown(displayedDetail.excerpt)"
        />
        <pre v-else :class="['citation-panel__excerpt', { 'citation-panel__excerpt--code': isCode }]">{{ displayedDetail.excerpt }}</pre>
        <p v-if="displayedDetail.truncated" class="citation-panel__truncated">内容较长，已截取命中位置附近片段。</p>
      </section>

      <a
        v-if="sourceLink"
        class="citation-panel__link"
        :href="sourceLink"
        :title="isDocument ? '打开产品文档原文件' : '打开 GitLab 源文件'"
        target="_blank"
        rel="noreferrer"
      >
        <el-icon><Link /></el-icon>
        {{ isDocument ? "打开产品文档原文件" : "打开 GitLab 源文件" }}
      </a>
    </template>
    <div v-else class="citation-panel__empty">
      <span>引用</span>
      <p>选择回答中的引用查看详情</p>
    </div>
  </section>
</template>

<style scoped>
.citation-panel {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
  padding: 24px 20px;
  background: var(--surface-raised);
  border-left: 1px solid var(--border-subtle);
  overflow: hidden;
  overflow-wrap: anywhere;
}

.citation-panel__header {
  flex: 0 0 auto;
}

.citation-panel__eyebrow {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  font-size: 12px;
}

.citation-panel__type { color: var(--accent-strong); font-weight: 700; }
.citation-panel__domain { color: var(--text-muted); }
h2 { margin: 0; font-size: 17px; line-height: 1.45; letter-spacing: 0; }
.citation-panel__source { margin: 8px 0 20px; color: var(--text-muted); font-size: 12px; }

.citation-panel__metadata {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  gap: 10px 12px;
  margin: 0;
  padding-top: 16px;
  border-top: 1px solid var(--border-subtle);
  font-size: 13px;
}

dt { color: var(--text-muted); }
dd { margin: 0; color: var(--text-secondary); }

.citation-panel__status {
  margin-top: 20px;
  padding: 14px;
  color: var(--text-muted);
  font-size: 13px;
  background: var(--surface-sunken, #f5f6f8);
}
.citation-panel__status--error { color: var(--danger, #b42318); }

.citation-panel__excerpt-section {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: 0;
  margin-top: 20px;
}
.citation-panel__excerpt-heading {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}
.citation-panel__excerpt-heading h3 { margin: 0; font-size: 14px; letter-spacing: 0; }
.citation-panel__heading-actions { display: flex; align-items: center; gap: 8px; }
.citation-panel__icon-button {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px;
  color: var(--text-secondary);
  font: inherit;
  font-size: 12px;
  background: transparent;
  border: 0;
  cursor: pointer;
}
.citation-panel__excerpt {
  flex: 1 1 auto;
  min-height: 120px;
  margin: 0;
  padding: 14px;
  color: var(--text-secondary);
  font-family: inherit;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  background: var(--surface-sunken, #f5f6f8);
  border: 1px solid var(--border-subtle);
  overflow: auto;
}
.citation-panel__excerpt--code {
  font-family: "Cascadia Code", "SFMono-Regular", Consolas, monospace;
  white-space: pre;
}
.citation-panel__document-body { white-space: normal; }
.citation-panel__document-body :deep(h1),
.citation-panel__document-body :deep(h2),
.citation-panel__document-body :deep(h3) { margin: 18px 0 8px; color: var(--text-primary); line-height: 1.4; letter-spacing: 0; }
.citation-panel__document-body :deep(h1:first-child),
.citation-panel__document-body :deep(h2:first-child),
.citation-panel__document-body :deep(h3:first-child) { margin-top: 0; }
.citation-panel__document-body :deep(p) { margin: 0 0 10px; }
.citation-panel__document-body :deep(pre) { padding: 12px; overflow: auto; background: var(--surface-raised); border: 1px solid var(--border-subtle); }
.citation-panel__document-body :deep(code) { font-family: "Cascadia Code", "SFMono-Regular", Consolas, monospace; }
.citation-panel__document-body :deep(.markdown-table) { max-width: 100%; overflow-x: auto; }
.citation-panel__document-body :deep(table) { width: max-content; min-width: 100%; border-collapse: collapse; }
.citation-panel__document-body :deep(th),
.citation-panel__document-body :deep(td) { padding: 7px 9px; border: 1px solid var(--border-subtle); text-align: left; }
.citation-panel__truncated { margin: 8px 0 0; color: var(--text-muted); font-size: 12px; }
.citation-panel__link {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 6px;
  margin-top: 16px;
  color: var(--accent-strong);
  font-size: 13px;
  font-weight: 600;
  text-decoration: none;
}
.citation-panel__empty {
  display: grid;
  flex: 1;
  place-content: center;
  min-height: 240px;
  color: var(--text-muted);
  text-align: center;
}
.citation-panel__empty span { color: var(--text-secondary); font-size: 14px; font-weight: 700; }
.citation-panel__empty p { margin: 6px 0 0; font-size: 12px; }

@media (max-width: 720px) {
  .citation-panel { padding: 18px 16px; border-left: 0; }
  .citation-panel__excerpt { max-height: min(52vh, 520px); }
}
</style>
