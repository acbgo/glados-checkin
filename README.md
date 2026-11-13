# GLaDOS 多账号签到

支持 `railgun.info` 和 GLaDOS 多账号签到、PushPlus 通知，以及 Streamlit 管理。

## 配置

复制 `config.example.json`，填写各账号的名称、域名和 Cookie，以及 PushPlus token。GLaDOS 的 `user_agent` 建议填写登录浏览器的 User-Agent。

`accounts` 数组中的每个对象代表一个账号，填写各自的 Cookie；`name` 不能重复。`domain` 按尝试顺序填写数组，例如 `["glados.cloud", "glados.rocks", "glados.network"]`；只使用一个域名时填写 `["railgun.info"]`。GLaDOS 和 railgun 域名不能混放在同一账号中。

| 配置项 | 说明 |
| --- | --- |
| `domain` | 域名数组，按顺序尝试；备用域名会收到该账号的 Cookie，认证被拒绝时停止切换 |
| `exchange_plan` | 自动兑换，默认 `off`；可选 `plan100`（100 分/10 天）、`plan200`（200 分/30 天）、`plan500`（500 分/100 天） |
| `notification_mode` | `always` 每次推送；`fail_only` 仅失败或发生兑换时推送 |
| `pushplus_token` | PushPlus token，留空关闭推送 |
| `streamlit_admin_password` | 管理页密码；仅使用 Actions 时可留空 |

兑换会消耗积分；实际档位以账号控制台为准。示例文件只放占位数据，真实凭据请保存在本地配置或 GitHub Secret 中。

## GitHub Actions

在仓库 **Settings → Secrets and variables → Actions → Repository secrets** 中创建 `GLADOS_CONFIG`，粘贴填写后的完整 JSON。每天约北京时间 09:30 自动运行，也可在 Actions 手动运行，选择 `diagnose_only` 先检查登录状态。

保活工作流每月会自动提交一次时间戳。

## 服务器

需要 Python 3.11 或更新版本：

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.local.json
```

填写 `config.local.json`，在 `streamlit_admin_password` 中直接设置管理页密码，并限制文件权限：`chmod 600 config.local.json`。管理页修改配置时会写入该文件。

```bash
python checkin.py           # 手动签到
python checkin.py diagnose  # 只检查登录状态
streamlit run app.py --server.address 127.0.0.1
```

公网访问管理页时，请使用 HTTPS 反向代理并限制登录请求频率。只读诊断不签到、不兑换、不推送，且不能验证签到设备校验是否通过。

管理页侧边栏可切换主页、运行日志和修改配置。运行日志读取项目目录中的 `checkin.log`，默认显示最近 50 行，可逐步展开；单次最多读取文件末尾 1 MB。cron 输出需要重定向到该文件，页面中的手动签到结果显示在主页。

使用 cron 定时运行，例如服务器本地时间每天 09:30：

```cron
30 9 * * * cd /path/to/glados-checkin && /path/to/glados-checkin/.venv/bin/python checkin.py >> /path/to/glados-checkin/checkin.log 2>&1
```

## 测试

```bash
python -m unittest discover -s tests -v
```

离线测试不能验证线上签到或积分到账，首次使用请核对官网记录。原有 `glados.py` 暂时保留供对照。
