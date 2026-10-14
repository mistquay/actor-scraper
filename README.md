# Actor Scraper

面向 FYMS / Emby 的独立演员资料与图片刮削工具。FastAPI 和原生前端在一个容器中运行，保留命令行入口。

- 管理台配置连接、代理、并发、缓存、图片来源和更新策略。
- 工作台创建预览或回写任务；任务记录查看进度、停止运行、重试失败和未完成演员。
- 任务记录支持演员姓名／任务编号搜索、状态／模式／创建日期组合筛选、时间排序与分页，覆盖全部保留记录；演员结果可按姓名与处理状态筛选。
- 查看演员来源档案、主图与背景预览，按需导出 JSONL。
- 默认只补图片、保留已有图；文字资料补全和图片替换均可在配置页开启。
- 每个任务冻结配置和演员范围。重试保留原策略；API 密钥可轮换，FYMS 地址变化时需新建任务。

## Docker 启动

镜像：`ghcr.io/mistquay/actor-scraper:latest`，支持 `linux/amd64` 和 `linux/arm64`。

```bash
git clone https://github.com/mistquay/actor-scraper.git
cd actor-scraper
docker compose up -d
cat data/admin-password
```

浏览器访问 `http://服务器IP:8891`，使用生成的管理密码登录。也可在首次启动前自行设置密码：

```bash
cp .env.example .env
nano .env
docker compose up -d
```

将 `.env` 中 `ACTOR_ADMIN_PASSWORD` 改为自己的密码；设置后不会生成 `admin-password` 文件。更改环境变量后使用 `docker compose up -d --force-recreate`。管理密码、数据目录和端口由部署配置管理，其余运行配置在前端保存。

不克隆代码也可直接运行：

```bash
mkdir -p ~/actor-scraper/data
docker run -d --name actor-scraper --restart unless-stopped \
  -p 8891:8891 -v "$HOME/actor-scraper/data:/data" \
  ghcr.io/mistquay/actor-scraper:latest
cat ~/actor-scraper/data/admin-password
```

升级：

```bash
docker compose pull
docker compose up -d
```

## 管理台使用

1. 在「连接与配置」填入 FYMS / Emby 地址、API 密钥、可选用户 ID，保存并测试连接。密钥留空会保留已保存值，读取配置不会返回原密钥。
2. 保留默认 GRAPHIS 优先、经典写真、下载预览图片，先用少量姓名创建预览任务。
3. 点击演员结果，核对来源资料、写真版本、主图与背景。预览不会更新媒体服务器。
4. 需要更新已有头像或背景时，分别开启替换选项。需要文字资料时，取消「只补图片」；开启「覆盖已有文字资料」才覆盖来源有值的字段。只补缺时，关闭所有覆盖和替换选项；工作台会分别显示文字、头像、背景的策略。
5. 创建「执行回写」任务。回写后读取服务器校验持久化结果，失败和部分完成会单独显示。

「全部演员」第一次勾选会填写 10 位上限，清空上限可处理全部选择。一次仅运行一个任务；改变配置不影响已经启动的任务。停止操作会等待当前请求结束，已经保存的结果和缓存保留。

前端的档案和图片表示来源及本次选图；是否已写入服务器，以结果中的回写状态为准。工具更新现有演员，不创建或合并演员实体。

## 数据源与速度

- **AV-LEAGUE**：演员档案、别名、生日、身高、三围、罩杯、出道日期等，具体取决于来源是否提供。
- **GRAPHIS**：公开演员写真页面的 `prof.jpg` 主图和 `model.jpg` 背景，优先选择同一写真版本。
- **Gfriends**：演员头像候选。自动模式会优先选择其中的 GRAPHIS 版本，并回退档案头像；Gfriends 也包含 GRAPHIS 图片，不能只按图库名称判断画质。
- 人工映射可指定档案 ID 或 GRAPHIS 写真 ID，例如 `{"三上悠亚":"200110_yua-mikami4"}`。

