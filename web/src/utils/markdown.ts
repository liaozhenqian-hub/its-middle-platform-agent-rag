import DOMPurify from "dompurify";
import { marked } from "marked";

export function renderMarkdown(markdown: string): string {
  const rendered = marked.parse(markdown, {
    async: false,
    breaks: true,
    gfm: true,
  }) as string;
  const sanitized = DOMPurify.sanitize(rendered, {
    USE_PROFILES: { html: true },
  });
  return sanitized
    .replaceAll("<table>", '<div class="markdown-table"><table>')
    .replaceAll("</table>", "</table></div>");
}
