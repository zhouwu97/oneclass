from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
import argparse
import hashlib
import json
import os
import sys
import webbrowser
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .config import ROOT, load_config, save_config


TIER_LABELS = {
    "one_time": "一次性购买",
    "lifetime_updates": "长期更新",
    "upgrade_updates": "补差升级长期更新",
}


def license_is_active(cfg: dict[str, Any]) -> bool:
    license_cfg = cfg.get("license") or {}
    if not bool(license_cfg.get("active")) or license_cfg.get("tier") not in TIER_LABELS:
        return False
    machine_id = str(license_cfg.get("machine_id") or "").strip()
    return not machine_id or machine_id == get_machine_id()


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
    raw = "|".join(
        [
            os.environ.get("COMPUTERNAME", ""),
            os.environ.get("USERNAME", ""),
            str(ROOT),
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


def _read_json(url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None
    headers = {
        "Accept": "application/json",
        "User-Agent": f"OneClass/{get_machine_id()}",
    }
    method = "GET"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    request = Request(url, data=data, headers=headers, method=method)
    with urlopen(request, timeout=12) as response:
        raw = response.read().decode("utf-8")
    parsed = json.loads(raw or "{}")
    if not isinstance(parsed, dict):
        raise ValueError("服务端返回格式不是 JSON 对象")
    return parsed


def create_remote_order(
    cfg: dict[str, Any],
    tier: str,
    pay_type: str = "alipay",
    contact: str = "",
) -> dict[str, Any]:
    base_url = _xiaoxin_base_url(cfg)
    if not base_url:
        raise RuntimeError("未配置 xiaoxin 支付后端")
    payload = {
        "tier": tier,
        "pay_type": pay_type if pay_type in {"alipay", "wechat"} else "alipay",
        "machine_id": get_machine_id(),
        "contact": contact.strip(),
    }
    return _read_json(_join_url(base_url, "/api/oneclass/pay/create"), payload)


def query_remote_order(cfg: dict[str, Any], order_no: str) -> dict[str, Any]:
    base_url = _xiaoxin_base_url(cfg)
    if not base_url:
        raise RuntimeError("未配置 xiaoxin 支付后端")
    query = urlencode({"order_no": order_no})
    return _read_json(_join_url(base_url, "/api/oneclass/pay/status") + "?" + query)


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
    pay_type: str = "alipay",
    contact: str = "",
) -> dict[str, Any]:
    data = create_remote_order(cfg, tier, pay_type, contact)
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
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        if verbose:
            print(f"检查待支付订单失败：{exc}")
        return False

    if not _order_is_paid(data):
        if verbose:
            print(f"待支付订单 {order_no} 当前状态：{data.get('status', 'unknown')}")
        return False

    activate_from_paid_order(data, tier, order_no)
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
    print("管理员可运行: python main.py activate --tier one_time --owner 用户名")


def ensure_license_or_exit(cfg: dict[str, Any]) -> bool:
    purchase_cfg = cfg.get("purchase") or {}
    if not purchase_cfg.get("enabled", True):
        return True
    if not purchase_cfg.get("require_license", True):
        return True
    if license_is_active(cfg):
        return True
    if check_pending_order():
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
    remote_machine_id = str(data.get("machine_id") or "").strip()
    current_machine_id = get_machine_id()
    if remote_machine_id and remote_machine_id != current_machine_id:
        raise RuntimeError("这笔订单绑定的机器标识不是当前电脑")
    tier = _order_tier(data, fallback_tier)
    paid_at = str(data.get("paid_at") or "").strip()
    activate_license(
        tier=tier,
        owner=current_machine_id,
        note="支付成功后自动激活",
        machine_id=current_machine_id,
        order_no=order_no,
        paid_at=paid_at,
    )


def show_status(cfg: dict[str, Any]) -> None:
    license_cfg = cfg.get("license") or {}
    if not license_is_active(cfg):
        print("授权状态: 未激活")
        print_purchase_help(cfg)
        return
    tier = license_cfg.get("tier", "")
    owner = license_cfg.get("owner", "")
    activated_at = license_cfg.get("activated_at", "")
    print(f"授权状态: 已激活 ({TIER_LABELS.get(tier, tier)})")
    if owner:
        print(f"授权对象: {owner}")
    machine_id = license_cfg.get("machine_id", "")
    if machine_id:
        print(f"机器标识: {machine_id}")
    order_no = license_cfg.get("order_no", "")
    if order_no:
        print(f"付款订单: {order_no}")
    if activated_at:
        print(f"激活时间: {activated_at}")


def show_purchase_window(cfg: dict[str, Any], preferred_tier: str | None = None) -> bool:
    try:
        import tkinter as tk
        from tkinter import messagebox
    except Exception:
        if preferred_tier:
            return open_purchase_target(cfg, preferred_tier)
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
    pay_type_var = tk.StringVar(value="alipay")
    active_order: dict[str, str] = {}

    tk.Label(
        container,
        text="请选择购买方案",
        font=("Microsoft YaHei UI", 15, "bold"),
    ).pack(anchor="w")
    provider = str(purchase_cfg.get("provider") or "local").strip().lower()
    if provider == "xiaoxin":
        guide_text = "当前已按 xiaoxin 后端的易支付方式接入，点击后直接打开对应商品页或支付页。"
    else:
        guide_text = "点击后直接打开对应易支付收款页。"
    tk.Label(
        container,
        text=guide_text + " 支付完成后本窗口会自动检查订单，并把授权绑定到当前机器标识。",
        justify="left",
        wraplength=360,
    ).pack(anchor="w", pady=(8, 12))

    form = tk.Frame(container)
    form.pack(fill="x", pady=(0, 8))
    tk.Label(form, text="联系方式（可选）").grid(row=0, column=0, sticky="w")
    tk.Entry(form, textvariable=contact_var).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 8))
    tk.Radiobutton(form, text="支付宝", variable=pay_type_var, value="alipay").grid(row=2, column=0, sticky="w")
    tk.Radiobutton(form, text="微信", variable=pay_type_var, value="wechat").grid(row=2, column=1, sticky="w")
    form.columnconfigure(0, weight=1)
    form.columnconfigure(1, weight=1)

    def start_purchase(selected_tier: str) -> None:
        try:
            order = begin_remote_purchase(
                cfg,
                selected_tier,
                pay_type_var.get(),
                contact_var.get(),
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
            if open_purchase_target(cfg, selected_tier):
                status_var.set(f"自动创建订单失败，已打开备用购买页：{exc}")
                return
            messagebox.showwarning(
                "未配置收款地址",
                f"请检查支付后端或 config.yaml 收款配置。\n\n{exc}",
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
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
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

    activate_parser = subparsers.add_parser("activate")
    activate_parser.add_argument(
        "--tier",
        required=True,
        choices=["one_time", "lifetime_updates"],
    )
    activate_parser.add_argument("--owner", required=True)
    activate_parser.add_argument("--note", default="")

    subparsers.add_parser("status")
    subparsers.add_parser("sync-license")
    return parser


def handle_cli(args: argparse.Namespace) -> int | None:
    command = getattr(args, "purchase_command", None)
    if not command:
        return None

    cfg = load_config()
    if command == "purchase":
        if not show_purchase_window(cfg, getattr(args, "tier", None)):
            print_purchase_help(cfg)
            return 1
        return 0
    if command == "activate":
        activate_license(args.tier, args.owner, args.note, machine_id=get_machine_id())
        print("授权已写入 config.yaml")
        return 0
    if command == "status":
        check_pending_order(verbose=True)
        cfg = load_config()
        show_status(cfg)
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
