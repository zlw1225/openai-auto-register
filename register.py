# ==============================================================================
# 免责声明
# 本脚本仅供学习和技术研究使用，禁止用于任何商业用途或违反服务条款的行为。
# 使用本脚本所产生的一切后果由使用者自行承担，作者不承担任何法律责任。
# OpenAI 服务条款地址：https://openai.com/policies/terms-of-use
# ==============================================================================

import json
import os
import re
import sys
import time
import uuid
import math
import random
import string
import secrets
import hashlib
import base64
import threading
import subprocess
import argparse
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, parse_qs, urlencode, quote
from dataclasses import dataclass
from typing import Any, Dict, Optional
import urllib.parse
import urllib.request
import urllib.error

from curl_cffi import requests

# ==========================================
# Mail.tm 临时邮箱 API
# ==========================================

MAILTM_BASE = "https://api.mail.tm"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DEFAULT_PASSWORD = "66661adcchat"


def _mailtm_headers(*, token: str = "", use_json: bool = False) -> Dict[str, str]:
    headers = {"Accept": "application/json"}
    if use_json:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _mailtm_req(method: str, url: str, headers: dict, proxies: Any = None, timeout: int = 12, json_body=None) -> Any:
    """Use standard requests for all Mail.tm calls - reliable timeout support"""
    import requests as _std_req
    import warnings
    warnings.filterwarnings("ignore")

    class FakeResp:
        def __init__(self, body, status):
            self._body = body
            self.status_code = status
        def json(self):
            return __import__('json').loads(self._body)

    try:
        if method.upper() == "POST":
            r = _std_req.post(url, headers=headers, proxies=proxies, timeout=timeout, json=json_body)
        else:
            r = _std_req.get(url, headers=headers, proxies=proxies, timeout=timeout)
        return FakeResp(r.content, r.status_code)
    except Exception:
        return FakeResp(b'{}', 0)


def _mailtm_get(url: str, headers: dict, proxies: Any = None, timeout: int = 12) -> Any:
    return _mailtm_req("GET", url, headers, proxies, timeout)


def _mailtm_domains(proxies: Any = None) -> list[str]:
    resp = _mailtm_req("GET",
        f"{MAILTM_BASE}/domains",
        headers=_mailtm_headers(),
        proxies=proxies,
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"获取 Mail.tm 域名失败，状态码: {resp.status_code}")

    data = resp.json()
    domains = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("hydra:member") or data.get("items") or []
    else:
        items = []

    for item in items:
        if not isinstance(item, dict):
            continue
        domain = str(item.get("domain") or "").strip()
        is_active = item.get("isActive", True)
        is_private = item.get("isPrivate", False)
        if domain and is_active and not is_private:
            domains.append(domain)

    return domains


def get_email_and_token(proxies: Any = None) -> tuple[str, str]:
    """创建 Mail.tm 邮箱并获取 Bearer Token"""
    try:
        domains = _mailtm_domains(proxies)
        if not domains:
            print("[Error] Mail.tm 没有可用域名")
            return "", ""
        domain = random.choice(domains)

        for _ in range(5):
            local = f"oc{secrets.token_hex(5)}"
            email = f"{local}@{domain}"
            password = secrets.token_urlsafe(18)

            create_resp = _mailtm_req("POST",
                f"{MAILTM_BASE}/accounts",
                headers=_mailtm_headers(use_json=True),
                proxies=proxies,
                timeout=15,
                json_body={"address": email, "password": password},
            )

            if create_resp.status_code not in (200, 201):
                continue

            token_resp = _mailtm_req("POST",
                f"{MAILTM_BASE}/token",
                headers=_mailtm_headers(use_json=True),
                proxies=proxies,
                timeout=15,
                json_body={"address": email, "password": password},
            )

            if token_resp.status_code == 200:
                token = str(token_resp.json().get("token") or "").strip()
                if token:
                    return email, token

        print("[Error] Mail.tm 邮箱创建成功但获取 Token 失败")
        return "", ""
    except Exception as e:
        print(f"[Error] 请求 Mail.tm API 出错: {e}")
        return "", ""


