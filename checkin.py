#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, sys, time, requests
from datetime import datetime

# 从环境变量获取 Token（优先）或账号密码
AUTH_TOKEN    = os.environ.get("AUTH_TOKEN") or ""
EMAIL         = os.environ.get("EMAIL") or ""
PASSWORD      = os.environ.get("PASSWORD") or ""
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN") or ""

BASE_URL       = "https://api.hcnsec.cn"
QUOTA_PER_UNIT = 500000  # 额度换算比例

# 确保 Authorization 格式正确
if AUTH_TOKEN and not AUTH_TOKEN.startswith("Bearer "):
    AUTH_TOKEN = f"Bearer {AUTH_TOKEN}"


def get_user_info(session: requests.Session):
    """获取用户信息"""
    url = f"{BASE_URL}/api/user/self"
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Authorization": AUTH_TOKEN,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": f"{BASE_URL}/profile",
    }
    resp = session.get(url, headers=headers, timeout=20)
    if resp.status_code == 200:
        data = resp.json()
        if data.get("success"):
            return data.get("data", {})
    return None


def checkin(session: requests.Session, user_id):
    """执行签到"""
    url = f"{BASE_URL}/api/user/checkin"
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Authorization": AUTH_TOKEN,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}/profile",
        "New-Api-User": str(user_id),
    }
    resp = session.post(url, headers=headers, json={}, timeout=20)
    try:
        return resp.json()
    except Exception:
        return {"success": False, "message": f"状态码: {resp.status_code}"}


def quota_to_dollar(quota):
    """额度换算"""
    return round(quota / QUOTA_PER_UNIT)


def send_notification(message):
    print("\n" + "=" * 25)
    print(message)
    print("=" * 25)

    if TG_BOT_TOKEN and TG_CHAT_ID:
        try:
            tg_url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
            resp = requests.post(
                tg_url,
                json={"chat_id": TG_CHAT_ID, "text": message},
                timeout=10,
            )
            if resp.status_code == 200:
                print("Telegram 通知发送成功")
            else:
                print(f"Telegram 通知发送失败: {resp.status_code} {resp.text}")
        except Exception as e:
            print("Telegram 通知发送失败:", e)


def main():
    if not AUTH_TOKEN:
        print("❌ 未配置 AUTH_TOKEN，请在 GitHub Secrets 中配置 AUTH_TOKEN")
        sys.exit(1)

    session = requests.Session()

    # 1. 验证 Token 并获取用户信息
    info_before = get_user_info(session)
    if not info_before:
        print("❌ Token 无效或已过期，请重新从浏览器获取 Authorization 填入 Secrets")
        sys.exit(1)

    user_id = info_before.get("id")
    username = info_before.get("username", str(user_id))
    balance_before = quota_to_dollar(info_before.get("quota", 0))
    print(f"✅ Token 验证成功 | 账户: {username} | ID: {user_id} | 当前余额: {balance_before}$")

    # 2. 执行签到
    checkin_data = checkin(session, user_id)

    # 3. 签到后刷新余额
    info_after = get_user_info(session)
    balance_after = quota_to_dollar(info_after.get("quota", 0)) if info_after else balance_before

    # 4. 判断结果
    local_time = time.gmtime(time.time() + 8 * 3600)
    now = time.strftime("%Y-%m-%d %H:%M:%S", local_time)
    success = checkin_data.get("success", False)
    msg = str(checkin_data.get("message", ""))

    if success:
        awarded_data = checkin_data.get("data", {})
        awarded_quota = awarded_data.get("quota_awarded", 0)
        awarded_dollar = quota_to_dollar(awarded_quota) if awarded_quota else (balance_after - balance_before)
        print(f"✅ 签到成功 | 获得: {awarded_dollar}$")

        message = (
            f"🎁 签到通知\n\n"
            f"✅ 签到成功, 本次签到获得: {awarded_dollar}$\n"
            f"👤 登录账户: {username}\n"
            f"💰 昨日余额: {balance_before}$\n"
            f"💰 当前余额: {balance_after}$\n"
            f"⏱️ 签到时间: {now}"
        )
    elif any(k in msg for k in ["已签到", "重复签到", "今天已签到"]):
        print(f"✅ 今日已签到 | 当前余额: {balance_after}$")
        message = (
            f"🎁 签到通知\n\n"
            f"✅ 今日你已经签到过了！\n"
            f"👤 登录账户: {username}\n"
            f"💰 当前余额: {balance_after}$\n"
            f"⏱️ 签到时间: {now}"
        )
    else:
        print(f"❌ 签到失败 | {msg}")
        message = (
            f"🎁 签到通知\n\n"
            f"❌ 签到失败: {msg}\n"
            f"👤 登录账户: {username}\n"
            f"💰 昨日余额: {balance_before}$\n"
            f"💰 当前余额: {balance_after}$\n"
            f"⏱️ 签到时间: {now}"
        )

    send_notification(message)


if __name__ == "__main__":
    main()
