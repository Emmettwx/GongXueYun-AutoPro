# -*- coding: utf-8 -*-
"""
自动补签申请模块（借鉴 github123666/gonxueyun 的补签接口实现）。
流程：查询本月已打卡日期 -> 找出未打卡的日期（含周末节假日，不含今天）-> 逐日提交补签申请。
补签申请提交后需管理员在网页/APP审批，本模块只负责自动提交，不保证审批通过。
"""
import os
import json
import re
import time
import calendar
import requests
from datetime import datetime, timezone, timedelta
from modules.crypto_aes import create_sign, aes_encrypt

shanghai_tz = timezone(timedelta(hours=8))
BASE_URL = "https://api.moguding.net:9000"
# 补签回看范围：默认 0 = 整月全量（含周末和节假日，全部提交申请等管理员审批）；
# 如需只补最近 N 天可设具体数字，如 MAKEUP_LOOKBACK_DAYS=7
MAKEUP_LOOKBACK_DAYS = int((os.getenv("MAKEUP_LOOKBACK_DAYS") or "0").strip() or "0")


def _headers(token, user_id, sign_val):
    return {
        "Host": "api.moguding.net:9000",
        "authorization": token,
        "roleKey": "student",
        "rolekey": "student",
        "userid": str(user_id),
        "version": "v6.0",
        "appversion": "6.0.0",
        "device": "Android",
        "sign": sign_val,
        "content-type": "application/json; charset=utf-8",
        "user-agent": "Dart/3.7 (dart:io)",
    }


def _legacy_headers(token, user_id, sign_val):
    """补签接口 attendanceReplace/v2/save 是老接口，必须用老式请求头（Android 浏览器 UA，不带 v6 版本号）。"""
    return {
        "Host": "api.moguding.net:9000",
        "accept-language": "zh-CN,zh;q=0.8",
        "user-agent": "Mozilla/5.0 (Linux; U; Android 9; zh-cn; SM-G977N Build/LMY48Z) AppleWebKit/533.1 (KHTML, like Gecko) Version/5.0 Mobile Safari/533.1",
        "authorization": token,
        "rolekey": "student",
        "userid": str(user_id),
        "sign": sign_val,
        "content-type": "application/json; charset=UTF-8",
        "accept-encoding": "gzip",
        "cache-control": "no-cache",
    }

def get_undated_days(token, user_id, plan_id, sign_info):
    """查询本月已打卡日期，返回本月内未打卡且需要补签的日期字符串列表。"""
    now = datetime.now(shanghai_tz)
    month_prefix = now.strftime("%Y-%m-")
    day_count = calendar.monthrange(now.year, now.month)[1]
    start_time = f"{month_prefix}01 00:00:00"
    end_time = f"{month_prefix}{day_count:02d} 23:59:59"

    body = {
        "pageSize": "50",
        "planId": plan_id,
        "endTime": end_time,
        "startTime": start_time,
        "currPage": "1",
        "t": aes_encrypt(str(int(time.time() * 1000))),
    }
    try:
        res = requests.post(
            f"{BASE_URL}/attendence/clock/v1/list",
            json=body,
            headers=_headers(token, user_id, sign_info),
            verify=False,
            timeout=15,
        ).json()
    except Exception as e:
        print(f"[MAKEUP_LIST_WARN] 查询打卡记录异常: {e}", flush=True)
        return []

    if not isinstance(res, dict) or res.get("code") != 200:
        print(f"[MAKEUP_LIST_WARN] 查询打卡记录返回: {res}", flush=True)
        return []

    rows = res.get("data") or []
    if isinstance(rows, dict):
        rows = rows.get("list") or rows.get("records") or []
    marked_days = set()
    for item in rows if isinstance(rows, list) else []:
        ymd = str(item.get("dateYmd") or item.get("createTime") or "")
        m = re.search(r"\d{4}-\d{2}-(\d{2})", ymd)
        if m:
            marked_days.add(int(m.group(1)))

    today = now.day
    # 整月全量扫描（含周末和节假日），只排除今天（今天走正常打卡）；
    # 若配置了 MAKEUP_LOOKBACK_DAYS>0，则只回看最近 N 天
    earliest = max(1, today - MAKEUP_LOOKBACK_DAYS) if MAKEUP_LOOKBACK_DAYS > 0 else 1
    missing = []
    for d in range(earliest, today):
        if d in marked_days:
            continue
        missing.append(f"{month_prefix}{d:02d}")
    return missing


def submit_makeup_requests(user, token, user_id, plan_id, sign_info, sign_type="END"):
    """对未打卡日期提交补签申请（attendence/attendanceReplace/v2/save）。"""
    if os.getenv("AUTO_MAKEUP", "").strip().lower() not in ["1", "true", "yes"]:
        print("[MAKEUP] AUTO_MAKEUP 未开启，跳过补签申请", flush=True)
        return {"status": "skipped", "count": 0}

    missing = get_undated_days(token, user_id, plan_id, sign_info)
    if not missing:
        print("[MAKEUP] 本月无需补签的日期", flush=True)
        return {"status": "success", "count": 0}

    print(f"[MAKEUP] 检测到 {len(missing)} 个未打卡工作日，准备提交补签申请: {missing}", flush=True)
    address = user.get("address", "")
    province = user.get("province", "")
    city = user.get("city", "")
    lng = str(user.get("longitude", ""))
    lat = str(user.get("latitude", ""))

    # 补签签名格式借鉴自 gonxueyun 项目：Android + 类型 + 计划 + 用户 + 地址 + 密钥
    # 参考实现中补签固定按 START（上班卡）提交，接口会按此受理
    makeup_sign = create_sign("Android", "START", plan_id, str(user_id), address)
    # 关键：补签必须用老式请求头，否则会返回"请到应用商店下载最新版本APP"
    headers = _legacy_headers(token, user_id, makeup_sign)

    base_payload = {
        "device": "Android",
        "address": address,
        "description": "",
        "country": "中国",
        "longitude": lng,
        "city": city,
        "latitude": lat,
        "t": aes_encrypt(str(int(time.time() * 1000))),
        "planId": plan_id,
        "province": province,
        "type": "START",
    }

    ok, failed = 0, 0
    for day in missing:
        payload = dict(base_payload)
        # 补签时间参考 v2.0 实现：随机早上 08~09 点，接口按 START 类型受理
        import random
        hh = random.randint(8, 9)
        mm, ss = random.randint(10, 59), random.randint(10, 59)
        payload["createTime"] = f"{day} 0{hh}:{mm}:{ss}"
        payload["t"] = aes_encrypt(str(int(time.time() * 1000)))
        try:
            res = requests.post(
                f"{BASE_URL}/attendence/attendanceReplace/v2/save",
                data=json.dumps(payload),
                headers=headers,
                verify=False,
                timeout=15,
            ).json()
            code = res.get("code")
            msg = str(res.get("msg", ""))
            if code == 200:
                ok += 1
                print(f"[MAKEUP] {day} 补签申请提交成功（待管理员审批）: {msg}", flush=True)
            else:
                failed += 1
                print(f"[MAKEUP_WARN] {day} 补签申请失败: code={code}, msg={msg}", flush=True)
        except Exception as e:
            failed += 1
            print(f"[MAKEUP_WARN] {day} 补签申请异常: {e}", flush=True)
        time.sleep(2)  # 拟人化间隔，避免短时间高频请求

    result = {"status": "success", "submitted": ok, "failed": failed, "dates": missing}
    print(f"[MAKEUP] 补签申请完成: 成功提交 {ok} 条，失败 {failed} 条（均需管理员审批）", flush=True)
    return result