def get_oai_code(token: str, email: str, proxies: Any = None) -> str:
    """使用 Mail.tm Token 轮询获取 OpenAI 验证码"""
    url_list = f"{MAILTM_BASE}/messages"
    regex = r"(?<!\d)(\d{6})(?!\d)"
    seen_ids: set[str] = set()

    print(f"[*] 正在等待邮箱 {email} 的验证码...", end="", flush=True)

    for _ in range(40):
        print(".", end="", flush=True)
        try:
            resp = _mailtm_get(
                url_list,
                headers=_mailtm_headers(token=token),
                proxies=proxies,
                timeout=12,
            )
            if resp.status_code != 200:
                time.sleep(3)
                continue

            data = resp.json()
            if isinstance(data, list):
                messages = data
            elif isinstance(data, dict):
                messages = data.get("hydra:member") or data.get("messages") or []
            else:
                messages = []

            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                msg_id = str(msg.get("id") or "").strip()
                if not msg_id or msg_id in seen_ids:
                    continue
                seen_ids.add(msg_id)

                read_resp = _mailtm_get(
                    f"{MAILTM_BASE}/messages/{msg_id}",
                    headers=_mailtm_headers(token=token),
                    proxies=proxies,
                    timeout=12,
                )
                if read_resp.status_code != 200:
                    continue

                mail_data = read_resp.json()
                sender = str(
                    ((mail_data.get("from") or {}).get("address") or "")
                ).lower()
                subject = str(mail_data.get("subject") or "")
                intro = str(mail_data.get("intro") or "")
                text = str(mail_data.get("text") or "")
                html = mail_data.get("html") or ""
                if isinstance(html, list):
                    html = "\n".join(str(x) for x in html)
                content = "\n".join([subject, intro, text, str(html)])

                if "openai" not in sender and "openai" not in content.lower():
                    continue

                m = re.search(regex, content)
                if m:
                    print(" 抓到啦! 验证码:", m.group(1))
                    return m.group(1)
        except Exception:
            pass

        time.sleep(3)

    print(" 超时，未收到验证码")
    return ""


# ==========================================
# OAuth 授权与辅助函数
# ==========================================

def _is_valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match((email or "").strip()))


def prompt_user_email(initial_email: str = "") -> str:
    email = (initial_email or "").strip()
    while True:
        if not email:
            email = input("[?] Enter registration email: ").strip()
        if _is_valid_email(email):
            return email
        print("[Error] Invalid email format. Try again.")
        email = ""


def _default_inbox_url(email: str) -> str:
    return f"https://mail.chatgpt.org.uk/{quote(email, safe='@')}"


def try_fetch_otp_via_playwright(inbox_email: str, timeout_sec: int = 90) -> str:
    inbox_url = _default_inbox_url(inbox_email)
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        print(f"[*] Playwright unavailable: {e}")
        return ""

    code_patterns = [
        re.compile(r"Your ChatGPT code is\s*(\d{6})", re.IGNORECASE),
        re.compile(r"ChatGPT code is\s*(\d{6})", re.IGNORECASE),
        re.compile(r"OpenAI code is\s*(\d{6})", re.IGNORECASE),
        re.compile(r"(?<!\d)(\d{6})(?!\d)"),
    ]

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(inbox_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2000)
            deadline = time.time() + max(timeout_sec, 5)
            while time.time() < deadline:
                text = page.locator("body").inner_text(timeout=10000)
                text = text.encode("ascii", "replace").decode("ascii")
                for pattern in code_patterns:
                    m = pattern.search(text)
                    if m:
                        browser.close()
                        code = m.group(1)
                        print(f"[*] Auto-fetched OTP via Playwright: {code}")
                        return code
                page.wait_for_timeout(5000)
                page.reload(wait_until="domcontentloaded", timeout=60000)
            browser.close()
    except Exception as e:
        print(f"[*] Playwright OTP fetch failed: {e}")
        return ""

    return ""


