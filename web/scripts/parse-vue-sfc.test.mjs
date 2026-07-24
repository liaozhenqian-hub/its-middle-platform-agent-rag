import assert from "node:assert/strict";
import test from "node:test";

import { parseVueFiles } from "./parse-vue-sfc.mjs";

test("parses script and script setup blocks with original line offsets", () => {
  const result = parseVueFiles([
    {
      relative_path: "src/App.vue",
      content: `<template><main /></template>
<script lang="js">
export default { name: "App" }
</script>
<script setup lang="ts">
const answer: number = 42
</script>`,
    },
  ]);

  assert.deepEqual(result.diagnostics, []);
  assert.equal(result.files[0].relative_path, "src/App.vue");
  assert.deepEqual(
    result.files[0].blocks.map(({ kind, language, start_line }) => ({ kind, language, start_line })),
    [
      { kind: "script", language: "js", start_line: 3 },
      { kind: "script_setup", language: "ts", start_line: 6 },
    ],
  );
  assert.match(result.files[0].blocks[1].content, /answer: number/);
});
