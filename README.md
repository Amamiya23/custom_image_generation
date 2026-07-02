# Custom Image Generation Skill

一个 Codex skill，用自定义的OpenAI Responses API 的 `image_generation` 工具生成、编辑和局部重绘图片。脚本会读取 Codex API 配置，调用接口完成图片生成等操作。**适合中转站用户**，并保证开箱即用

## 特性

- 支持图片生成、图片编辑、多输入图、mask 局部重绘。
- 支持 `size`、`quality`、`format`、`background`、`input_fidelity` 等常用参数。
- 无第三方依赖：Node 版本不需要 `npm install`，Python 版本不需要 `pip install`。
- 跨平台：Linux、macOS、Windows 均可用。
- 默认复用 Codex 配置和密钥，不要求用户手动粘贴 API key。

## 安装：
直接将此内容发给Codex：
```text
请安装这个 Codex skill：
https://github.com/Amamiya23/custom_image_generation/tree/main/custom-responses-image-generation
```

## 凭据来源

脚本默认读取 Codex 配置：

- `$CODEX_HOME/config.toml`，否则 `<home>/.codex/config.toml`
- `$CODEX_HOME/auth.json`，否则 `<home>/.codex/auth.json`

如果 Codex 配置不可用，会 fallback 到环境变量：

- `OPENAI_BASE_URL`
- `OPENAI_MODEL`
- `OPENAI_API_KEY`

环境变量名查找大小写不敏感，方便 Windows 使用。也可以只传变量名：

```bash
node responses-image-generation/scripts/generate-image.mjs \
  --prompt "A quick test image" \
  --out outputs/test.png \
  --api-key-env MY_OPENAI_API_KEY
```

## 安全说明

- 不要在命令行传真实密钥；脚本会拒绝 `--api-key <value>`。
- 不要把 `auth.json` 内容打印到聊天、日志或提交中。
- `--dry-run` 只显示 `has_api_key` 和 `api_key_source`，不会显示密钥值。
- `outputs/` 已被 `.gitignore` 忽略，生成图片默认可放在该目录。