def prompt_user_otp(
    email: str,
    inbox_email: Optional[str] = None,
    inbox_url: Optional[str] = None,
    auto_fetch: bool = True,
    resend_otp=None,
) -> str:
    otp_inbox_email = (inbox_email or email or "").strip()
    if not _is_valid_email(otp_inbox_email):
        raise RuntimeError("OTP inbox email is invalid.")

    resolved_inbox_url = (inbox_url or "").strip()
    if not resolved_inbox_url:
        try:
            with open("config.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
            resolved_inbox_url = str(cfg.get("inbox_url") or "").strip()
        except Exception:
            resolved_inbox_url = ""
    if not resolved_inbox_url:
        resolved_inbox_url = _default_inbox_url(otp_inbox_email)

    resent_once = False

    def maybe_resend_otp(reason: str) -> None:
        nonlocal resent_once
        if resend_otp is None:
            return
        print(f"[*] Triggering OTP resend: {reason}")
        status, text = resend_otp()
        print(f"[*] Resend OTP status: {status}")
        if text:
            print(text)
        if status == 200:
            resent_once = True

    print(f"[*] Inbox URL: {resolved_inbox_url}")
    if auto_fetch:
        print("[*] Trying to fetch OTP from GPTMail via Playwright...")
        code = try_fetch_otp_via_playwright(otp_inbox_email)
        if code:
            return code
        if not resent_once:
            maybe_resend_otp("initial auto-fetch timeout")
            code = try_fetch_otp_via_playwright(otp_inbox_email, timeout_sec=60)
            if code:
                return code
    if not sys.stdin.isatty():
        raise RuntimeError("Auto OTP fetch failed and stdin is not interactive. Use a fresh email or rerun interactively.")
    while True:
        code = input(
            f"[?] Enter the 6-digit code sent to {email}. Press Enter to keep waiting, r to resend, or q to quit: "
        ).strip()
        if not code:
            if auto_fetch:
                code = try_fetch_otp_via_playwright(otp_inbox_email, timeout_sec=30)
                if code:
                    return code
            if not resent_once:
                maybe_resend_otp("manual wait timeout")
            print(f"[*] Still waiting. Open {resolved_inbox_url} and check the inbox/spam folder.")
            continue
        if code.lower() == "r":
            maybe_resend_otp("user requested resend")
            continue
        if code.lower() == "q":
            raise RuntimeError("User cancelled before receiving the verification code.")
        if re.fullmatch(r"\d{6}", code):
            return code
        print("[Error] Invalid code format. Enter exactly 6 digits.")


AUTH_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
EMAIL_OTP_SEND_URL = "https://auth.openai.com/api/accounts/email-otp/send"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

DEFAULT_REDIRECT_URI = "http://localhost:1455/auth/callback"
DEFAULT_SCOPE = "openid email profile offline_access"


def _b64url_no_pad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _sha256_b64url_no_pad(s: str) -> str:
    return _b64url_no_pad(hashlib.sha256(s.encode("ascii")).digest())


def _random_state(nbytes: int = 16) -> str:
    return secrets.token_urlsafe(nbytes)


def _pkce_verifier() -> str:
    return secrets.token_urlsafe(64)


def _parse_callback_url(callback_url: str) -> Dict[str, str]:
    candidate = callback_url.strip()
    if not candidate:
        return {"code": "", "state": "", "error": "", "error_description": ""}

    if "://" not in candidate:
        if candidate.startswith("?"):
            candidate = f"http://localhost{candidate}"
        elif any(ch in candidate for ch in "/?#") or ":" in candidate:
            candidate = f"http://{candidate}"
        elif "=" in candidate:
            candidate = f"http://localhost/?{candidate}"

    parsed = urllib.parse.urlparse(candidate)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    fragment = urllib.parse.parse_qs(parsed.fragment, keep_blank_values=True)

    for key, values in fragment.items():
        if key not in query or not query[key] or not (query[key][0] or "").strip():
            query[key] = values

    def get1(k: str) -> str:
        v = query.get(k, [""])
        return (v[0] or "").strip()

    code = get1("code")
    state = get1("state")
    error = get1("error")
    error_description = get1("error_description")

    if code and not state and "#" in code:
        code, state = code.split("#", 1)

    if not error and error_description:
        error, error_description = error_description, ""

    return {
        "code": code,
        "state": state,
        "error": error,
        "error_description": error_description,
    }


def _jwt_claims_no_verify(id_token: str) -> Dict[str, Any]:
    if not id_token or id_token.count(".") < 2:
        return {}
    payload_b64 = id_token.split(".")[1]
    pad = "=" * ((4 - (len(payload_b64) % 4)) % 4)
    try:
        payload = base64.urlsafe_b64decode((payload_b64 + pad).encode("ascii"))
        return json.loads(payload.decode("utf-8"))
    except Exception:
        return {}


def _decode_jwt_segment(seg: str) -> Dict[str, Any]:
    raw = (seg or "").strip()
    if not raw:
        return {}
    pad = "=" * ((4 - (len(raw) % 4)) % 4)
    try:
        decoded = base64.urlsafe_b64decode((raw + pad).encode("ascii"))
        return json.loads(decoded.decode("utf-8"))
    except Exception:
        return {}


def _to_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _post_form(url: str, data: Dict[str, str], timeout: int = 30) -> Dict[str, Any]:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.status != 200:
                raise RuntimeError(
                    f"token exchange failed: {resp.status}: {raw.decode('utf-8', 'replace')}"
                )
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        raise RuntimeError(
            f"token exchange failed: {exc.code}: {raw.decode('utf-8', 'replace')}"
        ) from exc


def _send_email_otp(session: requests.Session) -> tuple[int, str]:
    resp = session.get(
        EMAIL_OTP_SEND_URL,
        headers={
            "referer": "https://auth.openai.com/create-account/password",
            "accept": "application/json",
        },
        timeout=30,
    )
    return resp.status_code, resp.text


def _send_passwordless_otp(session: requests.Session) -> tuple[int, str]:
    resp = session.post(
        "https://auth.openai.com/api/accounts/passwordless/send-otp",
        headers={
            "referer": "https://auth.openai.com/create-account/password",
            "accept": "application/json",
            "content-type": "application/json",
        },
        timeout=30,
    )
    return resp.status_code, resp.text


@dataclass(frozen=True)
class OAuthStart:
    auth_url: str
    state: str
    code_verifier: str
    redirect_uri: str


def generate_oauth_url(
    *, redirect_uri: str = DEFAULT_REDIRECT_URI, scope: str = DEFAULT_SCOPE
) -> OAuthStart:
    state = _random_state()
    code_verifier = _pkce_verifier()
    code_challenge = _sha256_b64url_no_pad(code_verifier)

    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "login",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
    }
    auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
    return OAuthStart(
        auth_url=auth_url,
        state=state,
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
    )


