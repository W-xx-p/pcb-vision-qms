# PCB 少样本异常检测与质量管理平台

![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white)
![License MIT](https://img.shields.io/badge/License-MIT-green.svg)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)

> 当前定位：企业级 PoC / 试点上线级。真实量产结论仍需使用目标工厂的 PCB 数据、相机、PLC 和 MES 完成现场验收。

## 2026-10 可靠性加固

- 检测记录、质量告警、审计日志和集成 Outbox 在同一数据库事务中提交。
- Redis Streams 训练队列支持消费者故障认领、指数退避延迟重试和死信流。
- 生产限流改为 Redis 原子计数，API Key 在 Redis 键中只保留不可逆摘要。
- 模型产物拥有逐文件 SHA-256 清单；生产模式使用 HMAC 签名验证真实性。
- 模型发布状态覆盖 candidate、approved、active、retired、rejected 和 rolled_back，并提供受审计回滚接口。
- Outbox 使用 `FOR UPDATE SKIP LOCKED` 并发认领，支持锁超时恢复、幂等键和失败退避。
- 漂移报告同时评估异常分数、亮度、对比度和清晰度 PSI。
- 边缘工位支持配方覆盖、服务心跳、原子落盘、缓存容量保护和抖动指数退避。
- 生产覆盖文件强制要求 PostgreSQL、Redis、MinIO、Grafana、API Key 和模型签名密钥使用非默认密钥。
- PostgreSQL 备份会恢复到临时数据库并校验表和业务数据，跨客户端版本恢复已开启严格错误检查。

### 开发环境启动

```bash
docker compose up -d --build
```

### 生产配置校验与启动

复制 `.env.production.example` 为不纳入版本控制的 `.env.production`，替换全部占位值，然后执行：

```bash
docker compose --env-file .env.production \
  -f compose.yaml -f compose.production.yaml --profile production config
docker compose --env-file .env.production \
  -f compose.yaml -f compose.production.yaml --profile production up -d
```

生产启动检查会拒绝默认凭据、缺失 Redis、短模型签名密钥以及非安全 Cookie 配置。

面向电子产品制造企业的视觉质检系统。平台使用少量正常产品图片建立外观基线，对 PCB、电子模块和连接器进行异常评分与缺陷定位，并将检测结果纳入人工复核、批次统计、模型版本和审计闭环。

项目参考 [Anomalib](https://github.com/open-edge-platform/anomalib) 的工业异常检测能力设计，支持可运行的参考图基线和独立 PatchCore 训练镜像。公开实验可使用 Amazon VisA 中的 PCB1～PCB4 数据。

![PCB Vision QMS 运行界面](docs/screenshots/dashboard.png)

▶ [观看 84 秒五工作区演示视频](docs/demo/pcb-vision-qms-demo.mp4)

## 公开数据实测

已在 Amazon VisA 的 PCB1～PCB4 上完成 48 次真实实验（4 后端 × 4 类别 × 3 随机种子），不是占位数据。统一 64-shot、CPU-only 协议的四类别宏平均如下：

| 后端 | 图像 AUROC | 像素 AUROC | F1 | Recall | P95 延迟 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Reference | 0.6112 | 0.7903 | 0.6666 | 0.8490 | 100.4 ms |
| PatchCore | **0.8493** | **0.9740** | 0.7534 | 0.7844 | 611.5 ms |
| PaDiM | 0.8159 | 0.9731 | **0.7631** | 0.8708 | 170.1 ms |
| EfficientAD（8 batches） | 0.5503 | 0.7403 | 0.6856 | **0.9396** | 1219.4 ms |

完整协议、分类别均值±标准差、48 次原始结果、数据质量风险和复现说明见 [VisA PCB 真实基准报告](docs/BENCHMARK_RESULTS.md)。公开数据质量扫描发现官方划分存在跨 split 近重复候选，因此本结果用于工程选型，不宣称学术 SOTA。

## 企业架构

```mermaid
flowchart LR
    Camera[工业相机/边缘工位] --> API[FastAPI 质检服务]
    API --> PG[(PostgreSQL)]
    API --> MinIO[(MinIO 工件存储)]
    API --> Redis[(Redis Streams)]
    Redis --> ML[PatchCore ML Worker]
    API --> Outbox[事务 Outbox]
    Outbox --> MES[MES / QMS / PLC]
    API --> Prom[Prometheus]
    Prom --> Grafana[Grafana / Alertmanager]
```

详细组件职责、数据流和部署边界见 [架构说明](docs/ARCHITECTURE.md)。

## 当前能力

- 产品型号、检测阈值和正常参考图管理
- 单张检测与最多 100 张图片的批次检测
- 异常分数、正常/异常判定和定位热力图
- 模型训练任务、模型版本、指标和活动模型管理
- Anomalib 2.2 PatchCore真实训练、OpenVINO安全推理和独立ML Worker
- 候选模型验证集重推理、F1/召回率/误报率发布门禁与人工发布
- 批次号、产品 SN、推理延迟和检测记录
- 人工复核、复核备注和审计日志
- 图片亮度、对比度、清晰度质量分析
- 基于已复核样本的Precision、Recall、F1与混淆矩阵
- 自动搜索并应用最优异常阈值
- 每日质量趋势与平均推理延迟
- CSV 质量报告导出
- Web 质检工作台与 OpenAPI 文档
- 可选 API Key、Docker、健康检查和自动化测试
- Redis Streams消费者组、ACK、超时认领、重试与死信队列
- MinIO作为参考图、检测图、热力图和备份的主存储，本地仅保留计算缓存
- 检测记录、质量告警、审计日志和Outbox事件原子事务
- PostgreSQL定时备份、MinIO异地副本与自动恢复验证
- 请求ID、结构化访问日志和Prometheus格式运行指标
- 产线、检测工位、相机编号、班次和产品SN追溯
- 产品SN重复过站拦截
- 滚动异常率、连续异常质量规则和告警处置闭环
- 三西格玛SPC均值、标准差、上下控制限和失控点
- Admin、Engineer、Inspector、Reviewer、Auditor、Integration角色权限
- MES/QMS可靠事件出站箱、交付确认和外部单号
- PostgreSQL 16、JSONB、带时区时间、连接池和外键约束
- Alembic版本化结构迁移、SQLite数据迁移工具和PostgreSQL逻辑备份
- 总览、检测中心、制造管理、质量分析四工作区后台界面
- 响应式SPC控制图、每日异常率趋势图和复核混淆矩阵
- 正式登录弹窗、当前用户/角色展示、退出登录和前端权限感知
- 检测图片预览、推理加载状态和热力图叠加强度调节
- 产品编辑、停用和ROI检测区域配置
- 检测记录组合筛选、分页参数与详情查看
- 缺陷区域坐标、面积占比和强度量化
- 告警负责人、确认、关闭及处置说明工作流
- 工位心跳、边缘软件版本和在线时间记录
- 误报、漏报和困难样本回流训练队列
- GZip响应压缩、CSP及常用浏览器安全响应头
- 边缘工位相机/GStreamer采集、离线缓存与目录回放
- MES HTTP幂等回调、PLC Modbus TCP结果寄存器回写和失败重试
- Prometheus、Alertmanager、Grafana预置仪表盘与故障规则
- Nginx TLS 1.2/1.3入口、OIDC JWT统一身份认证和RBAC角色映射
- PSI模型漂移扫描、漂移Outbox事件和可控自动重训练
- Locust性能场景与带错误率、P95门槛的72小时稳定性脚本

## 系统流程

```text
正常样本登记 ─→ 模型训练/发布 ─→ 单张或批次检测
                                      │
                                      ├→ 异常分数与热力图
                                      ├→ 人工复核
                                      ├→ 批次质量记录
                                      └→ CSV报告与审计日志
```

## 技术栈

- Python 3.11+
- FastAPI、PostgreSQL、SQLAlchemy、Alembic、Pillow、NumPy
- Redis任务队列、MinIO对象存储
- Anomalib/PatchCore（可选）
- 原生 HTML、CSS、JavaScript
- Docker Compose

本地无配置启动仍可使用SQLite，便于开发和自动化测试；Docker企业部署默认使用PostgreSQL 16，Redis独立执行训练任务，图片和模型工件归档至MinIO。
## 仓库结构

```text
app/                    API、后台任务、领域服务与 Web 工作台
migrations/             Alembic PostgreSQL 结构迁移
edge/                   工业相机/目录回放边缘工位代理
deploy/                 Nginx TLS 网关配置
monitoring/             Prometheus、Alertmanager 与 Grafana
performance/            Locust 性能场景
scripts/                数据准备、基准评测、迁移、备份与稳定性工具
tests/                  API、治理、数据质量、并发与安全测试
.github/workflows/       持续集成与 VisA PCB 定期基准
```

本地开发、企业 Compose、生产覆盖文件和 ML 镜像相互独立，便于本地评估、功能演示和真实工厂部署扩展。

## 本地启动

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

pip install -e ".[dev]"
uvicorn app.main:app --reload
```

默认优先将数据库、上传图片和模型产物写入项目的 `data` 目录；如果该目录不可写，会自动使用系统临时目录
`pcb-qms-runtime`。生产环境应显式设置 `PCB_QMS_DATA_DIR` 到持久化磁盘。

打开：

- 质检平台：<http://127.0.0.1:8000>
- API文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/health>

生成无需下载的演示图片：

```bash
python scripts/generate_demo_images.py
```

在页面中创建 `PCB-A100`，先上传输出目录中的 `normal`，再检测 `test` 中的图片。脚本默认输出到
`demo-data`；目录不可写时会打印实际使用的系统临时目录，也可通过 `--output` 显式指定。

## PatchCore后端

安装可选依赖：

```bash
pip install -e ".[anomalib]"
```

在产品页面选择 `Anomalib PatchCore` 并创建模型版本。ML Worker会用正常样本训练模型，导出不依赖 Pickle 的 OpenVINO IR，并为 XML/BIN 工件生成 SHA-256 完整性清单后保存到 MinIO。训练完成后模型保持候选状态；系统使用人工复核样本重新执行候选模型推理，达到样本量、F1、召回率和误报率门禁后，工程师才能调用发布接口。发布时自动应用验证集建议阈值。门禁可通过 `POST /api/products/{product_id}/models/{model_id}/evaluate` 重新执行。

> Anomalib版本升级可能调整部署API。正式部署前应锁定依赖版本，并在目标CPU/GPU上完成回归测试。

## 产线边缘与外部系统

复制 `edge/station.example.json` 并配置相机编号、RTSP/GStreamer地址或 `replay_dir`，然后运行：

```bash
pip install -e ".[edge]"
python edge/station_agent.py --config edge/station.json
```

边缘工位使用产品SN作为重复过站约束，上传失败的图片保留在本地spool。MES通过 `PCB_QMS_MES_WEBHOOK_URL` 接收带 `Idempotency-Key` 的HTTP事件；PLC通过 `PCB_QMS_PLC_*` 配置Modbus TCP寄存器，`0`代表正常、`1`代表异常。未连接真实设备时可设置 `PCB_QMS_INTEGRATION_SIMULATOR=1` 验证Outbox交付状态，但模拟结果不能作为现场联调结论。

## HTTPS与统一身份认证

生产环境将证书放到 `deploy/tls/tls.crt` 和 `deploy/tls/tls.key`，设置强数据库、MinIO、Grafana密码，配置API Key或OIDC，再启动TLS网关：

```bash
docker compose --profile production up -d --build
```

OIDC需要设置Issuer、JWKS URL、Audience与角色Claim。令牌角色需映射到 `admin/engineer/inspector/reviewer/auditor/integration` 之一。生产启动检查会拒绝默认密钥和无认证配置。

## 性能与72小时稳定性

```bash
pip install -e ".[load]"
locust -f performance/locustfile.py --headless -u 20 -r 5 -t 10m --host http://127.0.0.1:8013
python scripts/soak_test.py --url http://127.0.0.1:8013 --duration-hours 72 --concurrency 10
```

稳定性脚本默认以错误率不高于0.1%、P95不高于1000ms作为门槛，并以非零退出码标记失败。正式报告必须记录目标硬件、数据规模、并发量、模型后端、CPU/GPU、容器重启次数和监控截图；短时冒烟不能替代72小时运行结果。

## Docker

```bash
docker compose up -d --build
docker compose ps
```

Compose会启动业务服务、数据基础设施、集成/漂移Worker以及完整监控栈。默认入口如下：

- 质检平台：<http://127.0.0.1:8013>
- API文档：<http://127.0.0.1:8013/docs>
- 就绪检查：<http://127.0.0.1:8013/api/ready>
- MinIO管理控制台：<http://127.0.0.1:9001>
- Prometheus：<http://127.0.0.1:9090>
- Grafana：<http://127.0.0.1:3000>
- Alertmanager：<http://127.0.0.1:9093>

训练请求会进入Redis持久化队列，由独立Worker执行；参考图、检测原图、热力图和模型工件会同时归档到MinIO。PostgreSQL业务数据、本地媒体、Redis数据和MinIO对象分别保存在独立命名卷中。API启动前自动执行 `alembic upgrade head`。共享环境建议复制 `.env.example` 为 `.env`，修改数据库与MinIO凭据并配置分角色密钥：

```dotenv
PCB_QMS_API_KEYS={"admin-secret":{"name":"qms-admin","role":"admin"},"station-secret":{"name":"aoi-01","role":"inspector"},"mes-secret":{"name":"mes","role":"integration"}}
MINIO_ROOT_USER=qmsadmin
MINIO_ROOT_PASSWORD=replace-with-a-strong-password
POSTGRES_PASSWORD=replace-with-a-strong-password
```

可通过 `.env` 中的 `PCB_QMS_HTTP_PORT` 修改Web端口。`/api/ready` 会同时检查数据库、本地存储、Redis与MinIO，任一已配置依赖不可用时返回非就绪状态。

生产环境必须设置 `PCB_QMS_ENV=production`。该模式会拒绝无API Key、默认数据库密码或弱MinIO密码启动，并启用按客户端身份/IP计算的分钟级限流。训练任务使用Redis Streams消费者组；失败任务最多执行三次，之后写入 `pcb-qms:training:dead-letter`。

### SQLite数据迁移

旧版命名卷中的 `/app/data/quality.db` 会保留，不会自动删除。首次切换PostgreSQL时，在API和Worker停止写入的情况下执行：

```bash
docker compose up -d postgres redis minio
docker compose run --rm --no-deps pcb-qms alembic upgrade head
docker compose run --rm --no-deps pcb-qms python scripts/migrate_sqlite_to_postgres.py --source /app/data/quality.db
docker compose up -d pcb-qms worker
```

迁移工具要求目标业务表为空，避免误操作造成重复数据；导入在单个事务中完成，并自动转换JSONB、布尔值、时区时间及校正自增序列。

### 数据库版本与备份

```bash
docker compose run --rm --no-deps pcb-qms alembic current
```

管理员备份接口在PostgreSQL模式生成可供 `pg_restore` 使用的自定义格式 `.dump` 文件；SQLite开发模式仍生成 `.db` 文件。生产环境还应将备份复制到独立存储并定期执行恢复演练。

Compose中的 `backup` 服务会按 `PCB_QMS_BACKUP_INTERVAL_SECONDS` 周期生成备份，将其写入MinIO，并创建临时PostgreSQL数据库执行真实 `pg_restore` 和表/数据校验，验证后自动删除临时数据库。

客户端通过请求头 `X-API-Key` 发送密钥。未设置任何密钥时，系统仅为了本地开发以管理员身份运行。
Web工作台会用密钥换取8小时的 HttpOnly、SameSite 浏览器会话；检测原图和热力图同样经过鉴权，且阻止目录穿越。
HTTPS部署时设置 `PCB_QMS_SECURE_COOKIES=1`。

角色权限：管理员负责全部配置和备份；工程师负责产品、模型、阈值、产线及质量规则；检测员执行检测；复核员处理检测与告警；审计员读取审计记录；集成账号拉取并确认 MES/QMS 事件。

## 测试

### VisA PCB 公开评测与多模型基准

项目内置 Amazon VisA 官方 `1cls.csv` 数据准备器，只抽取 `pcb1`～`pcb4`，同时生成包含原始来源、尺寸、标签、SHA-256 和整体清单哈希的 `manifest.json`：

```bash
python scripts/prepare_visa.py --output data/public/visa
```

统一评测器在相同划分和校准规则下比较参考差分、PatchCore、PaDiM 与 EfficientAD，输出图像级 AUROC、像素级 AUROC、Precision/Recall/F1 和推理 P50/P95：

```bash
python scripts/benchmark_visa.py \
  --dataset data/public/visa \
  --output data/benchmarks/visa \
  --backends reference patchcore padim efficientad \
  --train-samples 64 \
  --patchcore-coreset-ratio 0.01 \
  --seeds 17 42 73
```

默认协议是每个 PCB 类别随机抽取 64 张正常图训练、使用完整官方测试集评估（`--train-samples 0` 可改为全训练集），PatchCore 使用明确记录的 1% coreset；EfficientAD 复用冻结的正常训练集作为辅助 hard-feature 数据并训练 8 个批次，避免运行时下载 1.56 GB Imagenette 且满足 CPU CI 时间预算；所有模型热力图统一为 256×256 后计算精确像素 AUROC。`checkpoint.json` 在每个类别/模型/种子组合完成后原子保存，可安全断点续跑；`report.json` 固定记录协议、数据清单哈希和逐次结果，`summary.csv` 保存单次结果，`aggregate.csv` 保存三随机种子的均值与标准差。GitHub Actions 的 `VisA PCB benchmark` 每周及手动运行完整 ML 镜像评测并保存结果工件；普通 `Enterprise acceptance` 则在每次 Push/PR 上执行 Ruff、空 PostgreSQL 迁移、API/数据血缘/并发测试、Compose 校验和容器冒烟测试，不会在普通提交中重复下载大型数据集。

### 高分辨率切片和实验血缘

训练请求可设置 `tiling_enabled=true`、`tile_size` 和 `tile_overlap`。活动模型会对原图执行重叠切片、边缘补齐和最大异常图融合，最终缺陷框仍返回原始整图归一化坐标。推荐先通过 `POST /api/products/{id}/datasets/snapshot` 冻结正常样本，再把返回的 `dataset_version_id` 传给训练接口。系统会将数据清单哈希、模型版本、参数、指标、代码版本和镜像摘要写入 `/api/experiments`，从而复现实验并审计模型来源。

切片推理使用常驻模型/模板缓存、内存切片和可配置 `tile_batch_size`，不再为每个切片重复校验模型、创建推理器或写入临时图片。数据版本冻结时会生成精确哈希和感知哈希，并阻断训练/测试跨集合重复、确认后的近重复以及同批次跨集合泄漏。历史数据版本会在首次训练时自动补算质量门禁。前端“模型实验”页提供数据版本、清单哈希、门禁错误/警告、模型指标对比和完整实验血缘。

```bash
pytest -q
```

测试覆盖产品创建、参考图上传、模型训练、异常检测、人工复核、报告导出、产线工位追溯、重复SN拦截、质量告警、SPC、角色越权、MES事件和在线备份。

PostgreSQL并发测试通过 `PCB_QMS_TEST_DATABASE_URL` 启用，会并发执行64次写入并验证事务回滚：

```bash
PCB_QMS_TEST_DATABASE_URL=postgresql+psycopg://pcb_qms:password@127.0.0.1:5433/pcb_qms pytest -q tests/test_postgresql_integration.py
```

## 主要API

| 方法 | 地址 | 说明 |
|---|---|---|
| `POST` | `/api/products` | 创建产品型号 |
| `POST` | `/api/products/{id}/references` | 上传正常参考图 |
| `POST` | `/api/products/{id}/models/train` | 创建训练任务 |
| `GET` | `/api/training-jobs/{id}` | 查询训练进度 |
| `POST` | `/api/products/{id}/inspect` | 单张产品检测 |
| `POST` | `/api/products/{id}/batch-inspect` | 批次检测 |
| `PATCH` | `/api/inspections/{id}/review` | 人工复核 |
| `GET` | `/api/reports/inspections.csv` | 导出质量报告 |
| `GET` | `/api/audit-logs` | 查询审计日志 |
| `POST` | `/api/production-lines` | 创建生产线 |
| `POST` | `/api/stations` | 创建检测工位 |
| `POST` | `/api/quality-rules` | 创建异常率和连续异常规则 |
| `GET` | `/api/quality-alerts` | 查询质量告警 |
| `GET` | `/api/products/{id}/spc` | 查询SPC控制数据 |
| `GET` | `/api/integration/events` | MES/QMS拉取待交付事件 |
| `PATCH` | `/api/integration/events/{id}/delivered` | 确认事件已被外部系统接收 |
| `POST` | `/api/admin/backups` | 创建PostgreSQL逻辑备份 |

## 工业化边界

本项目可以作为企业级系统的工程基础，但在真实产线上线前仍需要：

1. 使用目标产线相机、镜头和光源重新采样；
2. 通过缺陷样本验证漏检率，并按质量成本校准阈值；
3. 按现场协议将现有MES事件接口对接到MES、PLC和产品拦截设备；
4. 对模型漂移、相机偏移和光照变化建立监控；
5. 为PostgreSQL、MinIO和Redis配置高可用、监控与容量告警；
6. 集中部署时完成高可用、异地灾难恢复、渗透测试及组织统一身份认证。

## 开源与协作

本项目自身代码采用 [MIT License](LICENSE)。第三方组件及公开数据不随本许可证重新授权，具体边界见 [第三方声明](THIRD_PARTY_NOTICES.md)。提交代码前请阅读 [贡献指南](CONTRIBUTING.md)，安全问题请按 [安全策略](SECURITY.md) 私下报告。版本变化记录在 [CHANGELOG](CHANGELOG.md)，上传或发布前可逐项检查 [GitHub Release Checklist](docs/RELEASE_CHECKLIST.md)，仓库简介、Topics 与首版 Release 文案见 [GitHub Metadata](docs/GITHUB_METADATA.md)。
