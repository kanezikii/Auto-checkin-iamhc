#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, sys, time, json, requests
from playwright.sync_api import sync_playwright

AUTH_TOKEN   = os.environ.get("AUTH_TOKEN") or ""
TG_CHAT_ID   = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN") or ""

BASE_URL       = "https://api.hcnsec.cn"
QUOTA_PER_UNIT = 500000  # 额度比例：500000 quota = 1$


def quota_to_dollar(quota):
    try:
        return round(float(quota) / QUOTA_PER_UNIT)
    except Exception:
        return 0


def get_user_info_api(token):
    """通过 API 获取用户信息"""
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    }
    try:
        resp = requests.get(f"{BASE_URL}/api/user/self", headers=headers, timeout=15)
        if resp.status_code == 200 and resp.json().get("success"):
            return resp.json().get("data", {})
    except Exception as e:
        print(f"获取用户信息异常: {e}")
    return None


def send_notification(message):
    print("\n" + "=" * 30)
    print(message)
    print("=" * 30)

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
        print("❌ 未配置 AUTH_TOKEN")
        sys.exit(1)

    raw_token = AUTH_TOKEN.replace("Bearer ", "").strip()

    # 1. 获取基本信息并补充 token 字段
    user_info = get_user_info_api(raw_token)
    if not user_info:
        print("❌ Token 无效或已过期，请重新获取 Authorization")
        sys.exit(1)

    # 关键修复：补全前端判断登录状态所需的 token 字段
    user_info["token"] = raw_token
    user_info["access_token"] = raw_token

    user_id = user_info.get("id")
    username = user_info.get("username", str(user_id))
    balance_before = quota_to_dollar(user_info.get("quota", 0))
    print(f"✅ Token 验证成功 | 账户: {username} | ID: {user_id} | 当前余额: {balance_before}$")

    checkin_response_data = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1440, "height": 900},
            extra_http_headers={
                "Authorization": f"Bearer {raw_token}",
            },
        )

        context.add_cookies([{
            "name": "new_api_has_session",
            "value": "1",
            "domain": "api.hcnsec.cn",
            "path": "/",
        }])

        page = context.new_page()

        # 屏蔽 Webdriver 指纹特征
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)

        # 监听签到响应
        def handle_response(response):
            if "/api/user/checkin" in response.url and response.request.method == "POST":
                try:
                    res_json = response.json()
                    checkin_response_data.update(res_json)
                    print(f"📡 捕获到签到接口返回: {res_json}")
                except Exception:
                    pass

        page.on("response", handle_response)

        # 注入 LocalStorage 与 SessionStorage
        user_json = json.dumps(user_info)
        page.add_init_script(f"""
            window.localStorage.setItem('user', JSON.stringify({user_json}));
            window.localStorage.setItem('token', '{raw_token}');
            window.sessionStorage.setItem('user', JSON.stringify({user_json}));
            window.sessionStorage.setItem('token', '{raw_token}');
        """)

        print("正在打开个人中心页面...")
        page.goto(f"{BASE_URL}/profile", wait_until="domcontentloaded", timeout=60000)

        # 等待页面加载并输出当前实际地址用于排查
        time.sleep(3)
        print(f"当前页面实际地址: {page.url}")

        # 使用文本穿透定位按钮，并设置显式等待
        btn = page.locator("text='立即签到'").first
        try:
            print("正在等待「立即签到」按钮渲染就绪...")
            btn.wait_for(state="visible", timeout=15000)
            print("🎯 定位成功，正在执行点击...")
            btn.click()
            print("已点击，等待 Turnstile 静默完成并提交...")
            page.wait_for_timeout(8000)
        except Exception as e:
            print(f"ℹ️ 点击未完成（可能已签到或渲染超时）: {e}")

        browser.close()

    # 3. 再次获取余额比对增量
    time.sleep(1)
    new_info = get_user_info_api(raw_token)
    balance_after = quota_to_dollar(new_info.get("quota", 0)) if new_info else balance_before

    # 4. 组装结果通知
    local_time = time.gmtime(time.time() + 8 * 3600)
    now = time.strftime("%Y-%m-%d %H:%M:%S", local_time)

    success = checkin_response_data.get("success", False)
    msg = str(checkin_response_data.get("message", ""))

    if success or (balance_after > balance_before):
        awarded_dollar = balance_after - balance_before
        message = (
            f"🎁 签到通知\n\n"
            f"✅ 签到成功！\n"
            f"👤 账户: {username}\n"
            f"💰 变动: +{awarded_dollar}$ (当前: {balance_after}$)\n"
            f"⏱️ 签到时间: {now}"
        )
    elif any(k in msg for k in ["已签到", "重复签到", "今天已签到"]):
        message = (
            f"🎁 签到通知\n\n"
            f"✅ 今日你已经签到过了！\n"
            f"👤 账户: {username}\n"
            f"💰 当前余额: {balance_after}$\n"
            f"⏱️ 签到时间: {now}"
        )
    elif msg:
        message = (
            f"🎁 签到通知\n\n"
            f"❌ 签到失败: {msg}\n"
            f"👤 账户: {username}\n"
            f"💰 当前余额: {balance_after}$\n"
            f"⏱️ 签到时间: {now}"
        )
    else:
        message = (
            f"🎁 签到通知\n\n"
            f"ℹ️ 执行完毕（当前余额: {balance_after}$）\n"
            f"⏱️ 执行时间: {now}"
        )

    send_notification(message)


if __name__ == "__main__":
    main()