def submit_callback_url(
    *,
    callback_url: str,
    expected_state: str,
    code_verifier: str,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
) -> str:
    cb = _parse_callback_url(callback_url)
    if cb["error"]:
        desc = cb["error_description"]
        raise RuntimeError(f"oauth error: {cb['error']}: {desc}".strip())

    if not cb["code"]:
        raise ValueError("callback url missing ?code=")
    if not cb["state"]:
        raise ValueError("callback url missing ?state=")
    if cb["state"] != expected_state:
        raise ValueError("state mismatch")

    token_resp = _post_form(
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": cb["code"],
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        },
    )

    access_token = (token_resp.get("access_token") or "").strip()
    refresh_token = (token_resp.get("refresh_token") or "").strip()
    id_token = (token_resp.get("id_token") or "").strip()
    expires_in = _to_int(token_resp.get("expires_in"))

    claims = _jwt_claims_no_verify(id_token)
    email = str(claims.get("email") or "").strip()
    auth_claims = claims.get("https://api.openai.com/auth") or {}
    account_id = str(auth_claims.get("chatgpt_account_id") or "").strip()

    now = int(time.time())
    expired_rfc3339 = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + max(expires_in, 0))
    )
    now_rfc3339 = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))

    config = {
        "id_token": id_token,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "account_id": account_id,
        "last_refresh": now_rfc3339,
        "email": email,
        "type": "codex",
        "expired": expired_rfc3339,
    }

    return json.dumps(config, ensure_ascii=False, separators=(",", ":"))


# ==========================================
# 核心注册逻辑
# ==========================================


