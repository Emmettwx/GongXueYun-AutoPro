import json, time, requests, os, urllib3, urllib.parse, urllib.request, smtplib
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from modules.map_headers import header_user_agent
from modules.crypto_aes import aes_encrypt, create_sign

# 全局收集 304 安全验证详情
latest_auth_info = {}

# 仓库所有者兜底（环境变量取不到时使用），确保 @提及 与指派一定能触发通知
DEFAULT_GITHUB_OWNER = "Emmettwx"


def _smtp_notify(title, content):
    """检测到人脸验证时立即发 SMTP 邮件（不依赖主程序末尾的汇总邮件）。"""
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
    recipient = os.getenv("SMTP_TO", "").strip() or smtp_user
    if not smtp_user or not smtp_password or not recipient:
        print("[SMTP_NOTIFY] 未配置完整 SMTP（SMTP_USER/SMTP_PASSWORD/SMTP_TO），跳过即时邮件", flush=True)
        return False
    try:
        port = int((os.getenv("SMTP_PORT") or "465").strip() or "465")
    except ValueError:
        port = 465
    server = os.getenv("SMTP_SERVER", "smtp.qq.com").strip()
    try:
        msg = MIMEText(content, "plain", "utf-8")
        # QQ/163 等要求 From 为合法 RFC5322 地址，不能用 Header 包裹纯地址
        msg["From"] = formataddr(("GongXueYun", smtp_user))
        msg["To"] = recipient
        msg["Subject"] = Header(title, "utf-8")
        if port == 465:
            client = smtplib.SMTP_SSL(server, port, timeout=15)
        else:
            client = smtplib.SMTP(server, port, timeout=15)
            client.starttls()
        client.login(smtp_user, smtp_password)
        client.sendmail(smtp_user, [recipient], msg.as_string())
        client.quit()
        print(f"[SMTP_NOTIFY] 即时验证邮件已发送至 {recipient}", flush=True)
        return True
    except Exception as e:
        print(f"[SMTP_NOTIFY_WARN] 即时验证邮件发送失败: {e}", flush=True)
        return False


def _build_pages_jump_url(reg_url):
    """构造 GitHub Pages 中转页链接（手机点普通 https 链接即可唤起支付宝）。"""
    gh_repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    if not gh_repo or "/" not in gh_repo:
        return ""
    owner, repo_name = gh_repo.split("/", 1)
    return f"https://{owner}.github.io/{repo_name}/jump.html?url={urllib.parse.quote(reg_url)}"

def get_saved_out_reg(user=None):
    if user and user.get("outRegisterNo"):
        return str(user["outRegisterNo"]).strip()
    env_no = os.getenv("OUT_REGISTER_NO", "").strip()
    if env_no:
        return env_no
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    txt_path = os.path.join(base_dir, "out_register_no.txt")
    if os.path.exists(txt_path):
        try:
            with open(txt_path, "r", encoding="utf-8") as f:
                c = f.read().strip()
                if c:
                    return c
        except Exception:
            pass
    return ""

def persist_out_reg(out_no):
    if not out_no:
        return
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    txt_path = os.path.join(base_dir, "out_register_no.txt")
    try:
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(out_no)
    except Exception:
        pass

def download_alipay_qr(reg_url):
    try:
        encoded = urllib.parse.quote(reg_url)
        api_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={encoded}"
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        local_qr = os.path.join(base_dir, "alipay_qr.png")
        urllib.request.urlretrieve(api_url, local_qr)
        return local_qr
    except Exception as e:
        print(f"[ALIPAY_QR_ERROR] 下载二维码失败: {e}", flush=True)
        return None

def popup_windows_notice(title, message, timeout_sec=12):
    if os.name != "nt" or os.getenv("GITHUB_ACTIONS"):
        return
    try:
        import subprocess
        ps_cmd = (
            f"$ws = New-Object -ComObject WScript.Shell; "
            f"$null = $ws.Popup('{message}', {timeout_sec}, '{title}', 4160)"
        )
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_cmd],
            creationflags=0x08000000
        )
    except Exception:
        pass

