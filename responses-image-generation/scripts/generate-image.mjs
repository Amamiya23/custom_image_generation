#!/usr/bin/env node

import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const HELP = `
Usage:
  node scripts/generate-image.mjs --prompt "..." --out outputs/image.png [options]

Required:
  --prompt <text>             Image prompt. Use --prompt-file or stdin as alternatives.

Output:
  --out <path>                Output image path. Default: generated.png

Codex config:
  --codex-home <path>         Defaults to $CODEX_HOME or <home>/.codex on Linux, macOS, and Windows
  --base-url <url>            Explicit override. Defaults to Codex provider base_url.
  --response-model <model>    Explicit override. Defaults to Codex top-level model.
  --api-key <key>             Explicit override. Avoid using this unless debugging.

Image generation options:
  --action <generate|edit|auto>
  --image <path>              Input image. Can be repeated.
  --mask <path>               Optional inpainting mask image.
  --image-model <model>
  --size <size>
  --quality <low|medium|high|auto>
  --format <png|webp|jpeg>
  --background <transparent|opaque|auto>
  --input-fidelity <high|low>
  --moderation <auto|low>
  --output-compression <0-100>
  --partial-images <0-3>

Other:
  --dry-run                   Print redacted config and request body without calling the API.
  --json                      Print machine-readable result summary.
  --help

Runtime:
  Requires Node.js 18+ only. No npm packages are needed.
`;

function die(message, code = 1) {
  console.error(`Error: ${message}`);
  process.exit(code);
}

function parseArgs(argv) {
  const args = { image: [] };
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith("--")) {
      die(`unexpected argument: ${token}`);
    }

    const key = token.slice(2);
    if (["help", "dry-run", "json"].includes(key)) {
      args[key] = true;
      continue;
    }

    const value = argv[i + 1];
    if (!value || value.startsWith("--")) {
      die(`missing value for --${key}`);
    }
    i += 1;

    if (key === "image") {
      args.image.push(value);
    } else {
      args[key] = value;
    }
  }
  return args;
}

function parseTomlValue(raw) {
  const value = raw.trim();
  if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
    return value.slice(1, -1);
  }
  if (value === "true") return true;
  if (value === "false") return false;
  if (/^-?\d+(\.\d+)?$/.test(value)) return Number(value);
  return value;
}

function parseTomlLite(text) {
  const root = {};
  const sections = {};
  let current = root;

  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;

    const sectionMatch = trimmed.match(/^\[([^\]]+)\]$/);
    if (sectionMatch) {
      const section = sectionMatch[1].replaceAll('"', "");
      sections[section] = sections[section] || {};
      current = sections[section];
      continue;
    }

    const keyValueMatch = trimmed.match(/^([A-Za-z0-9_.-]+)\s*=\s*(.+)$/);
    if (!keyValueMatch) continue;
    const [, key, rawValue] = keyValueMatch;
    current[key] = parseTomlValue(rawValue);
  }

  return { root, sections };
}

function readJsonIfExists(filePath) {
  if (!fs.existsSync(filePath)) return {};
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (error) {
    die(`failed to parse ${filePath}: ${error.message}`);
  }
}

function defaultCodexHome() {
  return path.resolve(process.env.CODEX_HOME || path.join(os.homedir(), ".codex"));
}

function resolveCodexConfig(args) {
  const codexHome = args["codex-home"] ? path.resolve(args["codex-home"]) : defaultCodexHome();
  const configPath = path.join(codexHome, "config.toml");
  const authPath = path.join(codexHome, "auth.json");

  let codexConfig = { root: {}, sections: {} };
  if (fs.existsSync(configPath)) {
    codexConfig = parseTomlLite(fs.readFileSync(configPath, "utf8"));
  }

  const providerName = codexConfig.root.model_provider || "OpenAI";
  const provider = codexConfig.sections[`model_providers.${providerName}`] || {};
  const auth = readJsonIfExists(authPath);

  const baseUrl = args["base-url"] || provider.base_url || process.env.OPENAI_BASE_URL || "https://api.openai.com/v1";
  const responseModel = args["response-model"] || codexConfig.root.model || process.env.OPENAI_MODEL;
  const apiKey = args["api-key"] || auth.OPENAI_API_KEY || process.env.OPENAI_API_KEY;

  if (!responseModel) {
    die("no Responses model found. Set Codex top-level model or pass --response-model.");
  }
  if (!apiKey && !args["dry-run"]) {
    die("no API key found. Expected OPENAI_API_KEY in Codex auth.json, environment, or --api-key.");
  }

  return {
    codexHome,
    configPath,
    authPath,
    providerName,
    provider,
    baseUrl: baseUrl.replace(/\/+$/, ""),
    responseModel,
    apiKey,
    hasApiKey: Boolean(apiKey),
  };
}

async function readPrompt(args) {
  if (args.prompt) return args.prompt;
  if (args["prompt-file"]) return fs.readFileSync(args["prompt-file"], "utf8").trim();
  if (!process.stdin.isTTY) {
    const chunks = [];
    for await (const chunk of process.stdin) chunks.push(chunk);
    const stdinPrompt = Buffer.concat(chunks).toString("utf8").trim();
    if (stdinPrompt) return stdinPrompt;
  }
  die("missing --prompt, --prompt-file, or stdin prompt.");
}