def run(
    proxy: Optional[str],
    email: Optional[str] = None,
    password: str = DEFAULT_PASSWORD,
    auto_fetch_otp: bool = True,
    inbox_email: Optional[str] = None,
    inbox_url: Optional[str] = None,
) -> Optional[str]:
    proxies: Any = None
    if proxy:
        proxies = {"http": proxy, "https": proxy}

    s = requests.Session(proxies=proxies, impersonate="chrome")

    try:
        trace = s.get("https://cloudflare.com/cdn-cgi/trace", timeout=10)
        trace = trace.text
        loc_re = re.search(r"^loc=(.+)$", trace, re.MULTILINE)
        loc = loc_re.group(1) if loc_re else None
        print(f"[*] 当前 IP 所在地: {loc}")
        if loc == "CN" or loc == "HK":
            raise RuntimeError("检查代理哦 - 所在地不支持")
    except Exception as e:
        print(f"[Error] 网络连接检查失败: {e}")
        return None

    email = prompt_user_email(email)
    password = (password or "").strip() or DEFAULT_PASSWORD
    print(f"[*] 本次注册使用邮箱: {email}")
    print(f"[*] 本次注册使用密码: {password[:4]}{'*' * max(len(password) - 4, 0)}")

    oauth = generate_oauth_url()
    url = oauth.auth_url

    try:
        resp = s.get(url, timeout=15)
        did = s.cookies.get("oai-did")
        print(f"[*] Device ID: {did}")

        signup_body = json.dumps(
            {
                "username": {"value": email, "kind": "email"},
                "screen_hint": "signup",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        sen_req_body = f'{{"p":"","id":"{did}","flow":"authorize_continue"}}'

        sen_resp = requests.post(
            "https://sentinel.openai.com/backend-api/sentinel/req",
            headers={
                "origin": "https://sentinel.openai.com",
                "referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6",
                "content-type": "text/plain;charset=UTF-8",
            },
            data=sen_req_body,
            proxies=proxies,
            impersonate="chrome",
            timeout=15,
        )

        if sen_resp.status_code != 200:
            print(f"[Error] Sentinel 异常拦截，状态码: {sen_resp.status_code}")
            return None

        sen_token = sen_resp.json()["token"]
        sentinel = f'{{"p": "", "t": "", "c": "{sen_token}", "id": "{did}", "flow": "authorize_continue"}}'

        signup_resp = s.post(
            "https://auth.openai.com/api/accounts/authorize/continue",
            headers={
                "referer": "https://auth.openai.com/create-account",
                "accept": "application/json",
                "content-type": "application/json",
                "openai-sentinel-token": sentinel,
            },
            data=signup_body,
        )
        print(f"[*] Email submit status: {signup_resp.status_code}")
        if signup_resp.text:
            print(signup_resp.text)
        if signup_resp.status_code != 200:
            return None

        continue_url = str((signup_resp.json() or {}).get("continue_url") or "").strip()
        if continue_url:
            password_page_resp = s.get(continue_url, timeout=30)
            print(f"[*] Password page status: {password_page_resp.status_code}")
            if password_page_resp.status_code != 200:
                print(password_page_resp.text)
                return None

        register_resp = s.post(
            "https://auth.openai.com/api/accounts/user/register",
            headers={
                "referer": "https://auth.openai.com/create-account/password",
                "accept": "application/json",
                "content-type": "application/json",
            },
            data=json.dumps({"username": email, "password": password}, separators=(",", ":")),
            timeout=30,
        )
        print(f"[*] User register status: {register_resp.status_code}")
        if register_resp.text:
            print(register_resp.text)
        otp_send_status = 0
        otp_send_text = ""

        if register_resp.status_code == 200:
            otp_send_url = str((register_resp.json() or {}).get("continue_url") or "").strip()
            if otp_send_url:
                global EMAIL_OTP_SEND_URL
                EMAIL_OTP_SEND_URL = otp_send_url
            otp_send_status, otp_send_text = _send_email_otp(s)
            print(f"[*] OTP send status: {otp_send_status}")
            if otp_send_text:
                print(otp_send_text)
        else:
            print("[*] user/register failed, fallback to legacy passwordless/send-otp flow.")
            otp_send_status, otp_send_text = _send_passwordless_otp(s)
            print(f"[*] Legacy OTP send status: {otp_send_status}")
            if otp_send_text:
                print(otp_send_text)

        if otp_send_status != 200:
            print("[Error] OTP send failed in both current and legacy flows.")
            return None

        otp_inbox_email = (inbox_email or email or "").strip()
        print(f"[*] OTP inbox email: {otp_inbox_email}")
        print("[*] Check the mailbox via the direct inbox URL, then enter the verification code.")
        resend_otp = _send_email_otp if register_resp.status_code == 200 else _send_passwordless_otp
        code = prompt_user_otp(
            email,
            inbox_email=otp_inbox_email,
            inbox_url=inbox_url,
            auto_fetch=auto_fetch_otp,
            resend_otp=lambda: resend_otp(s),
        )

        code_body = f'{{"code":"{code}"}}'
        code_resp = s.post(
            "https://auth.openai.com/api/accounts/email-otp/validate",
            headers={
                "referer": "https://auth.openai.com/email-verification",
                "accept": "application/json",
                "content-type": "application/json",
            },
            data=code_body,
        )
        print(f"[*] 验证码校验状态: {code_resp.status_code}")

        create_account_body = json.dumps(
            {
                "name": "Neo",
                "birthdate": "2000-02-20",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        create_account_resp = s.post(
            "https://auth.openai.com/api/accounts/create_account",
            headers={
                "referer": "https://auth.openai.com/about-you",
                "accept": "application/json",
                "content-type": "application/json",
            },
            data=create_account_body,
        )
        create_account_status = create_account_resp.status_code
        print(f"[*] 账户创建状态: {create_account_status}")

        if create_account_status != 200:
            print(create_account_resp.text)
            return None

        auth_cookie = s.cookies.get("oai-client-auth-session")
        if not auth_cookie:
            print("[Error] 未能获取到授权 Cookie")
            return None

        auth_json = _decode_jwt_segment(auth_cookie.split(".")[0])
        workspaces = auth_json.get("workspaces") or []
        if not workspaces:
            print("[Error] 授权 Cookie 里没有 workspace 信息")
            return None
        workspace_id = str((workspaces[0] or {}).get("id") or "").strip()
        if not workspace_id:
            print("[Error] 无法解析 workspace_id")
            return None

        select_body = f'{{"workspace_id":"{workspace_id}"}}'
        select_resp = s.post(
            "https://auth.openai.com/api/accounts/workspace/select",
            headers={
                "referer": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                "content-type": "application/json",
            },
            data=select_body,
            timeout=45,
        )

        if select_resp.status_code != 200:
            print(f"[Error] 选择 workspace 失败，状态码: {select_resp.status_code}")
            print(select_resp.text)
            return None

        continue_url = str((select_resp.json() or {}).get("continue_url") or "").strip()
        if not continue_url:
            print("[Error] workspace/select 响应里缺少 continue_url")
            return None

        current_url = continue_url
        for _ in range(6):
            final_resp = s.get(current_url, allow_redirects=False, timeout=45)
            location = final_resp.headers.get("Location") or ""

            if final_resp.status_code not in [301, 302, 303, 307, 308]:
                break
            if not location:
                break

            next_url = urllib.parse.urljoin(current_url, location)
            if "code=" in next_url and "state=" in next_url:
                return submit_callback_url(
                    callback_url=next_url,
                    code_verifier=oauth.code_verifier,
                    redirect_uri=oauth.redirect_uri,
                    expected_state=oauth.state,
                )
            current_url = next_url

        print("[Error] 未能在重定向链中捕获到最终 Callback URL")
        return None

    except Exception as e:
        print(f"[Error] 运行时发生错误: {e}")
        return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OpenAI Auto-Register Script",
        epilog=(
            "Examples:\n"
            "  python register.py --email user@example.com\n"
            "  python register.py --email user@example.com --manual-otp\n"
            "  python register.py --proxy http://127.0.0.1:7890 --email user@example.com\n"
            "  python register.py --email user@example.com --inbox-email inbox@example.com"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--proxy", default=None, help="Proxy address, e.g. http://127.0.0.1:7890"
    )
    parser.add_argument("--once", action="store_true", help="Run once")
    parser.add_argument("--sleep-min", type=int, default=5, help="Minimum wait seconds in loop mode")
    parser.add_argument(
        "--sleep-max", type=int, default=30, help="Maximum wait seconds in loop mode"
    )
    parser.add_argument("--email", default=None, help="Specify registration email")
    parser.add_argument("--inbox-email", default=None, help="Specify the mailbox email used to receive OTP")
    parser.add_argument("--inbox-url", default=None, help="Specify the direct inbox URL used to open the OTP mailbox")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="Registration password")
    parser.add_argument("--manual-otp", action="store_true", help="Disable Playwright auto OTP fetch")
    args = parser.parse_args()

    sleep_min = max(1, args.sleep_min)
    sleep_max = max(sleep_min, args.sleep_max)

    count = 0
    print("[Info] OpenAI Auto-Register Started")

    while True:
        count += 1
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] >>> Start signup attempt {count} <<<")

        try:
            token_json = run(
                args.proxy,
                args.email,
                args.password,
                not args.manual_otp,
                args.inbox_email,
                args.inbox_url,
            )

            if token_json:
                try:
                    t_data = json.loads(token_json)
                    fname_email = t_data.get("email", "unknown")
                except Exception:
                    fname_email = "unknown"

                cpa_dir = os.path.expanduser("~/.cli-proxy-api")
                os.makedirs(cpa_dir, exist_ok=True)
                file_name = os.path.join(cpa_dir, f"codex-{fname_email}.json")

                with open(file_name, "w", encoding="utf-8") as f:
                    f.write(token_json)

                print(f"[*] Success! Token saved to: {file_name}")
            else:
                print("[-] Signup failed.")

        except Exception as e:
            print(f"[Error] Unhandled exception: {e}")

        if args.once:
            break

        wait_time = random.randint(sleep_min, sleep_max)
        print(f"[*] Sleep {wait_time}s...")
        time.sleep(wait_time)

if __name__ == "__main__":
    main()
