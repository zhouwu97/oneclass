from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
import argparse
import base64
import hashlib
import json
import os
import sys
import webbrowser
from urllib.parse import quote, urlencode

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
import requests

from .config import ROOT, load_config, save_config


CLIENT_VERSION = "1.0.0"
CLIENT_RELEASED_AT = "2026-06-15T00:00:00+08:00"
ONECLASS_LICENSE_PUBLIC_KEY = "Fx4uhd5n7yeTzX-NE8XYZSV2PCnnAzJ3hvrBqjw_ZS4"

TIER_LABELS = {
    "one_time": "一次性购买",
    "lifetime_updates": "长期更新",
    "upgrade_updates": "补差升级长期更新",
}


def license_is_active(cfg: dict[str, Any]) -> bool:
    return get_license_status(cfg)["active"]


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def verify_license_token(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("授权 token 格式无效")
    signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
    signature = _b64url_decode(parts[2])
    public_key = Ed25519PublicKey.from_public_bytes(_b64url_decode(ONECLASS_LICENSE_PUBLIC_KEY))
    try:
        public_key.verify(signature, signing_input)
    except InvalidSignature as exc:
        raise ValueError("授权 token 签名无效") from exc
    header = json.loads(_b64url_decode(parts[0]).decode("utf-8"))
    payload = json.loads(_b64url_decode(parts[1]).decode("utf-8"))
    if header.get("alg") != "EdDSA" or payload.get("typ") != "oneclass_license":
        raise ValueError("授权 token 类型无效")
    if payload.get("machine_id") != get_machine_id():
        raise ValueError("授权 token 绑定的机器标识不是当前电脑")
    if payload.get("tier") not in TIER_LABELS:
        raise ValueError("授权 token 套餐无效")
    updates_until = _parse_time(str(payload.get("updates_until") or ""))
    released_at = _parse_time(CLIENT_RELEASED_AT)
    if updates_until and released_at and released_at > updates_until:
        raise ValueError("当前客户端发布日期晚于授权更新截止日期")
    return payload


def get_license_status(cfg: dict[str, Any]) -> dict[str, Any]:
    token = str((cfg.get("license") or {}).get("token") or "").strip()
    result: dict[str, Any] = {
        "active": False,
        "machine_id": get_machine_id(),
        "client_version": CLIENT_VERSION,
        "client_released_at": CLIENT_RELEASED_AT,
        "reason": "未激活",
    }
    if not token:
        return result
    try:
        payload = verify_license_token(token)
    except Exception as exc:
        result["reason"] = str(exc)
        return result
    result.update(
        {
            "active": True,
            "reason": "",
            "tier": payload.get("tier", ""),
            "order_no": payload.get("order_no", ""),
            "paid_at": payload.get("paid_at", ""),
            "updates_until": payload.get("updates_until"),
            "license_issued": payload.get("license_issued", ""),
        }
    )
    return result


def current_license_token(cfg: dict[str, Any]) -> str:
    return str((cfg.get("license") or {}).get("token") or "").strip()


def format_price(cents: int) -> str:
    return f"{cents / 100:.2f}元"


def tier_summary(cfg: dict[str, Any], tier: str) -> str:
    purchase_cfg = cfg.get("purchase") or {}
    tiers = purchase_cfg.get("tiers") or {}
    tier_cfg = tiers.get(tier) or {}
    price = int(tier_cfg.get("price_cents") or 0)
    title = tier_cfg.get("title") or TIER_LABELS.get(tier, tier)
    desc = tier_cfg.get("description") or ""
    return f"{title} {format_price(price)} - {desc}".strip()


def configured_purchase_tiers(cfg: dict[str, Any]) -> list[str]:
    tiers = (cfg.get("purchase") or {}).get("tiers") or {}
    order = ["one_time", "lifetime_updates", "upgrade_updates"]
    result = [tier for tier in order if tier in tiers]
    for tier in tiers:
        if tier not in result:
            result.append(tier)
    return result


def _join_url(base_url: str, path: str) -> str:
    if not base_url:
        return path
    if not path:
        return base_url
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def get_machine_id() -> str:
    machine_guid = ""
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
            ) as key:
                machine_guid = str(winreg.QueryValueEx(key, "MachineGuid")[0] or "")
        except Exception:
            machine_guid = ""
    raw = machine_guid or "|".join(
        [
            os.environ.get("COMPUTERNAME", ""),
            os.environ.get("USERNAME", ""),
        ]
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"oc-{digest}"


def _append_machine_id(url: str) -> str:
    if not url:
        return url
    separator = "&" if "?" in url else "?"
    if "machine_id=" in url:
        return url
    return f"{url}{separator}{urlencode({'machine_id': get_machine_id()})}"


def _xiaoxin_base_url(cfg: dict[str, Any]) -> str:
    purchase_cfg = cfg.get("purchase") or {}
    if str(purchase_cfg.get("provider") or "local").strip().lower() != "xiaoxin":
        return ""
    xiaoxin_cfg = purchase_cfg.get("xiaoxin") or {}
    return str(xiaoxin_cfg.get("base_url") or "").strip().rstrip("/")


def _read_json(
    url: str,
    payload: dict[str, Any] | None = None,
    jwt_token: str = "",
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    data = None
    headers = {
        "Accept": "application/json",
        "User-Agent": f"OneClass/{get_machine_id()}",
        "X-OneClass-Client": "desktop",
    }
    method = "GET"
    if payload is not None:
        data = payload
        method = "POST"
    if jwt_token:
        headers["Authorization"] = f"Bearer {jwt_token}"
    if extra_headers:
        headers.update({k: v for k, v in extra_headers.items() if v})
    try:
        response = requests.request(method, url, json=data, headers=headers, timeout=12)
        raw = response.text
    except requests.RequestException as exc:
        raise RuntimeError(f"网络请求失败：{exc}") from exc
    if response.status_code >= 400:
        raw_error = raw
        message = raw_error
        try:
            parsed_error = json.loads(raw_error or "{}")
            if isinstance(parsed_error, dict):
                message = str(parsed_error.get("error") or raw_error)
        except Exception:
            pass
        raise RuntimeError(f"HTTP {response.status_code}: {message}")
    parsed = json.loads(raw or "{}")
    if not isinstance(parsed, dict):
        raise ValueError("服务端返回格式不是 JSON 对象")
    return parsed


def create_remote_order(
    cfg: dict[str, Any],
    tier: str,
    contact: str = "",
    jwt_token: str = "",
) -> dict[str, Any]:
    base_url = _xiaoxin_base_url(cfg)
    if not base_url:
        raise RuntimeError("未配置 xiaoxin 支付后端")
    jwt_token = jwt_token or os.environ.get("ONECLASS_LOGIN_JWT", "")
    if not jwt_token:
        raise RuntimeError(
            "请先打开融智云考（E:\\AI\\yunkao\\main.py）登录，进入“学习工具中心”后点击 OneClass 的“购买/同步授权”。"
        )
    payload = {
        "tier": tier,
        "machine_id": get_machine_id(),
        "contact": contact.strip(),
    }
    return _read_json(_join_url(base_url, "/api/oneclass/pay/create"), payload, jwt_token=jwt_token)


def query_remote_order(cfg: dict[str, Any], order_no: str) -> dict[str, Any]:
    base_url = _xiaoxin_base_url(cfg)
    if not base_url:
        raise RuntimeError("未配置 xiaoxin 支付后端")
    query = urlencode({"order_no": order_no})
    return _read_json(_join_url(base_url, "/api/oneclass/pay/status") + "?" + query)


def query_client_version(cfg: dict[str, Any]) -> dict[str, Any]:
    base_url = _xiaoxin_base_url(cfg)
    if not base_url:
        return {}
    token = current_license_token(cfg)
    return _read_json(
        _join_url(base_url, "/api/oneclass/client/version"),
        extra_headers={
            "X-OneClass-License": token,
            "X-OneClass-Machine-ID": get_machine_id(),
        },
    )


def _save_seen_notice(cfg: dict[str, Any], notice_id: str) -> None:
    cfg.setdefault("license", {})
    cfg["license"]["last_notice_id"] = notice_id
    save_config(cfg)


def _show_update_notice(title: str, lines: list[str]) -> None:
    text = "\n".join(line for line in lines if line)
    print(f"[OneClass 更新通知] {title}")
    if text:
        print(text)
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(title or "OneClass 更新通知", text or title)
        root.destroy()
    except Exception:
        pass


def maybe_handle_update_notice(cfg: dict[str, Any], data: dict[str, Any], verbose: bool = True) -> None:
    notice = data.get("update_notice")
    if not isinstance(notice, dict):
        return
    notice_id = str(notice.get("id") or "").strip()
    if not notice_id:
        return
    seen_id = str((cfg.get("license") or {}).get("last_notice_id") or "").strip()
    if seen_id == notice_id:
        return
    title = str(notice.get("title") or "OneClass 更新通知").strip()
    lines = []
    if notice.get("version"):
        lines.append(f"版本：{notice['version']}")
    if notice.get("content"):
        lines.append(str(notice["content"]))
    if notice.get("download_url"):
        lines.append(f"下载地址：{notice['download_url']}")
    _save_seen_notice(cfg, notice_id)
    if verbose:
        _show_update_notice(title, lines)


def remote_version_allows_run(cfg: dict[str, Any], verbose: bool = True) -> bool:
    try:
        data = query_client_version(cfg)
    except Exception as exc:
        if verbose:
            print(f"版本检查失败，暂时允许离线启动：{exc}")
        return True
    if not data:
        return True
    maybe_handle_update_notice(cfg, data, verbose=verbose)
    if bool(data.get("force_update")):
        if verbose:
            print("当前 OneClass 客户端需要强制升级。")
            if data.get("message"):
                print(str(data["message"]))
            if data.get("download_url"):
                print(f"下载地址: {data['download_url']}")
        return False
    return True


def _order_is_paid(data: dict[str, Any]) -> bool:
    status = str(data.get("status") or "").strip().lower()
    return status in {"paid", "success", "completed", "complete", "active"}


def _order_tier(data: dict[str, Any], fallback: str) -> str:
    tier = str(data.get("tier") or fallback).strip()
    return tier if tier in TIER_LABELS else fallback


def resolve_purchase_url(cfg: dict[str, Any], tier: str) -> str:
    purchase_cfg = cfg.get("purchase") or {}
    tiers = purchase_cfg.get("tiers") or {}
    tier_cfg = tiers.get(tier) or {}

    checkout_url = str(tier_cfg.get("checkout_url") or "").strip()
    if checkout_url:
        return checkout_url

    provider = str(purchase_cfg.get("provider") or "local").strip().lower()
    if provider == "xiaoxin":
        xiaoxin_cfg = purchase_cfg.get("xiaoxin") or {}
        base_url = str(xiaoxin_cfg.get("base_url") or "").strip()
        product_path = str(tier_cfg.get("product_path") or "").strip()
        checkout_path = str(tier_cfg.get("checkout_path") or "").strip()

        if product_path:
            return _append_machine_id(_join_url(base_url, product_path))
        if checkout_path:
            return _append_machine_id(_join_url(base_url, checkout_path))

        template = str(xiaoxin_cfg.get("checkout_url_template") or "").strip()
        if template:
            title = str(tier_cfg.get("title") or TIER_LABELS.get(tier, tier))
            return template.format(
                base_url=base_url.rstrip("/"),
                tier=tier,
                title=quote(title),
                price_cents=int(tier_cfg.get("price_cents") or 0),
                price_yuan=f"{int(tier_cfg.get('price_cents') or 0) / 100:.2f}",
            )

    fallback = str(tier_cfg.get("payment_url") or "").strip()
    if fallback:
        return _append_machine_id(fallback)

    if provider == "xiaoxin":
        xiaoxin_cfg = purchase_cfg.get("xiaoxin") or {}
        base_url = str(xiaoxin_cfg.get("base_url") or "").strip()
        if base_url:
            query = urlencode({"tier": tier, "machine_id": get_machine_id()})
            return _join_url(base_url, "/api/oneclass/pay/buy") + "?" + query
    return ""


def open_purchase_target(cfg: dict[str, Any], tier: str) -> bool:
    purchase_cfg = cfg.get("purchase") or {}
    tiers = purchase_cfg.get("tiers") or {}
    tier_cfg = tiers.get(tier) or {}
    url = resolve_purchase_url(cfg, tier)
    image_value = str(tier_cfg.get("qr_image") or "").strip()

    if url:
        webbrowser.open(url)
        return True

    if image_value:
        image_path = Path(image_value)
        if not image_path.is_absolute():
            image_path = ROOT / image_path
        if image_path.exists():
            webbrowser.open(image_path.resolve().as_uri())
            return True
    return False


def begin_remote_purchase(
    cfg: dict[str, Any],
    tier: str,
    contact: str = "",
    jwt_token: str = "",
) -> dict[str, Any]:
    data = create_remote_order(cfg, tier, contact, jwt_token)
    order = data.get("order") if isinstance(data.get("order"), dict) else {}
    order_no = str(data.get("order_no") or order.get("order_no") or "").strip()
    checkout_url = str(
        data.get("checkout_url") or data.get("pay_url") or order.get("checkout_url") or ""
    ).strip()
    if not order_no or not checkout_url:
        raise RuntimeError("服务端没有返回订单号或支付链接")
    webbrowser.open(checkout_url)
    return {"order_no": order_no, "checkout_url": checkout_url, "tier": tier}


def remember_pending_order(tier: str, order_no: str, checkout_url: str = "") -> None:
    cfg = load_config()
    cfg.setdefault("purchase", {})
    cfg["purchase"]["pending_order"] = {
        "tier": tier,
        "order_no": order_no,
        "checkout_url": checkout_url,
        "machine_id": get_machine_id(),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    save_config(cfg)


def clear_pending_order(cfg: dict[str, Any]) -> None:
    purchase_cfg = cfg.get("purchase")
    if isinstance(purchase_cfg, dict):
        purchase_cfg.pop("pending_order", None)


def check_pending_order(verbose: bool = False) -> bool:
    cfg = load_config()
    if license_is_active(cfg):
        return True
    pending = (cfg.get("purchase") or {}).get("pending_order") or {}
    if not isinstance(pending, dict):
        return False
    order_no = str(pending.get("order_no") or "").strip()
    tier = str(pending.get("tier") or "").strip()
    machine_id = str(pending.get("machine_id") or "").strip()
    if not order_no or tier not in TIER_LABELS:
        return False
    if machine_id and machine_id != get_machine_id():
        if verbose:
            print("待支付订单绑定的机器标识不是当前电脑，已忽略。")
        return False

    try:
        data = query_remote_order(cfg, order_no)
    except (requests.RequestException, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        if verbose:
            print(f"检查待支付订单失败：{exc}")
        return False

    if not _order_is_paid(data):
        if verbose:
            print(f"待支付订单 {order_no} 当前状态：{data.get('status', 'unknown')}")
        return False

    try:
        activate_from_paid_order(data, tier, order_no)
    except Exception as exc:
        if verbose:
            print(f"付款订单授权写入失败：{exc}")
        return False
    if verbose:
        print("付款已确认，授权已自动写入 config.yaml。")
    return True


def print_purchase_help(cfg: dict[str, Any]) -> None:
    purchase_cfg = cfg.get("purchase") or {}
    contact = str(purchase_cfg.get("contact") or "").strip()
    note = str(purchase_cfg.get("post_payment_note") or "").strip()
    provider = str(purchase_cfg.get("provider") or "local").strip().lower()

    print("未授权，当前版本需要先购买后使用。")
    for index, tier in enumerate(configured_purchase_tiers(cfg), start=1):
        print(f"{index}. {tier_summary(cfg, tier)}")
    if provider == "xiaoxin":
        xiaoxin_cfg = purchase_cfg.get("xiaoxin") or {}
        base_url = str(xiaoxin_cfg.get("base_url") or "").strip()
        if base_url:
            print(f"支付后端: xiaoxin 易支付 ({base_url})")
            print(f"机器标识: {get_machine_id()}")
    if contact:
        print(f"联系方式: {contact}")
    if note:
        print(note)
    print("可运行: python main.py purchase")
    print("请先打开 E:\\AI\\yunkao\\main.py 登录，再在“学习工具中心”里点击 OneClass 的“购买/同步授权”。")


def ensure_license_or_exit(cfg: dict[str, Any]) -> bool:
    purchase_cfg = cfg.get("purchase") or {}
    if not purchase_cfg.get("enabled", True):
        return True
    if not purchase_cfg.get("require_license", True):
        return True
    if license_is_active(cfg):
        return remote_version_allows_run(cfg)
    if check_pending_order() and remote_version_allows_run(load_config()):
        return True

    print_purchase_help(cfg)
    if not show_purchase_window(cfg):
        print("未能打开购买窗口，请检查 config.yaml 中 purchase.tiers.* 的 payment_url 或 qr_image 配置。")
    cfg = load_config()
    if license_is_active(cfg):
        return True
    return False


def activate_license(
    tier: str,
    owner: str,
    note: str = "",
    machine_id: str | None = None,
    order_no: str = "",
    paid_at: str = "",
) -> None:
    cfg = load_config()
    cfg.setdefault("license", {})
    cfg["license"].update(
        {
            "active": True,
            "tier": tier,
            "owner": owner,
            "note": note,
            "machine_id": machine_id or get_machine_id(),
            "order_no": order_no,
            "paid_at": paid_at,
            "activated_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    clear_pending_order(cfg)
    save_config(cfg)


def activate_from_paid_order(data: dict[str, Any], fallback_tier: str, order_no: str) -> None:
    token = str(data.get("license_token") or "").strip()
    if not token:
        raise RuntimeError("服务端还没有返回授权 token，请稍后再试")
    payload = verify_license_token(token)
    tier = _order_tier(data, fallback_tier)
    paid_at = str(data.get("paid_at") or "").strip()
    cfg = load_config()
    cfg.setdefault("license", {})
    cfg["license"].update(
        {
            "token": token,
            "tier": payload.get("tier") or tier,
            "machine_id": payload.get("machine_id") or get_machine_id(),
            "order_no": payload.get("order_no") or order_no,
            "paid_at": payload.get("paid_at") or paid_at,
            "updates_until": payload.get("updates_until"),
            "license_issued": payload.get("license_issued", ""),
            "activated_at": datetime.now().isoformat(timespec="seconds"),
            "last_notice_id": "",
            "note": "支付成功后自动激活",
        }
    )
    clear_pending_order(cfg)
    save_config(cfg)


def show_status(cfg: dict[str, Any]) -> None:
    status = get_license_status(cfg)
    license_cfg = cfg.get("license") or {}
    if not status["active"]:
        print("授权状态: 未激活")
        if status.get("reason"):
            print(f"原因: {status['reason']}")
        print_purchase_help(cfg)
        return
    tier = status.get("tier", "")
    owner = license_cfg.get("owner", "")
    activated_at = license_cfg.get("activated_at", "")
    print(f"授权状态: 已激活 ({TIER_LABELS.get(tier, tier)})")
    if owner:
        print(f"授权对象: {owner}")
    machine_id = status.get("machine_id", "")
    if machine_id:
        print(f"机器标识: {machine_id}")
    order_no = status.get("order_no", "")
    if order_no:
        print(f"付款订单: {order_no}")
    if activated_at:
        print(f"激活时间: {activated_at}")


def show_status_json(cfg: dict[str, Any]) -> None:
    print(json.dumps(get_license_status(cfg), ensure_ascii=False))


def show_purchase_window(cfg: dict[str, Any], preferred_tier: str | None = None) -> bool:
    try:
        import tkinter as tk
        from tkinter import messagebox
    except Exception:
        if preferred_tier:
            try:
                begin_remote_purchase(
                    cfg,
                    preferred_tier,
                    jwt_token=os.environ.get("ONECLASS_LOGIN_JWT", ""),
                )
                return True
            except Exception as exc:
                print(f"无法创建购买订单：{exc}")
        return False

    purchase_cfg = cfg.get("purchase") or {}
    contact = str(purchase_cfg.get("contact") or "").strip()
    note = str(purchase_cfg.get("post_payment_note") or "").strip()

    root = tk.Tk()
    root.title("OneClass 购买")
    root.geometry("460x390")
    root.resizable(False, False)

    container = tk.Frame(root, padx=20, pady=18)
    container.pack(fill="both", expand=True)

    status_var = tk.StringVar(value="")
    contact_var = tk.StringVar(value="")
    active_order: dict[str, str] = {}

    tk.Label(
        container,
        text="请选择购买方案",
        font=("Microsoft YaHei UI", 15, "bold"),
    ).pack(anchor="w")
    tk.Label(
        container,
        text="支付完成后本窗口会自动检查订单，并把 OneClass 授权绑定到当前机器标识。OneClass 授权与云考余额互不通用。",
        justify="left",
        wraplength=360,
    ).pack(anchor="w", pady=(8, 12))

    form = tk.Frame(container)
    form.pack(fill="x", pady=(0, 8))
    tk.Label(form, text="联系方式（可选）").grid(row=0, column=0, sticky="w")
    tk.Entry(form, textvariable=contact_var).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 8))
    tk.Label(form, text="支付方式：支付宝").grid(row=2, column=0, sticky="w")
    form.columnconfigure(0, weight=1)
    form.columnconfigure(1, weight=1)

    def start_purchase(selected_tier: str) -> None:
        try:
            order = begin_remote_purchase(
                cfg,
                selected_tier,
                contact_var.get(),
                os.environ.get("ONECLASS_LOGIN_JWT", ""),
            )
            active_order.clear()
            active_order.update(
                {
                    "order_no": str(order["order_no"]),
                    "tier": selected_tier,
                }
            )
            remember_pending_order(
                selected_tier,
                str(order["order_no"]),
                str(order.get("checkout_url") or ""),
            )
            status_var.set(f"已创建订单 {order['order_no']}，请在打开的页面完成付款。")
            root.after(2500, poll_order_once)
        except Exception as exc:
            if "统一登录" in str(exc) or "未登录" in str(exc) or "401" in str(exc):
                messagebox.showwarning("需要登录", str(exc))
                status_var.set(str(exc))
                return
            provider = str((cfg.get("purchase") or {}).get("provider") or "local").strip().lower()
            if provider != "xiaoxin" and open_purchase_target(cfg, selected_tier):
                status_var.set(f"自动创建订单失败，已打开备用购买页：{exc}")
                return
            messagebox.showwarning(
                "创建订单失败",
                f"请检查网络、登录状态或支付后端配置。\n\n{exc}",
            )

    def poll_order_once(show_pending: bool = False) -> None:
        order_no = active_order.get("order_no", "")
        tier = active_order.get("tier", "")
        if not order_no or not tier:
            status_var.set("请先点击一个购买方案。")
            return
        try:
            data = query_remote_order(cfg, order_no)
            if _order_is_paid(data):
                activate_from_paid_order(data, tier, order_no)
                status_var.set("付款已确认，授权已自动写入。现在可以使用了。")
                messagebox.showinfo("授权成功", "付款已确认，授权已绑定到当前机器标识。")
                root.after(300, root.destroy)
                return
            if show_pending:
                status_var.set(f"订单 {order_no} 仍未支付，付款后会自动激活。")
        except (requests.RequestException, TimeoutError, OSError, ValueError, RuntimeError) as exc:
            if show_pending:
                status_var.set(f"检查订单失败：{exc}")
        root.after(3000, poll_order_once)

    for tier in configured_purchase_tiers(cfg):
        summary = tier_summary(cfg, tier)

        def handler(selected_tier: str = tier) -> None:
            start_purchase(selected_tier)

        tk.Button(
            container,
            text=summary,
            command=handler,
            anchor="w",
            padx=10,
        ).pack(fill="x", pady=4)

    tk.Button(
        container,
        text="我已付款，立即检查",
        command=lambda: poll_order_once(True),
    ).pack(fill="x", pady=(8, 4))

    tk.Label(
        container,
        textvariable=status_var,
        justify="left",
        wraplength=400,
        fg="#1268d3",
    ).pack(anchor="w", pady=(4, 0))

    extra_lines = []
    if contact:
        extra_lines.append(f"联系方式: {contact}")
    extra_lines.append(f"机器标识: {get_machine_id()}")
    if note:
        extra_lines.append(note)
    if extra_lines:
        tk.Label(
            container,
            text="\n".join(extra_lines),
            justify="left",
            wraplength=360,
            fg="#555555",
        ).pack(anchor="w", pady=(14, 0))

    if preferred_tier:
        root.after(200, lambda: start_purchase(preferred_tier))

    root.mainloop()
    return True


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    subparsers = parser.add_subparsers(dest="purchase_command")

    purchase_parser = subparsers.add_parser("purchase")
    purchase_parser.add_argument(
        "--tier",
        choices=["one_time", "lifetime_updates", "upgrade_updates"],
        help="直接打开某个购买方案",
    )
    purchase_parser.add_argument(
        "--jwt-token",
        default="",
        help="统一登录 JWT，仅用于创建 OneClass 订单",
    )

    activate_parser = subparsers.add_parser("activate")
    activate_parser.add_argument(
        "--tier",
        required=True,
        choices=["one_time", "lifetime_updates"],
    )
    activate_parser.add_argument("--owner", required=True)
    activate_parser.add_argument("--note", default="")

    subparsers.add_parser("status")
    subparsers.add_parser("status-json")
    subparsers.add_parser("sync-license")
    return parser


def handle_cli(args: argparse.Namespace) -> int | None:
    command = getattr(args, "purchase_command", None)
    if not command:
        return None

    cfg = load_config()
    if command == "purchase":
        if getattr(args, "jwt_token", ""):
            os.environ["ONECLASS_LOGIN_JWT"] = args.jwt_token
        if not show_purchase_window(cfg, getattr(args, "tier", None)):
            print_purchase_help(cfg)
            return 1
        return 0
    if command == "activate":
        print("手工 activate 已停用：当前版本只接受服务端签发的 OneClass 授权 token。")
        return 1
    if command == "status":
        check_pending_order(verbose=True)
        cfg = load_config()
        show_status(cfg)
        return 0
    if command == "status-json":
        check_pending_order(verbose=False)
        cfg = load_config()
        show_status_json(cfg)
        return 0
    if command == "sync-license":
        if check_pending_order(verbose=True):
            return 0
        print("还没有检测到已付款订单。")
        return 1
    return 1


def exit_if_purchase_command(argv: list[str]) -> None:
    parser = build_arg_parser()
    args, _ = parser.parse_known_args(argv[1:])
    result = handle_cli(args)
    if result is not None:
        raise SystemExit(result)
