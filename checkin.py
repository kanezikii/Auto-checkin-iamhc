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

    checkin_response_data = {}

    with sync_playwright() as p:
        # 启动 Chromium，加入常用反指纹参数
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
        )

        page = context.new_page()

        # 屏蔽 webdriver 特征
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)

        # 监听签到接口响应
        def handle_response(response):
            if "/api/user/checkin" in response.url and response.request.method == "POST":
                try:
                    res_json = response.json()
                    checkin_response_data.update(res_json)
                    print(f"捕获到签到接口返回: {res_json}")
                except Exception:
                    pass

        page.on("response", handle_response)

        print("正在打开网站并注入认证凭证...")
        page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")

        # 将 Token 注入到 LocalStorage
        page.evaluate(f"""() => {{
            localStorage.setItem('token', '{raw_token}');
        }}""")

        # 访问个人主页
        page.goto(f"{BASE_URL}/profile", wait_until="networkidle")
        time.sleep(3)

        # 检查是否成功加载个人中心
        if "profile" not in page.url and "login" in page.url:
            print("❌ 页面被重定向回登录页，Token 可能已失效")
            browser.close()
            sys.exit(1)

        print("✅ 成功进入个人中心，等待 Turnstile 人机验证就绪...")
        # 等待 Turnstile 自动渲染和静默校验
        time.sleep(5)

        # 寻找并点击【立即签到】按钮
        btn = page.locator("button:has-text('立即签到'), button:has-text('签到')").first
        if btn.is_visible():
            print("找到签到按钮，正在点击...")
            btn.click()
            # 点击后等待网络响应和 Turnstile 提交
            page.wait_for_timeout(6000)
        else:
            print("⚠️ 未找到签到按钮，可能今日已完成签到或按钮文案不匹配")

        browser.close()

    # 处理通知逻辑
    local_time = time.gmtime(time.time() + 8 * 3600)
    now = time.strftime("%Y-%m-%d %H:%M:%S", local_time)

    success = checkin_response_data.get("success", False)
    msg = str(checkin_response_data.get("message", ""))

    if success:
        awarded_data = checkin_response_data.get("data", {})
        awarded_quota = awarded_data.get("quota_awarded", 0)
        awarded_dollar = quota_to_dollar(awarded_quota)
        message = (
            f"🎁 签到通知\n\n"
            f"✅ 签到成功！本次获得: {awarded_dollar}$\n"
            f"⏱️ 签到时间: {now}"
        )
    elif any(k in msg for k in ["已签到", "重复签到", "今天已签到"]):
        message = (
            f"🎁 签到通知\n\n"
            f"✅ 今日你已经签到过了！\n"
            f"⏱️ 签到时间: {now}"
        )
    elif msg:
        message = (
            f"🎁 签到通知\n\n"
            f"❌ 签到失败: {msg}\n"
            f"⏱️ 签到时间: {now}"
        )
    else:
        message = (
            f"🎁 签到通知\n\n"
            f"ℹ️ 脚本执行完成（未截获到新签到响应，可能已完成签到）\n"
            f"⏱️ 执行时间: {now}"
        )

    send_notification(message)


if __name__ == "__main__":
    main()
