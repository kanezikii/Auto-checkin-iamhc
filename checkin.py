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
    """先用 requests 获取用户信息，用于注入浏览器和对比余额"""
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

    # 1. 先验证 Token 并拿到用户数据
    user_info = get_user_info_api(raw_token)
    if not user_info:
        print("❌ Token 无效或已过期，请重新获取 Authorization")
        sys.exit(1)

    user_id = user_info.get("id")
    username = user_info.get("username", str(user_id))
    balance_before = quota_to_dollar(user_info.get("quota", 0))
    print(f"✅ Token 验证成功 | 账户: {username} | ID: {user_id} | 当前余额: {balance_before}$")

    checkin_response_data = {}

    # 2. 启动 Playwright 执行带有 Turnstile 的浏览器模拟
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
            viewport={"width": 1280, "height": 800},
            extra_http_headers={
                "Authorization": f"Bearer {raw_token}",
            },
        )

        # 注入会话标记 Cookie
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

        # 监听签到响应接口
        def handle_response(response):
            if "/api/user/checkin" in response.url and response.request.method == "POST":
                try:
                    res_json = response.json()
                    checkin_response_data.update(res_json)
                    print(f"📡 捕获到签到接口返回: {res_json}")
                except Exception:
                    pass

        page.on("response", handle_response)

        # 注入 LocalStorage（New-API 前端需要 user 对象）
        user_json_str = json.dumps(user_info).replace("'", "\\'")
        page.add_init_script(f"""
            localStorage.setItem('user', '{user_json_str}');
            localStorage.setItem('token', '{raw_token}');
        """)

        print("正在打开个人中心页面...")
        # 此处使用 domcontentloaded，避免因后台持续网络连接导致超时
        page.goto(f"{BASE_URL}/profile", wait_until="domcontentloaded", timeout=60000)

        # 等待页面组件及 Turnstile 渲染
        print("等待页面元素及人机验证加载...")
        time.sleep(4)

        # 寻找签到按钮
        btn = page.locator("button:has-text('立即签到'), button:has-text('签到')").first
        try:
            if btn.is_visible(timeout=5000):
                print("🎯 找到「立即签到」按钮，正在点击...")
                btn.click()
                # 点击后等待 6 秒供 Turnstile 计算并通过接口提交
                page.wait_for_timeout(6000)
            else:
                print("ℹ️ 未检测到「立即签到」按钮（可能今日已完成签到）")
        except Exception as e:
            print(f"查找或点击签到按钮时提示: {e}")

        browser.close()

    # 3. 重新获取一次余额计算增量
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
            f"ℹ️ 执行完毕（未截获到新签到响应，当前余额: {balance_after}$）\n"
            f"⏱️ 执行时间: {now}"
        )

    send_notification(message)


if __name__ == "__main__":
    main()