def dispatch_cloud_notice(out_no, reg_url, sign_type="打卡", is_refresh=False):
    """
    云端/本地多渠道即时推送实名核验链接：
    1. GitHub Issue 自动触发邮件和手机 App 推送 (无需额外密码)
    2. PushPlus 微信公众号推送
    3. Server酱推送
    4. SMTP 邮件直发 (如有 SMTP_PASSWORD)
    """
    issue_id = None
    gh_token = os.getenv("GITHUB_TOKEN")
    gh_repo = os.getenv("GITHUB_REPOSITORY")

    # 1. 尝试通过 GitHub Issue 触发官方邮件推送
    if gh_token and gh_repo:
        try:
            gh_headers = {
                "Authorization": f"token {gh_token}",
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "GongXueYun-Notifier"
            }
            issue_title = f"【工学云{sign_type}】请在手机上点击链接完成人脸安全验证"
            if is_refresh:
                issue_title = f"【工学云{sign_type}】验证已自动续期，请用最新链接完成人脸核验"
            pages_url = _build_pages_jump_url(reg_url)
            pages_block = (
                f"\n\n- **👉👉 手机点此链接唤起支付宝（推荐）**:\n{pages_url}\n"
                f"> 若微信内打不开，请点右上角【···】选择【在浏览器打开】\n"
            ) if pages_url else ""
            # 通知关键：GitHub 不给自己创建的内容发通知，必须 @ 自己或指派给自己
            gh_actor = (os.getenv("GITHUB_ACTOR") or "").strip()
            gh_owner = (os.getenv("GITHUB_REPOSITORY_OWNER") or (gh_repo.split("/")[0] if gh_repo else "")).strip()
            mention_target = gh_actor or gh_owner or DEFAULT_GITHUB_OWNER
            mention_line = f"@{mention_target} ⚠️ 请立即处理，本条通过@提及触发通知\n\n" if mention_target else ""
            issue_body = (
                f"{mention_line}"
                f"## 📱 工学云打卡安全验证提醒\n\n"
                f"GitHub Actions 云端正在执行工学云 **{sign_type}** 任务。\n"
                f"因工学云风控检测到云服务器访问，需进行一次手机实名/人脸核验：\n\n"
                f"- **最新验证单号**: `{out_no}`\n"
                f"- **👉 支付宝核验直跳链接（支付宝内打开）**:\n```text\n{reg_url}\n```\n"
                f"> ⏱️ **云端后台已开启长效自动续期（总计最长等待 30 分钟）**\n"
                f"> 平台官方单号每 5 分钟失效一次，若过期云端将**自动刷新单号并更新通知**。\n"
                f"> 您在手机上随时点击**最新链接**在支付宝完成人脸核验，云端将**自动完成打卡**并关闭本通知！" + pages_block
            )
            payload = {"title": issue_title, "body": issue_body}
            if mention_target:
                payload["assignees"] = [mention_target]
            r = requests.post(
                f"https://api.github.com/repos/{gh_repo}/issues",
                headers=gh_headers,
                json=payload,
                timeout=10
            )
            # 若因权限不足导致指派失败（422），去掉 assignees 重试，保证 Issue 一定能建出来
            if r.status_code == 422 and "assignees" in payload:
                print("[CLOUD_NOTICE_WARN] 指派失败，改为仅 @提及方式重试", flush=True)
                payload.pop("assignees", None)
                r = requests.post(
                    f"https://api.github.com/repos/{gh_repo}/issues",
                    headers=gh_headers,
                    json=payload,
                    timeout=10
                )
            if r.status_code == 201:
                issue_id = r.json().get("number")
                print(f"[CLOUD_NOTICE] GitHub Issue #{issue_id} 创建成功，已自动发送邮件/移动端通知！", flush=True)
            else:
                print(f"[CLOUD_NOTICE_WARN] 创建 GitHub Issue 响应码 {r.status_code}: {r.text}", flush=True)
        except Exception as e:
            print(f"[CLOUD_NOTICE_ERROR] GitHub Issue 创建异常: {e}", flush=True)

    # 2. 尝试 PushPlus 微信推送
    push_key = os.getenv("PUSHPLUS_TOKEN") or os.getenv("SECRET") or ""
    if push_key:
        try:
            pp_url = "https://www.pushplus.plus/send"
            encoded_url = urllib.parse.quote(reg_url)
            alipay_ds_url = f"https://ds.alipay.com/?scheme={encoded_url}"
            gh_repo_env = os.getenv("GITHUB_REPOSITORY", "").strip()
            pages_block = ""
            if gh_repo_env and "/" in gh_repo_env:
                owner, repo_name = gh_repo_env.split("/", 1)
                pages_jump_url = f"https://{owner}.github.io/{repo_name}/jump.html?url={encoded_url}"
                pages_block = (
                    f"<div style='background:#fffbe6;border:1px solid #ffe58f;border-radius:8px;padding:10px 12px;margin:12px 0;font-size:13px;color:#ad6800;text-align:left;'>"
                    f"  <b>📌 备用跳转（中转页）：</b><br>"
                    f"  <a href='{pages_jump_url}'>👉 点此前往打卡中转页</a><br>"
                    f"  若微信提示被拦截，只需点击右上角 <b>【···】</b> 选择 <b>【在浏览器打开】</b> 即可秒进支付宝！"
                    f"</div>"
                )
            refresh_badge = "（已自动续期最新链接）" if is_refresh else ""
            content_html = (
                f"<h3>工学云{sign_type}安全核验提醒{refresh_badge}</h3>"
                f"<p style='color:#666;'>检测到需要支付宝人脸/实名验证，云端后台已开启 <b>自动续期轮询</b>（最长等待 30 分钟）：</p>"
                f"<div style='background:#f0f5ff;border:1px solid #adc6ff;border-radius:10px;padding:14px;margin:12px 0;text-align:center;'>"
                f"  <a href='{alipay_ds_url}' style='display:inline-block;background:#1677ff;color:#ffffff;font-size:16px;font-weight:bold;padding:12px 24px;border-radius:24px;text-decoration:none;'>👉 点击直跳支付宝刷脸</a>"
                f"</div>"
                f"{pages_block}"
                f"<p style='font-size:12px;color:#8c8c8c;'>最新单号: {out_no}<br>如超 5 分钟未刷脸，云端会自动刷新链接。请随时点击最新一条通知完成刷脸，刷脸后数秒内自动打卡入库。</p>"
            )
            r = requests.post(pp_url, json={
                "token": push_key,
                "title": f"工学云{sign_type}安全验证{refresh_badge}",
                "content": content_html,
                "template": "html"
            }, timeout=10)
            print(f"[CLOUD_NOTICE] PushPlus 微信通知发送结果: {r.text}", flush=True)
        except Exception as e:
            print(f"[CLOUD_NOTICE_WARN] PushPlus 推送失败: {e}", flush=True)

    # 3. 尝试 Server酱 推送
    send_key = os.getenv("SENDKEY")
    if send_key and send_key != push_key:
        try:
            s_url = f"https://sctapi.ftqq.com/{send_key}.send"
            requests.post(s_url, data={
                "title": f"工学云{sign_type}需要安全验证",
                "desp": f"请点击完成验证: [前往支付宝]({reg_url})\n\n单号: {out_no}"
            }, timeout=10)
            print("[CLOUD_NOTICE] Server酱通知已发送", flush=True)
        except Exception:
            pass

    # 5. 即时 SMTP 邮件通知（不必等 30 分钟轮询结束，含中转页链接）
    email_body = (
        f"工学云{sign_type}需要支付宝人脸/实名安全验证。\n\n"
        f"最新验证单号: {out_no}\n"
        f"手机点此链接唤起支付宝（推荐）: {_build_pages_jump_url(reg_url) or reg_url}\n\n"
        f"若打不开，请复制以下原始链接到手机支付宝打开:\n{reg_url}\n\n"
        f"云端已开启自动续期轮询，最长等待 30 分钟；单号每 5 分钟过期会自动刷新并推送新链接。"
    )
    _smtp_notify(f"【工学云】{sign_type}需要安全验证（请及时刷脸）", email_body)
    return issue_id