默认 4 位演员并发、每个外部主机 2 请求/秒、10 秒超时、GET 额外重试 1 次。只查指定演员时直接搜索，避免拉取全部演员库；档案、别名、选图和图片缓存跨任务复用。未匹配也短期缓存，403 / 429 不立即重试。缓存命中可减少网络请求；首次全量速度仍取决于源站、代理和服务器响应，任务统计会记录请求数与耗时。

## 数据目录

Docker 中固定为 `/data`，Compose 挂载到项目的 `./data`。Python 安装默认使用安装目录旁的 `data`，推荐显式设置 `ACTOR_DATA_DIR` 或 `--data-dir`。

```text
data/
  state.sqlite       # 配置、任务、演员结果
  cache/             # 来源与图片缓存
  logs/app.log       # Web 轮转日志
  exports/           # 手动 JSONL 导出
  admin-password     # 未指定管理密码时自动生成
```

**正常任务不生成 JSONL。** Web 只在点击导出时生成 `exports/job-ID.jsonl`；CLI 相对输出路径也自动进入 `exports`。显式绝对路径按用户指定位置导出。

任务历史默认保留 30 天，启动服务和新建任务时清理过期记录及对应 Web 导出；图片缓存保留复用。日志为 5 MB × 4 份轮转，`docker logs actor-scraper` 可查看服务输出。

备份完整 `data` 可保留配置、结果与缓存；其中含 API 密钥和管理密码，不应公开或提交 Git。同一数据目录只启动一个 Web 服务，CLI 与 Web 任务应依次运行；服务重启会把运行中的记录标为中断，随后可重试未完成部分。

## Python / CLI

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
export ACTOR_DATA_DIR="$HOME/actor-scraper/data"
actor-scraper-web --host 0.0.0.0 --port 8891
```

CLI 与管理台使用相同数据目录和已保存配置。没有配置服务器时，也可按姓名获取来源资料。

```bash
# 预览，自动保存 SQLite；不生成 JSONL
actor-scraper --name '三上悠亚' --name '桥本有菜'

# 手动导出到 data/exports/preview.jsonl
actor-scraper --name '三上悠亚' --output preview.jsonl

# 只处理头像和背景，明确执行回写
actor-scraper --name '三上悠亚' --images-only --apply

# 补全文字资料和图片；默认仅补缺失文字
actor-scraper --name '三上悠亚' --with-metadata --apply

# 小批量检查服务器演员
actor-scraper --all --limit 10

actor-scraper --help
```

也支持 `FYMS_URL`、`FYMS_USER_ID`、`FYMS_API_KEY` 环境变量。通过环境变量或前端设置 API 密钥，不在命令参数中传递。

容器内 CLI：`docker compose exec actor-scraper actor-scraper --name '三上悠亚'`。后台任务完成后再运行 CLI。

## 从旧 actor_scraper 迁移

旧目录 `~/actor_scraper` 与新项目 `~/actor-scraper` 分开保留。先停止旧刮削任务，然后启动新项目、在前端保存连接和策略即可。无需移动影片、NFO 或原有 FYMS 数据。

如旧工具使用 `~/actor_scraper/.cache`，可复制缓存复用；若当时指定过 `--cache-dir`，改成该实际路径：

```bash
mkdir -p ~/actor-scraper/data/cache ~/actor-scraper/data/exports
cp -a ~/actor_scraper/.cache/. ~/actor-scraper/data/cache/
```

根目录中已确认属于旧工具的导出可手动归档，例如：

```bash
mv ~/preview.jsonl ~/actor-scraper/data/exports/legacy-preview.jsonl
```

不需要批量清理根目录 JSONL。旧导出是历史文件，不会自动导入新任务数据库。

## 开发与镜像发布

```bash
pip install -e '.[dev]'
ruff check .
pytest -q
```

GitHub Actions 在 `main` 推送、`v*` 标签和手动触发时运行检查并构建两种架构。`main` 发布 `latest`，标签发布对应版本，提交也生成 `sha-*` 镜像标签。PR 只运行检查。

首次发布后需要在 GitHub Package 设置中将包可见性设为 Public；公开仓库不会自动让首次创建的 GHCR 包公开。
