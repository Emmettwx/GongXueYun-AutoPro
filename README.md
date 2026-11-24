# GongXueYun-AutoPro（工学云 / 蘑菇丁全自动打卡与周月报助手）

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![GitHub Actions](https://img.shields.io/badge/GitHub%20Actions-Automated-2ea44f.svg)](https://github.com/features/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

基于 **GitHub Actions** 零服务器成本运行的工学云（蘑菇丁）全自动考勤与实习报告管理项目，原生适配最新 **v6.0 API**、滑块自动过验、支付宝 304 人脸风控 30 分钟长效自动续期轮询、法定节假日智能跳过以及周报问卷自动勾选。

---

## ✨ 核心特性

- **零服务器云端托管**：基于 GitHub Actions 定时运行，电脑关机、断网照常执行。
- **双调度防漏打保底**：
  - 上班打卡：`09:30` 正常打卡 + `11:15` 二次保底补签；
  - 下班打卡：`17:30` 正常打卡 + `19:30` 二次保底补签；
  - 已打卡记录精准判重跳过（带 AES-128-ECB 时间戳与当日日期双重校验），并发锁防止重入冲突。
- **304 人脸风控 30 分钟长效自动续期**：
  - 遇到支付宝实名/人脸核验时，自动通过 **GitHub Issue + PushPlus 微信 + Server酱** 推送手机直跳链接；
  - 云端自动轮询长达 30 分钟，单号每 ~5 分钟过期自动无缝申请新单号并关闭旧 Issue，避免点错过期链接；刷脸完成后数秒内自动入库并闭环关闭通知。
- **法定节假日智能跳过（支持单休/双休切换）**：
  - 自动对接法定节假日 API（双源 + 本地规则兜底），法定放假自动跳过、调休补班自动打卡；
  - 支持 `SATURDAY_CHECKIN` 开关（默认 `true` 周六照常打卡，双休用户设为 `false` 即可）。
- **防风控拟人化设计**：
  - 每次打卡自动叠加 20～60 米自然 GPS 坐标微漂移与 15～150 秒随机时间抖动。
- **智能周报 / 月报 & 问卷自动填写**：
  - 对接 `practice/paper/v6/save` 与 `practice/paper/v2/listByStu` 真实历史去重，自动延续周次序号；
  - 自动拉取学校周报/月报问卷模板（`practice/paper/v2/info`），默认全选第一项（可通过 `SURVEY_OPTION_INDEX` 调整）；
  - 自动检测未批阅周报中缺失问卷的记录，并通过 `practice/paper/v3/update` 原位补齐，不产生重复报告；
  - 支持对接 Gemini AI（`GEMINI_API_KEY`）生成定制正文，未配置时自动启用内置高质量轮换文库兜底。

---

## 🚀 快速部署（3 分钟上手）

### 第一步：Fork 本仓库
点击本页右上角 **Fork**，将项目复制到您自己的 GitHub 账号下。

### 第二步：配置 GitHub Secrets
进入您 Fork 后的仓库，点击 **Settings → Secrets and variables → Actions → New repository secret**，按需添加以下变量：

#### 1. 必填参数（账号与打卡定位）
| Secret 名称 | 说明 | 示例 |
| :--- | :--- | :--- |
| `PHONE` | 工学云登录手机号 | `13800138000` |
| `PASSWORD` | 工学云登录密码 | `YourPassword123` |
| `PROVINCE` | 打卡省份 | `北京市` |
| `CITY` | 打卡城市 | `北京市` |
| `AREA` | 打卡区/县 | `海淀区` |
| `ADDRESS` | 打卡详细地址 | `北京市海淀区中关村软件园` |
| `LONGITUDE` | 打卡地经度（建议保留 6 位小数） | `116.307484` |
| `LATITUDE` | 打卡地纬度（建议保留 6 位小数） | `40.057031` |

#### 2. 推荐配置（推送通知与个性化开关）
| Secret 名称 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `PUSHPLUS_TOKEN` | 无 | [PushPlus](https://www.pushplus.plus/) 微信推送 Token（强烈推荐，用于接收打卡结果与支付宝刷脸链接） |
| `SENDKEY` | 无 | Server酱推送 Key（可选） |
| `SATURDAY_CHECKIN` | `true` | 周六是否正常打卡：`true`（单休/周六打卡）或 `false`（双休跳过周六） |
| `SURVEY_OPTION_INDEX` | `0` | 周报/月报问卷默认勾选下标：`0` 表示所有题目默认选第 1 项（通常为 `a`），`1` 表示选第 2 项 |
| `REPORT_START_DATE` | 留空 | 可选补交起始日期（格式 `YYYY-MM-DD`，如 `2026-10-01`）；留空时默认仅维护当前活跃周与当月月报 |
| `GEMINI_API_KEY` | 无 | 可选 Google Gemini API Key，用于 AI 定制生成周报/月报正文；不填则自动使用内置优质模板库 |
| `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_TO` | 无 | 可选邮箱通知配置（另支持 `SMTP_SERVER`、`SMTP_PORT`） |
| `PROXY_URL` / `PROXY_API_URL` | 无 | 可选国内 HTTP 代理，用于降低海外云端 IP 触发 304 刷脸风控的概率 |

### 第三步：开启 GitHub Actions 并测试
1. 进入仓库顶部的 **Actions** 标签页，点击 **I understand my workflows, go ahead and enable them** 启用工作流。
2. 选中左侧的 **GongXueYun Auto CheckIn & Report**，点击右侧 **Run workflow** 即可手动测试：
   - `mode = auto`：完整执行打卡 + 周月报检查；
   - `mode = clock`：仅执行打卡；
   - `mode = report`：跳过打卡与随机延迟，快速测试周月报与问卷自动提交。

*(可选)* 如需在微信内使用备用中转页跳转支付宝，可在仓库 **Settings → Pages** 中将 Branch 设为 `main` 并保存开启 GitHub Pages，系统会自动识别您的 Pages 域名。

---

## 📁 目录结构

```text
.
├── .github/workflows/clock.yml   # GitHub Actions 定时调度与手动触发配置
├── gongxueyun_auto.py            # 主入口（节假日过滤、抖动延迟、打卡与周月报编排）
├── compat_adapter.py             # 环境变量与通知兼容适配层
├── jump.html                     # 支付宝核验链接中转页（适配 GitHub Pages）
├── modules/
│   ├── get_login_token.py        # v6.0 登录、OpenCV 滑块识别与长效会话缓存
│   ├── check_sign_in.py          # 当日打卡记录判重（含 AES 时间戳与日期校验）
│   ├── save_user_info.py         # v6.0 打卡提交、30 分钟人脸续期轮询与 Issue 闭环
│   ├── holiday_filter.py         # 法定节假日与单/双休判断
│   ├── report_manager.py         # 周月报自动去重、v6/save 提交与问卷首项勾选/原位补全
│   ├── ai_report_generator.py    # Gemini AI / 内置文库正文生成
│   ├── clean_issues.py           # 过期历史 Issue 自动清理
│   └── crypto_aes.py             # AES-128-ECB 加密与签名工具
└── requirements.txt              # Python 依赖清单
```

---

## ⚠️ 免责声明

本项目仅供学习交流 Python 自动化、GitHub Actions CI/CD 编排及接口加密通信原理使用，严禁用于任何违反学校或平台管理规定的用途。使用本项目所产生的一切后果由使用者自行承担。