function mimeTypeFor(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  if (ext === ".jpg" || ext === ".jpeg") return "image/jpeg";
  if (ext === ".webp") return "image/webp";
  if (ext === ".gif") return "image/gif";
  return "image/png";
}

function imageFileToDataUrl(filePath) {
  if (!fs.existsSync(filePath)) die(`image file not found: ${filePath}`);
  const data = fs.readFileSync(filePath).toString("base64");
  return `data:${mimeTypeFor(filePath)};base64,${data}`;
}

function buildTool(args) {
  const tool = { type: "image_generation" };
  const optionMap = {
    action: "action",
    background: "background",
    "input-fidelity": "input_fidelity",
    "image-model": "model",
    moderation: "moderation",
    "output-compression": "output_compression",
    format: "output_format",
    "partial-images": "partial_images",
    quality: "quality",
    size: "size",
  };

  for (const [argName, bodyName] of Object.entries(optionMap)) {
    if (args[argName] === undefined) continue;
    if (["output-compression", "partial-images"].includes(argName)) {
      const numberValue = Number(args[argName]);
      if (!Number.isFinite(numberValue)) die(`--${argName} must be a number`);
      tool[bodyName] = numberValue;
    } else {
      tool[bodyName] = args[argName];
    }
  }

  if (args.mask) {
    tool.input_image_mask = { image_url: imageFileToDataUrl(args.mask) };
  }

  if (!tool.action && (!args.image || args.image.length === 0)) {
    tool.action = "generate";
  }

  return tool;
}

function buildInput(prompt, args) {
  if (!args.image || args.image.length === 0) return prompt;

  return [
    {
      role: "user",
      content: [
        { type: "input_text", text: prompt },
        ...args.image.map((imagePath) => ({
          type: "input_image",
          image_url: imageFileToDataUrl(imagePath),
        })),
      ],
    },
  ];
}

function outputPathFor(basePath, index, count, format) {
  if (count === 1) return basePath;
  const parsed = path.parse(basePath);
  const ext = parsed.ext || `.${format || "png"}`;
  return path.join(parsed.dir, `${parsed.name}-${index + 1}${ext}`);
}

function redactRequest(body) {
  return JSON.parse(
    JSON.stringify(body, (key, value) => {
      if (key === "image_url" && typeof value === "string" && value.startsWith("data:")) {
        return value.slice(0, value.indexOf(",") + 1) + "<base64-redacted>";
      }
      return value;
    }),
  );
}

async function main() {
  if (typeof fetch !== "function") {
    die("Node.js 18+ is required because this script uses the built-in fetch API.");
  }

  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(HELP.trim());
    return;
  }

  const prompt = await readPrompt(args);
  const config = resolveCodexConfig(args);
  const outputPath = args.out || "generated.png";
  const tool = buildTool(args);
  const requestBody = {
    model: config.responseModel,
    input: buildInput(prompt, args),
    tools: [tool],
  };

  if (args["dry-run"]) {
    console.log(JSON.stringify({
      codex_home: config.codexHome,
      config_path: config.configPath,
      auth_path: config.authPath,
      provider: config.providerName,
      base_url: config.baseUrl,
      endpoint: `${config.baseUrl}/responses`,
      response_model: config.responseModel,
      has_api_key: config.hasApiKey,
      request: redactRequest(requestBody),
    }, null, 2));
    return;
  }

  const response = await fetch(`${config.baseUrl}/responses`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${config.apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(requestBody),
  });

  const responseText = await response.text();
  let responseJson;
  try {
    responseJson = JSON.parse(responseText);
  } catch {
    die(`API returned non-JSON response with status ${response.status}: ${responseText.slice(0, 500)}`);
  }

  if (!response.ok) {
    const message = responseJson.error?.message || responseJson.message || JSON.stringify(responseJson).slice(0, 1000);
    die(`API request failed with status ${response.status}: ${message}`);
  }

  const imageResults = [];
  for (const item of responseJson.output || []) {
    if (item.type === "image_generation_call" && item.result) {
      imageResults.push(item.result);
    }
  }

  if (imageResults.length === 0) {
    console.error(JSON.stringify(responseJson, null, 2));
    die("response did not contain output[].type == image_generation_call with a result.");
  }

  const outputFormat = tool.output_format || path.extname(outputPath).replace(".", "") || "png";
  const written = [];
  for (let i = 0; i < imageResults.length; i += 1) {
    const target = outputPathFor(outputPath, i, imageResults.length, outputFormat);
    fs.mkdirSync(path.dirname(path.resolve(target)), { recursive: true });
    fs.writeFileSync(target, Buffer.from(imageResults[i], "base64"));
    written.push(target);
  }

  const summary = {
    response_id: responseJson.id,
    provider: config.providerName,
    base_url: config.baseUrl,
    response_model: config.responseModel,
    image_model: tool.model || "api-default",
    outputs: written,
  };

  if (args.json) {
    console.log(JSON.stringify(summary, null, 2));
  } else {
    for (const filePath of written) {
      console.log(`Wrote ${filePath}`);
    }
  }
}

main().catch((error) => {
  die(error.stack || error.message || String(error));
});
