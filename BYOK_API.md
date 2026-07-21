# 临时 API Key（BYOK）接口说明

## 使用方式

正式问答接口为 `POST /ask`；流式客户端使用 `POST /ask/stream`。两个端点的 JSON 请求体和 BYOK 请求头一致。需要使用真实模型生成时，在同源请求中增加可选请求头：

```http
X-LLM-API-Key: <临时密钥>
X-LLM-Base-URL: https://api.deepseek.com
X-LLM-Model: deepseek-chat
```

请求示例：

```http
POST /ask
Content-Type: application/json
X-LLM-API-Key: <临时密钥>

{
  "question": "2024级计算机科学与技术专业的专业选修最低需要多少学分？",
  "college": "计算机与人工智能学院",
  "cohort": "2024",
  "session_id": "browser-session-id"
}
```

密钥不能写入 JSON 请求体；`AskRequest` 继续使用 `extra="forbid"`，因此请求体中的 `api_key` 会返回 HTTP 422。

流式端点返回 `application/x-ndjson`。普通对话的供应商增量作为 `delta`
事件实时转发。政策 RAG 会先冻结本轮检索证据，再消费供应商的真实 token 流；
后端只在一个完整声明通过引用、数字/课程代码、来源相关性和 URL 检查后，才发送带
`verified=true`、`seq` 与 `evidence_ids` 的 `delta`。全部声明结束后还会执行整体校验。
若中途或整体校验失败，服务端发送 `reset`，要求客户端丢弃尚未展示的字符并立即换成
确定性证据答案；最后始终用 `final.response` 交付完整 Markdown。结构化 SQL、拒答和
缓存命中答案不经过供应商声明流，而是发送已完成校验的安全预览。BYOK 不会绕过引用
和拒答边界。

## 生命周期与安全边界

- 前端使用 `type="password"` 和 `autocomplete="new-password"`。
- 密钥不写入 `.env`、YAML、SQLite、日志、URL、`localStorage` 或 `sessionStorage`。
- 浏览器只在当前页面内存中保留输入值；刷新、关闭或离开页面时清除。
- 服务端收到请求头后创建一次性 DeepSeek 客户端；请求结束后不把该运行时写入全局状态或缓存。
- 一次性运行时复用已加载的 BGE、FAISS、reranker 和可信 SQLite，避免每次请求重复加载 GPU 模型。
- 密钥只替换路由、普通对话和有依据生成客户端，不会关闭检索范围、证据门、引用验证或拒答策略。
- 提供方错误只返回异常类型，不回显请求头、密钥或上游响应正文。
- 自定义端点必须使用 HTTPS、端口 443 和精确域名白名单；DNS 解析到回环、私网、链路本地、保留地址或云元数据地址时直接返回 HTTP 400。

## 模型配置

模型和非敏感参数继续来自 `config.advanced.yaml`：

```yaml
generation:
  llm: deepseek-chat
  temperature: 0
  general_temperature: 0.7
  refuse_th: 0.35
  max_retries: 2
  request_timeout_seconds: 60
```

OpenAI 兼容客户端默认端点为 `https://api.deepseek.com`。内置白名单覆盖客户端预设厂商；额外厂商必须由运维通过 `SWUFE_RAG_LLM_ALLOWED_HOSTS` 添加精确域名。API Key 不应添加到配置文件。

## 无密钥行为

在 `SWUFE_RAG_MODE=local` 下，不发送 `X-LLM-API-Key` 时仍使用本地确定性生成器；正式 BGE/FAISS 检索、范围过滤、引用绑定和拒答门保持启用，便于离线调试。
