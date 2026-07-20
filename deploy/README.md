# 生产部署指南

服务器上线所需的全部配置。两种方案任选:**Docker Compose**(推荐,一条命令
起全套)或 **systemd**(不想装 Docker 时)。

## 目录内容

| 文件 | 用途 |
|---|---|
| `../Dockerfile` | 应用镜像(非 root、只读数据、含 HEALTHCHECK) |
| `../docker-compose.yml` | 编排:app + redis + nginx |
| `nginx.conf` | TLS 反代;流式端点关缓冲、长超时、真实 IP 透传 |
| `swufe-rag.service` | systemd 单元(非 Docker 方案) |
| `.env.example` | 环境变量样例,复制后修改 |

---

## 一、服务器规格

每个 worker 独立加载 BGE 模型 + FAISS 索引,**实测常驻约 1.0 GB**。

| 配置 | 内存 | workers | 大致承载(活跃用户) |
|---|---|---|---|
| 最低 | 4 GB | 1 | 30~50 |
| 推荐 | 8 GB | 2 | 100~200 |
| 宽裕 | 16 GB | 4 | 200~400 |

磁盘至少 **10 GB**:模型 1.5 G + 索引 627 M + 数据 807 M + 镜像与日志。

> 承载力的瓶颈不是本地检索(实测单次 30 ms),而是 LLM 调用(6~15 秒)。
> 由于是 BYOK,每个用户用自己的 Key,LLM 的费用与速率限制天然分摊。

---

## 二、Docker Compose 部署

```bash
# 1. 准备配置
cp deploy/.env.example .env
vim .env                      # 至少确认 SWUFE_RAG_WORKERS 与 HF_CACHE_DIR

# 2. 准备 TLS 证书(见下方"证书签发")
mkdir -p deploy/certs

# 3. 起服务
docker compose up -d --build

# 4. 确认就绪(首次要加载模型,约 1~3 分钟)
curl -s http://127.0.0.1/healthz          # 立即 200
curl -s http://127.0.0.1/readyz | jq      # ready:true 才算能接流量
docker compose logs -f app
```

### 数据与模型如何进入容器

`data/` 与 `artifacts/` 是**只读挂载**而非烤进镜像——知识库更新(重新入库、
换索引)不需要重建镜像。BGE 模型同理复用宿主机的 `~/.cache/huggingface`。

首次在新服务器上需要先把模型放到宿主机:

```bash
# 方式一(推荐):从本机打包上传,避免服务器上的网络问题
tar czf hf-cache.tgz -C ~/.cache huggingface
scp hf-cache.tgz user@server:~/ && ssh user@server 'tar xzf hf-cache.tgz -C ~/.cache'

# 方式二:服务器上从 ModelScope 拉(国内直连稳定)
pip install modelscope
python -c "from modelscope import snapshot_download; snapshot_download('AI-ModelScope/bge-large-zh-v1.5')"
```

### 想要完全自包含的镜像(烤模型进去)

把模型下载加进 `Dockerfile`(镜像会大约 +1.5 G,但部署时零外部依赖):

```dockerfile
# 放在 COPY . . 之前
RUN python -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('BAAI/bge-large-zh-v1.5')" \
    && chown -R swufe:swufe /home/swufe/.cache
```

然后删掉 compose 里的 `HF_CACHE_DIR` 挂载行。

---

## 三、TLS 证书

**iOS 客户端必须走 HTTPS**(App Transport Security 不允许公网明文),
局域网调试时用的 `NSAllowsLocalNetworking` 在公网无效。

```bash
# Let's Encrypt(需要域名已解析到本机)
docker run --rm -it \
  -v "$PWD/deploy/certs:/etc/letsencrypt/live/out" \
  -v swufe-rag_certbot-webroot:/var/www/certbot \
  certbot/certbot certonly --webroot -w /var/www/certbot \
  -d your-domain.edu.cn --email you@example.com --agree-tos

# 把签发结果放到 nginx 读取的位置
cp /etc/letsencrypt/live/your-domain.edu.cn/{fullchain,privkey}.pem deploy/certs/
docker compose restart nginx
```

续签加进 crontab(证书 90 天有效):

```cron
0 3 * * 1 cd /opt/swufe-rag && docker compose run --rm certbot renew && docker compose restart nginx
```

---

## 四、systemd 部署(非 Docker)

