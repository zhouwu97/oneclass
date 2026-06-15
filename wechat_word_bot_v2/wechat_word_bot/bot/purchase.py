from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
import argparse
import sys
import webbrowser

from .config import ROOT, load_config, save_config


TIER_LABELS = {
    "one_time": "一次性购买",
    "lifetime_updates": "长期更新",
}


def license_is_active(cfg: dict[str, Any]) -> bool:
    license_cfg = cfg.get("license") or {}
    return bool(license_cfg.get("active")) and license_cfg.get("tier") in TIER_LABELS


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


def open_purchase_target(cfg: dict[str, Any], tier: str) -> bool:
    purchase_cfg = cfg.get("purchase") or {}
    tiers = purchase_cfg.get("tiers") or {}
    tier_cfg = tiers.get(tier) or {}
    url = str(tier_cfg.get("payment_url") or "").strip()
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


def print_purchase_help(cfg: dict[str, Any]) -> None:
    purchase_cfg = cfg.get("purchase") or {}
    contact = str(purchase_cfg.get("contact") or "").strip()
    note = str(purchase_cfg.get("post_payment_note") or "").strip()

    print("未授权，当前版本需要先购买后使用。")
    print(f"1. {tier_summary(cfg, 'one_time')}")
    print(f"2. {tier_summary(cfg, 'lifetime_updates')}")
    if contact:
        print(f"付款后联系: {contact}")
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

    print_purchase_help(cfg)
    if not show_purchase_window(cfg):
        print("未能打开购买窗口，请检查 config.yaml 中 purchase.tiers.* 的 payment_url 或 qr_image 配置。")
    return False


def activate_license(tier: str, owner: str, note: str = "") -> None:
    cfg = load_config()
    cfg.setdefault("license", {})
    cfg["license"].update(
        {
            "active": True,
            "tier": tier,
            "owner": owner,
            "note": note,
            "activated_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    save_config(cfg)


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
    root.geometry("420x260")
    root.resizable(False, False)

    container = tk.Frame(root, padx=20, pady=18)
    container.pack(fill="both", expand=True)

    tk.Label(
        container,
        text="请选择购买方案",
        font=("Microsoft YaHei UI", 15, "bold"),
    ).pack(anchor="w")
    tk.Label(
        container,
        text="参考 yunkao 的方式，点击后直接打开对应收款页或收款码。",
        justify="left",
        wraplength=360,
    ).pack(anchor="w", pady=(8, 12))

    for tier in ("one_time", "lifetime_updates"):
        summary = tier_summary(cfg, tier)

        def handler(selected_tier: str = tier) -> None:
            if not open_purchase_target(cfg, selected_tier):
                messagebox.showwarning(
                    "未配置收款地址",
                    "请先在 config.yaml 的 purchase.tiers 下配置 payment_url 或 qr_image。",
                )

        tk.Button(
            container,
            text=summary,
            command=handler,
            anchor="w",
            padx=10,
        ).pack(fill="x", pady=4)

    extra_lines = []
    if contact:
        extra_lines.append(f"付款后联系: {contact}")
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
        root.after(200, lambda: open_purchase_target(cfg, preferred_tier))

    root.mainloop()
    return True


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    subparsers = parser.add_subparsers(dest="purchase_command")

    purchase_parser = subparsers.add_parser("purchase")
    purchase_parser.add_argument(
        "--tier",
        choices=["one_time", "lifetime_updates"],
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
        activate_license(args.tier, args.owner, args.note)
        print("授权已写入 config.yaml")
        return 0
    if command == "status":
        show_status(cfg)
        return 0
    return 1


def exit_if_purchase_command(argv: list[str]) -> None:
    parser = build_arg_parser()
    args, _ = parser.parse_known_args(argv[1:])
    result = handle_cli(args)
    if result is not None:
        raise SystemExit(result)
