import { parse } from "@vue/compiler-sfc";
import { pathToFileURL } from "node:url";

function diagnosticMessage(error) {
  if (typeof error === "string") {
    return error;
  }
  return error?.message ?? String(error);
}

function normalizedBlock(kind, block) {
  let content = block.content;
  let startLine = block.loc.start.line;
  while (content.startsWith("\r\n") || content.startsWith("\n")) {
    if (content.startsWith("\r\n")) {
      content = content.slice(2);
    } else {
      content = content.slice(1);
    }
    startLine += 1;
  }
  return {
    kind,
    language: (block.lang || "js").toLowerCase(),
    content,
    start_line: startLine,
  };
}

export function parseVueFiles(files) {
  const parsedFiles = [];
  const diagnostics = [];

  for (const file of files) {
    const relativePath = file?.relative_path;
    const content = file?.content;
    if (typeof relativePath !== "string" || typeof content !== "string") {
      diagnostics.push({ message: "Vue input requires relative_path and content strings" });
      continue;
    }
    const result = parse(content, { filename: relativePath });
    for (const error of result.errors) {
      diagnostics.push({ relative_path: relativePath, message: diagnosticMessage(error) });
    }
    const blocks = [
      ["script", result.descriptor.script],
      ["script_setup", result.descriptor.scriptSetup],
    ]
      .filter(([, block]) => Boolean(block))
      .map(([kind, block]) => normalizedBlock(kind, block));
    parsedFiles.push({ relative_path: relativePath, blocks });
  }

  return { files: parsedFiles, diagnostics };
}

async function readStdin() {
  let input = "";
  process.stdin.setEncoding("utf8");
  for await (const chunk of process.stdin) {
    input += chunk;
  }
  return input;
}

async function main() {
  const request = JSON.parse(await readStdin());
  const files = Array.isArray(request?.files) ? request.files : [];
  process.stdout.write(JSON.stringify(parseVueFiles(files)));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    process.stderr.write(`${diagnosticMessage(error)}\n`);
    process.exitCode = 1;
  });
}
