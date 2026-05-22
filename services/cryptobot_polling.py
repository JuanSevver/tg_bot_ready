"""
CryptoBot payment polling.

Каждые 30 секунд запрашивает список оплаченных счетов у CryptoBot
и автоматически выдаёт подписку пользователю.
Не требует домена и HTTPS.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

import aiohttp
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from config import load_config
from database.db import async_session
from database.models import User, Subscription

logger = logging.getLogger(__name__)
_config = load_config()

_BASE = "https://pay.crypt.bot/api"
_POLL_INTERVAL = 30  # секунд


class CryptoBotPoller:
    def __init__(self) -> None:
        self._running = False
        self._processed_invoice_ids: set[int] = set()
        self._bot = None

    def set_bot(self, bot) -> None:
        self._bot = bot

    async def start(self) -> None:
        if not _config.cryptobot_token:
            logger.info("CRYPTOBOT_TOKEN not set — payment polling disabled.")
            return
        self._running = True
        asyncio.create_task(self._poll_loop())
        logger.info("CryptoBot polling started (interval=%ss).", _POLL_INTERVAL)

    async def stop(self) -> None:
        self._running = False

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await self._check_invoices()
            except Exception as e:
                logger.error("CryptoBot polling error: %s", e)
            await asyncio.sleep(_POLL_INTERVAL)

    async def _check_invoices(self) -> None:
        headers = {"Crypto-Pay-API-Token": _config.cryptobot_token}
        params = {"status": "paid", "count": 100}

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_BASE}/getInvoices", headers=headers, params=params
            ) as resp:
                data = await resp.json()

        if not data.get("ok"):
            logger.warning("CryptoBot getInvoices error: %s", data)
            return

        invoices = data.get("result", {}).get("items", [])
        for invoice in invoices:
            invoice_id = invoice.get("invoice_id")
            if invoice_id in self._processed_invoice_ids:
                continue

            self._processed_invoice_ids.add(invoice_id)
            await self._process_invoice(invoice)

    async def _process_invoice(self, invoice: dict) -> None:
        user_id_str = invoice.get("payload", "")
        try:
            user_id = int(user_id_str)
        except (ValueError, TypeError):
            return

        amount = float(invoice.get("amount", 0))
        plans = _config.SUBSCRIPTION_PLANS

        matched_plan = None
        for plan_id, plan in plans.items():
            if plan_id != "trial" and round(amount, 2) == plan["price"]:
                matched_plan = (plan_id, plan)
                break

        if not matched_plan:
            logger.warning("No plan matched for amount %s USDT", amount)
            return

        plan_id, plan = matched_plan

        async with async_session() as session:
            result = await session.execute(
                select(User).where(User.id == user_id).options(selectinload(User.subscription))
            )
            user = result.scalar_one_or_none()
            if not user:
                logger.warning("User %s not found for invoice %s", user_id, invoice.get("invoice_id"))
                return

            now = datetime.utcnow()
            days = plan["days"]
            if user.subscription and user.subscription.is_active:
                expires = user.subscription.expires_at + timedelta(days=days)
            else:
                expires = now + timedelta(days=days)

            if user.subscription:
                user.subscription.plan = plan_id
                user.subscription.expires_at = expires
                user.subscription.purchases_count += 1
            else:
                sub = Subscription(
                    user_id=user.id,
                    plan=plan_id,
                    expires_at=expires,
                    purchases_count=1,
                )
                session.add(sub)

            await session.commit()
            logger.info("Subscription %s granted to user %s via CryptoBot polling", plan_id, user_id)

        # Уведомляем пользователя
        if self._bot:
            try:
                await self._bot.send_message(
                    user_id,
                    f"✅ <b>Оплата получена!</b>\n\n"
                    f"Подписка <b>{plan['label']}</b> активирована.\n"
                    f"Срок: <b>{days} дней</b>",
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.debug("Could not notify user %s: %s", user_id, e)


cryptobot_poller = CryptoBotPoller()