```bash
sudo useradd --system --home /opt/swufe-rag --shell /usr/sbin/nologin swufe
sudo rsync -a --exclude .venv ./ /opt/swufe-rag/
cd /opt/swufe-rag && sudo -u swufe python3.12 -m venv .venv
sudo -u swufe .venv/bin/pip install -r requirements.txt -r requirements-web.txt

sudo cp deploy/.env.example /etc/swufe-rag.env    # 记得把 REDIS_URL 改成 127.0.0.1
sudo cp deploy/swufe-rag.service /etc/systemd/system/
sudo mkdir -p /var/tmp/swufe-rag /opt/swufe-rag/logs
sudo chown swufe:swufe /var/tmp/swufe-rag /opt/swufe-rag/logs
sudo systemctl daemon-reload && sudo systemctl enable --now swufe-rag

systemctl status swufe-rag
journalctl -u swufe-rag -f
```

Nginx 单独装:把 `deploy/nginx.conf` 放进 `/etc/nginx/conf.d/`,并把
`map` 里的 `app:8000` 改成 `127.0.0.1:8000`、删掉 `resolver` 那行
(非容器环境不需要动态解析)。

---

## 五、本地冒烟(上线前先在自己机器上跑一遍)

```bash
# 自签证书仅供本地;正式环境必须用 CA 签发的(iOS 不信任自签)
mkdir -p deploy/certs
openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -keyout deploy/certs/privkey.pem -out deploy/certs/fullchain.pem \
  -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

cp deploy/.env.example .env
docker compose up -d --build          # 首次构建约 10 分钟(装 PyTorch CPU)
docker compose ps                     # 三个服务都要 healthy

curl -sk https://127.0.0.1/readyz | jq          # ready:true
curl -sk -X POST https://127.0.0.1/ask -H 'Content-Type: application/json' \
  -d '{"question":"毕业需要修满多少学分？","cohort":"2024","major":"网络空间安全专业"}' | jq -r .answer_md

docker compose down                   # 清理(加 -v 连数据卷一起删)
```

### 已知坑(都已在配置里修好,供排查参考)

| 现象 | 原因 | 已做的处理 |
|---|---|---|
| app 反复重启,日志 `cp: /tmp/swufe-rag/...: Permission denied` | 给该路径挂了命名卷,Docker 以 root 创建,而容器跑在 uid 10001 | 去掉该卷,数据落容器可写层(每次启动都从只读源重新生成,本就不需持久化) |
| nginx 起不来,`host not found in upstream "app:8000"` | upstream 在启动时解析,app 还没就绪就会拖死 nginx | 改用 `resolver` + 变量,请求时解析 |
| 流式回答"卡十几秒后整段蹦出" | Nginx 默认缓冲整个响应 | `/ask/stream` 显式 `proxy_buffering off` |
| 限流把健康检查也算进配额 | 豁免路径名与实际端点不一致 | 豁免列表对齐 `/healthz` `/readyz`,并加了守护测试 |

---

## 六、上线后的运维

### 看状态

```bash
curl -s https://your-domain/readyz | jq
```

返回里三块值得盯:

- `query_capacity` — `timed_out`/`rejected` 持续增长说明检索并发不够,
  调大 `SWUFE_RAG_QUERY_MAX_CONCURRENCY`(云服务器纯 CPU,默认 4 偏保守)。
- `rate_limit.rejected` — 增长快说明限流过严或有人在刷。
- `redis.reachable` — 生产配置下为 false 时 `/readyz` 和问答入口均返回
  503；先恢复 Redis，不能用多 worker 进程内降级冒充正常服务。

### 调参经验

| 现象 | 调整 |
|---|---|
| 内存吃紧 | 减 `SWUFE_RAG_WORKERS` |
| 检索排队(`queue_waits` 高) | 加 `SWUFE_RAG_QUERY_MAX_CONCURRENCY` |
| 正常用户被限流 | 加 `SWUFE_RAG_RATE_LIMIT` |
| 首问慢 | 确认 `SWUFE_RAG_EAGER_WARMUP=1` |

### 安全须知

- **服务端不持有任何 LLM Key**。用户 Key 随 `X-LLM-API-Key` 按次传入,
  用完即弃、不落日志。别往 `.env` 里写 Key。
- `SWUFE_RAG_TRUST_PROXY=1` **只在反代后面开**。直接暴露端口时开启的话,
  客户端可以伪造 `X-Forwarded-For` 绕过限流。
- 防火墙只放行 80/443,app 的 8000 与 redis 的 6379 都不要对公网开放
  (compose 里已经不发布这两个端口)。

---

## 七、客户端配合改动

iOS 端在「关于 › 后端地址」填 `https://your-domain.edu.cn` 即可,
其余无需改动。上线前记得确认:

- 后端地址用 **https**(否则 ATS 拦截)
- 若用自签证书,iOS 不会信任 —— 必须用正式 CA 签发的证书