# 保存用户的签到信息
def save_user_info(
    user,
    userId,
    token,
    planId,
    province,
    address,
    signType="START",
    device="Android",
    latitude=None,
    longitude=None
):
    global latest_auth_info
    url = "https://api.moguding.net:9000/attendence/clock/v6/save"
    text = device + signType + planId + userId + address
    headers = {
        "roleKey": "student",
        "version": "v6.0",
        "user-agent": "Dart/3.7 (dart:io)",
        "appversion": "6.0.0",
        "device": "Android",
        "accept-encoding": "gzip",
        "host": "api.moguding.net:9000",
        "sign": create_sign(text),
        "authorization": token,
        "content-type": "application/json; charset=utf-8"
    }
    session = requests.Session()
    out_reg = get_saved_out_reg(user)
    data = {
        "country": "中国",
        "address": address,
        "province": province,
        "city": user.get("city", ""),
        "area": user.get("area", ""),
        "latitude": latitude,
        "description": user.get("desc", ""),
        "planId": planId,
        "type": signType,
        "device": device,
        "longitude": longitude,
        "state": "NORMAL",
        "attendanceType": "",
        "keys": {},
        "t": aes_encrypt(str(int(time.time() * 1000))),
        "sign": create_sign(text),
        "outRegisterNo": out_reg if out_reg else None
    }

    res = session.post(url, headers=headers, json=data, verify=False)
    res_text = res.text
    print(f"[CLOCK_API_FIRST] status={res.status_code} body={res.text}", flush=True)

    if "304" in res.text or "验证已过期" in res.text or "人脸核验失败" in res.text:
        h2 = headers.copy()
        h2["client_id"] = "428a8310cd442757ae699df5d89vmgh"
        alipay_url = url.replace("/attendence/clock/v6/save", "/usercenter/alipay/v1/createAxdjk")

        def fetch_new_alipay_ticket():
            try:
                r_create = session.post(alipay_url, headers=h2, json={}, verify=False)
                j = r_create.json()
                print(f"[ALIPAY_304_CREATE] {r_create.text}", flush=True)
                if j.get("code") == 200 and isinstance(j.get("data"), dict):
                    data_dict = j["data"]
                    return data_dict.get("outRegisterNo"), data_dict.get("registerUrl")
            except Exception as ex:
                print(f"[ALIPAY_304_CREATE_ERROR] {ex}", flush=True)
            return None, None

        try:
            out_no, reg_url = fetch_new_alipay_ticket()
            if out_no and reg_url:
                latest_auth_info = {
                    "outRegisterNo": out_no,
                    "registerUrl": reg_url
                }
                persist_out_reg(out_no)
                local_qr = download_alipay_qr(reg_url)
                print(f"\n[ALIPAY_AUTH_NOTICE] >>> 请在手机支付宝打开以下链接完成实名安全验证 <<<", flush=True)
                print(f"[ALIPAY_AUTH_NOTICE] 验证单号: {out_no}", flush=True)
                print(f"[ALIPAY_AUTH_NOTICE] 验证链接: {reg_url}\n", flush=True)

                # 1. 本地 Windows 弹窗与展示二维码
                if os.name == "nt" and not os.getenv("GITHUB_ACTIONS"):
                    if local_qr and os.path.exists(local_qr):
                        try:
                            os.startfile(local_qr)
                        except Exception:
                            pass
                    popup_windows_notice(
                        "工学云打卡 - 需支付宝扫码验证",
                        "检测到当前打卡需要一次支付宝实名验证！\\n\\n已为您自动打开二维码图片，请用【手机支付宝】扫一扫完成验证。\\n验证完成后程序将在数秒内【自动完成打卡】，无需手动操作！",
                        timeout_sec=25
                    )

                # 2. 触发云端通知 (GitHub Issue 邮件 / PushPlus 微信 / Server酱)
                display_type = "上班" if signType == "START" else "下班"
                issue_id = dispatch_cloud_notice(out_no, reg_url, sign_type=display_type, is_refresh=False)
                active_issue_ids = [issue_id] if issue_id else []

                # 3. 统一轮询等待用户在手机端完成验证 (最长等待 30 分钟，遇单号过期自动无缝续期)
                print("[ALIPAY_AUTO_POLL] 正在等待手机支付宝完成验证 (长效自动续期，最长等待 30 分钟，每 8 秒自动检测一次)...", flush=True)
                verified_success = False
                current_ticket_start = time.time()
                overall_start = time.time()

                attempt = 0
                while time.time() - overall_start < 1800:
                    attempt += 1
                    time.sleep(8)
                    data["outRegisterNo"] = out_no
                    data["t"] = aes_encrypt(str(int(time.time() * 1000)))
                    data["sign"] = create_sign(text)
                    res_poll = session.post(url, headers=headers, json=data, verify=False)
                    
                    if attempt % 5 == 0 or '"code":200' in res_poll.text:
                        elapsed_total = int(time.time() - overall_start)
                        print(f"[ALIPAY_AUTO_POLL #{attempt} / {elapsed_total}s] status={res_poll.status_code} body={res_poll.text}", flush=True)

                    # 成功入库
                    if '"code":200' in res_poll.text and '"success"' in res_poll.text and "304" not in res_poll.text:
                        res = res_poll
                        res_text = res_poll.text
                        latest_auth_info = {}
                        verified_success = True
                        popup_windows_notice(
                            "工学云自动打卡成功",
                            f"已检测到支付宝验证通过，工学云打卡已自动完成！\\n响应: {res_text}",
                            timeout_sec=15
                        )
                        break

                    # 检测单号是否已在工学云服务端过期（或当前单号已持续超过 280 秒）
                    ticket_age = time.time() - current_ticket_start
                    is_expired = "验证已过期" in res_poll.text or ticket_age >= 280

                    if is_expired and (time.time() - overall_start < 1700):
                        print(f"[ALIPAY_AUTO_REFRESH] 检测到单号 {out_no} 已过期/临近过期 (已用时 {int(ticket_age)}s)，正在自动申请新单号...", flush=True)
                        new_out_no, new_reg_url = fetch_new_alipay_ticket()
                        if new_out_no and new_reg_url:
                            out_no = new_out_no
                            reg_url = new_reg_url
                            current_ticket_start = time.time()
                            latest_auth_info = {
                                "outRegisterNo": out_no,
                                "registerUrl": reg_url
                            }
                            persist_out_reg(out_no)
                            print(f"[ALIPAY_AUTO_REFRESH] >>> 单号自动续期成功！最新单号: {out_no} <<<", flush=True)
                            print(f"[ALIPAY_AUTO_REFRESH] 最新验证链接: {reg_url}", flush=True)

                            # 重新推送最新通知，并自动关闭刚过期的上一条 Issue，避免点错旧链接
                            new_issue_id = dispatch_cloud_notice(out_no, reg_url, sign_type=display_type, is_refresh=True)
                            if new_issue_id:
                                gh_token_tmp = os.getenv("GITHUB_TOKEN")
                                gh_repo_tmp = os.getenv("GITHUB_REPOSITORY")
                                if gh_token_tmp and gh_repo_tmp and active_issue_ids:
                                    gh_h = {
                                        "Authorization": f"token {gh_token_tmp}",
                                        "Accept": "application/vnd.github.v3+json",
                                        "User-Agent": "GongXueYun-Notifier"
                                    }
                                    for old_id in list(active_issue_ids):
                                        try:
                                            requests.post(
                                                f"https://api.github.com/repos/{gh_repo_tmp}/issues/{old_id}/comments",
                                                headers=gh_h,
                                                json={"body": f"🔄 该验证单号已过期，云端已自动续期并生成最新链接，请移步至最新 Issue #{new_issue_id} 完成人脸核验。"},
                                                timeout=8
                                            )
                                            requests.patch(
                                                f"https://api.github.com/repos/{gh_repo_tmp}/issues/{old_id}",
                                                headers=gh_h,
                                                json={"state": "closed"},
                                                timeout=8
                                            )
                                            active_issue_ids.remove(old_id)
                                        except Exception:
                                            pass
                                active_issue_ids.append(new_issue_id)
                        else:
                            print(f"[ALIPAY_AUTO_REFRESH_WARN] 申请新单号失败，将在下个周期重试", flush=True)

                # 4. 若通过 GitHub Issue 发起通知，打卡完成后自动更新并关闭相关的 Issue
                gh_token = os.getenv("GITHUB_TOKEN")
                gh_repo = os.getenv("GITHUB_REPOSITORY")
                if gh_token and gh_repo and active_issue_ids:
                    try:
                        gh_headers = {
                            "Authorization": f"token {gh_token}",
                            "Accept": "application/vnd.github.v3+json",
                            "User-Agent": "GongXueYun-Notifier"
                        }
                        for single_issue_id in active_issue_ids:
                            if verified_success:
                                requests.post(
                                    f"https://api.github.com/repos/{gh_repo}/issues/{single_issue_id}/comments",
                                    headers=gh_headers,
                                    json={"body": f"✅ **工学云{display_type}打卡成功！**\n\n已检测到支付宝实名核验通过，后台已自动完成签到提交。"},
                                    timeout=10
                                )
                                requests.patch(
                                    f"https://api.github.com/repos/{gh_repo}/issues/{single_issue_id}",
                                    headers=gh_headers,
                                    json={"state": "closed"},
                                    timeout=10
                                )
                            else:
                                requests.post(
                                    f"https://api.github.com/repos/{gh_repo}/issues/{single_issue_id}/comments",
                                    headers=gh_headers,
                                    json={"body": f"⚠️ **等待超时（超过30分钟）**\n\n30分钟内未在手机上完成最新支付宝人脸验证，本次打卡任务已结束。"},
                                    timeout=10
                                )
                                requests.patch(
                                    f"https://api.github.com/repos/{gh_repo}/issues/{single_issue_id}",
                                    headers=gh_headers,
                                    json={"state": "closed"},
                                    timeout=10
                                )
                    except Exception as e:
                        print(f"[CLOUD_NOTICE_WARN] 批量更新 GitHub Issue 失败: {e}", flush=True)
        except Exception as e:
            print(f"[ALIPAY_304_CREATE_ERROR] {e}", flush=True)

    return res, res_text, res
