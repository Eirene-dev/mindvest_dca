#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Bot Commands (Simplified for 3-Mode system)
"""

from dataclasses import dataclass
import logging
from typing import Any
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, Application
from telegram.constants import ParseMode
import html

SEP = "━━━━━━━━━━━━━━━━━━━━"


@dataclass
class Services:
    trader: "EnhancedTrader"
    state_manager: "MarketStateManager"
    db_manager: "DatabaseManager"
    trade_config: "TradeConfig"
    logger: logging.Logger
    binance_client: Any = None


class Commands:
    def __init__(self, services: Services):
        self.s = services

    async def _send_html(self, update: Update, text: str):
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    # =========================
    # Status & Analysis
    # =========================
    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        sm = self.s.state_manager
        mode = sm.current_mode
        params = sm.mode_params

        lines = [
            "<b>System Status</b>",
            SEP,
            f"📍 State: <code>{html.escape(sm.current_state)}</code>",
            f"🎯 Mode: <b>{html.escape(mode.value)}</b>",
            f"📋 {html.escape(params['description'])}",
            f"• Amount: {params['amount_multiplier']}x",
            f"• Interval: {params['interval_hours']}h",
            f"• Confidence: {sm.state_confidence:.1%}",
            "",
        ]

        active = sum(1 for c in self.s.trade_config.trade_config.values() if not c.stop_trade)
        stopped = sum(1 for c in self.s.trade_config.trade_config.values() if c.stop_trade)
        lines.append(f"Active: <b>{active}</b> / Stopped: <b>{stopped}</b>")

        await self._send_html(update, "\n".join(lines))

    async def market_state(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.s.state_manager.update_state()
        sm = self.s.state_manager
        mode = sm.current_mode
        params = sm.mode_params

        allowed = params['allowed_tickers']
        if allowed is None:
            allowed_str = "All (YAML config)"
        elif not allowed:
            allowed_str = "None"
        else:
            allowed_str = ", ".join(html.escape(s) for s in allowed)

        lines = [
            "<b>Market State</b>",
            SEP,
            f"State: <code>{html.escape(sm.current_state)}</code>",
            f"Mode: <b>{html.escape(mode.value)}</b>",
            f"Confidence: {sm.state_confidence:.1%}",
            f"Last Update: {sm.last_update_time.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            f"<b>Mode Parameters</b>",
            f"• {html.escape(params['description'])}",
            f"• Amount Multiplier: {params['amount_multiplier']}x",
            f"• Interval: {params['interval_hours']}h",
            f"• Allowed Symbols: {allowed_str}",
        ]
        await self._send_html(update, "\n".join(lines))

    async def state_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        history = self.s.db_manager.get_state_history(24)
        if history:
            lines = ["<b>State History (24h)</b>", SEP]
            for record in history[:10]:
                ts = record['timestamp'].strftime('%m-%d %H:%M')
                st = record['state']
                price = f"${record.get('btc_price', 0):,.0f}"
                lines.append(f"{ts} - <code>{html.escape(st)}</code> ({price})")
        else:
            lines = ["<i>No history available</i>"]
        await self._send_html(update, "\n".join(lines))

    async def position_analysis(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        report = await self.s.trader.get_position_analysis_report()
        await self._send_html(update, report)

    async def risk_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        report = await self.s.trader.get_risk_report()
        await self._send_html(update, report)

    # =========================
    # Portfolio / Positions
    # =========================
    async def balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        lines = ["<b>Balance</b>", SEP]
        total_value = 0.0
        total_profit = 0.0

        for symbol, pos_info in self.s.trader.pos_info_dict.items():
            try:
                if self.s.binance_client:
                    pos_info.update(self.s.binance_client)
            except Exception as e:
                self.s.logger.error(f"Balance update error for {symbol}: {e}")

            pos = pos_info.position_amt["LONG"]
            if pos > 0:
                entry = pos_info.entry_price["LONG"]
                current = pos_info.current_price()
                value = pos * current
                profit = ((current - entry) / entry * 100) if entry > 0 else 0

                lines.append(
                    f"\n<b>{html.escape(symbol)}</b>: "
                    f"${value:,.0f} ({profit:+.1f}%)"
                )
                lines.append(f"  Entry: ${entry:,.2f} → ${current:,.2f}")

                total_value += value
                total_profit += (value - pos * entry)

        lines.append(f"\n<b>Total</b>: ${total_value:,.0f} (P/L: ${total_profit:+,.0f})")
        await self._send_html(update, "\n".join(lines))

    async def positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        lines = ["<b>Symbol Configurations</b>", SEP]
        for symbol, cfg in self.s.trade_config.trade_config.items():
            pos_info = self.s.trader.pos_info_dict[symbol]
            pos = pos_info.position_amt["LONG"]

            status = "🟢" if not cfg.stop_trade else "🔴"
            pos_str = f" | Pos: ${pos * pos_info.price:,.0f}" if pos > 0 else ""

            lines.append(
                f"\n{status} <b>{html.escape(symbol)}</b>"
                f" ({'VA' if cfg.is_va else 'DCA'}){pos_str}"
            )
            lines.append(
                f"  Amount: ${cfg.open_amount:,.0f} | "
                f"Max: ${cfg.max_amount:,.0f} | "
                f"TP: {cfg.take_profit_ratio}% | "
                f"SL: {cfg.stop_loss_ratio}%"
            )
        await self._send_html(update, "\n".join(lines))

    async def update_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.s.trader.update_all()
        await self._send_html(update, "✅ All positions updated")

    async def update(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        TC = self.s.trade_config
        if len(context.args) == 1:
            symbol = str(context.args[0]).upper()
        elif TC.current_symbol:
            symbol = TC.current_symbol
        else:
            await self._send_html(update, "Usage: <code>/update SYMBOL</code>")
            return

        ok = await self.s.trader.update_symbol(symbol)
        if ok:
            pos_info = self.s.trader.pos_info_dict[symbol]
            mode = self.s.state_manager.current_mode.value
            await self._send_html(
                update,
                f"✅ <b>{html.escape(symbol)}</b> updated (Mode: {mode}, "
                f"Price: ${pos_info.price:,.2f})"
            )
        else:
            await self._send_html(update, f"Skipped <b>{html.escape(symbol)}</b>")

    # =========================
    # Configuration
    # =========================
    async def current(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        TC = self.s.trade_config
        if len(context.args) == 1:
            symbol = str(context.args[0]).upper()
            if symbol in TC.trade_config:
                TC.current_symbol = symbol
                await self._send_html(update, f"Current → <b>{html.escape(symbol)}</b>")
            else:
                await self._send_html(update, f"<b>{html.escape(symbol)}</b> not found")
        elif TC.current_symbol:
            cfg = TC.trade_config[TC.current_symbol]
            await self._send_html(
                update,
                f"<b>{html.escape(TC.current_symbol)}</b>: "
                f"${cfg.open_amount:,.0f} / Max ${cfg.max_amount:,.0f} / "
                f"TP {cfg.take_profit_ratio}% / SL {cfg.stop_loss_ratio}%"
            )
        else:
            await self._send_html(update, "No current symbol. Use <code>/current SYMBOL</code>")

    async def amount(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        cfg = self.s.trade_config.current_symbol_config()
        if not cfg:
            await self._send_html(update, "No symbol selected")
            return
        if len(context.args) == 1:
            try:
                vol = float(context.args[0])
                cfg.open_amount = vol
                await self._send_html(update, f"Amount → ${vol:,.0f}")
            except ValueError:
                await self._send_html(update, "Invalid amount")
        else:
            await self._send_html(update, f"Amount: ${cfg.open_amount:,.0f}")

    async def max_amount(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        cfg = self.s.trade_config.current_symbol_config()
        if not cfg:
            await self._send_html(update, "No symbol selected")
            return
        if len(context.args) == 1:
            try:
                val = float(context.args[0])
                cfg.max_amount = val
                await self._send_html(update, f"Max → ${val:,.0f}")
            except ValueError:
                await self._send_html(update, "Invalid amount")
        else:
            await self._send_html(update, f"Max: ${cfg.max_amount:,.0f}")

    async def target(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        TC = self.s.trade_config
        if len(context.args) == 0:
            symbol = TC.current_symbol
        elif len(context.args) == 1:
            symbol = TC.current_symbol
            try:
                amount = float(context.args[0])
                pos_info = self.s.trader.pos_info_dict.get(symbol)
                if pos_info:
                    pos_info.va_target_amount = amount
                    await self._send_html(update, f"VA target → ${amount:,.0f}")
                return
            except ValueError:
                await self._send_html(update, "Invalid amount")
                return
        elif len(context.args) == 2:
            symbol = str(context.args[0]).upper()
            try:
                amount = float(context.args[1])
                pos_info = self.s.trader.pos_info_dict.get(symbol)
                if pos_info:
                    pos_info.va_target_amount = amount
                    await self._send_html(update, f"{html.escape(symbol)} VA target → ${amount:,.0f}")
                else:
                    await self._send_html(update, f"{html.escape(symbol)} not found")
                return
            except ValueError:
                await self._send_html(update, "Invalid amount")
                return
        else:
            await self._send_html(update, "Usage: <code>/target [SYMBOL] amount</code>")
            return

        pos_info = self.s.trader.pos_info_dict.get(symbol)
        if pos_info:
            await self._send_html(update, f"VA target: ${pos_info.va_target_amount:,.0f}")
        else:
            await self._send_html(update, "No symbol selected")

    async def take_profit_ratio(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        cfg = self.s.trade_config.current_symbol_config()
        if not cfg:
            await self._send_html(update, "No symbol selected")
            return
        if len(context.args) == 1:
            try:
                cfg.take_profit_ratio = float(context.args[0])
                await self._send_html(update, f"TP → {cfg.take_profit_ratio}%")
            except ValueError:
                await self._send_html(update, "Invalid ratio")
        else:
            await self._send_html(update, f"TP: {cfg.take_profit_ratio}%")

    async def stop_loss_ratio(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        cfg = self.s.trade_config.current_symbol_config()
        if not cfg:
            await self._send_html(update, "No symbol selected")
            return
        if len(context.args) == 1:
            try:
                cfg.stop_loss_ratio = float(context.args[0])
                await self._send_html(update, f"SL → {cfg.stop_loss_ratio}%")
            except ValueError:
                await self._send_html(update, "Invalid ratio")
        else:
            await self._send_html(update, f"SL: {cfg.stop_loss_ratio}%")

    # =========================
    # Trading Control
    # =========================
    async def stop_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        for cfg in self.s.trade_config.trade_config.values():
            cfg.stop_trade = True
        await self._send_html(update, "⛔ All trading stopped")

    async def start_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        for cfg in self.s.trade_config.trade_config.values():
            cfg.stop_trade = False
        await self._send_html(update, "✅ All trading resumed")

    async def stop_trade(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        cfg = self.s.trade_config.current_symbol_config()
        if not cfg:
            await self._send_html(update, "No symbol selected")
            return
        sym = self.s.trade_config.current_symbol
        if len(context.args) == 1:
            mode = str(context.args[0]).lower()
            if mode in ("on", "yes", "1", "true"):
                cfg.stop_trade = True
                await self._send_html(update, f"🔴 {html.escape(sym)} stopped")
            else:
                cfg.stop_trade = False
                await self._send_html(update, f"🟢 {html.escape(sym)} resumed")
        else:
            status = "stopped" if cfg.stop_trade else "active"
            await self._send_html(update, f"{html.escape(sym)}: {status}")

    async def close_position(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if len(context.args) != 1:
            await self._send_html(update, "Usage: <code>/close_position SYMBOL</code>")
            return
        symbol = str(context.args[0]).upper()
        if symbol in self.s.trader.pos_info_dict:
            await self.s.trader.close_position(symbol)
            await self._send_html(update, f"Closing <b>{html.escape(symbol)}</b>")
        else:
            await self._send_html(update, f"{html.escape(symbol)} not found")

    async def reduce_position(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if len(context.args) != 2:
            await self._send_html(update, "Usage: <code>/reduce_position SYMBOL %</code>")
            return
        symbol = str(context.args[0]).upper()
        try:
            pct = float(context.args[1]) / 100.0
            if symbol in self.s.trader.pos_info_dict:
                await self.s.trader.reduce_position(symbol, pct)
                await self._send_html(update, f"Reducing {html.escape(symbol)} by {pct*100:.0f}%")
            else:
                await self._send_html(update, f"{html.escape(symbol)} not found")
        except ValueError:
            await self._send_html(update, "Invalid percentage")

    async def ignore_sl(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if len(context.args) != 1:
            await self._send_html(update, "Usage: <code>/ignore_sl SYMBOL</code>")
            return
        symbol = str(context.args[0]).upper()
        pos_info = self.s.trader.pos_info_dict.get(symbol)
        if pos_info:
            pos_info.sl_alert_sent = False
            await self._send_html(update, f"SL alert reset for {html.escape(symbol)}")
        else:
            await self._send_html(update, f"{html.escape(symbol)} not found")

    async def sl_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        flagged = [s for s, p in self.s.trader.pos_info_dict.items() if p.sl_alert_sent]
        if flagged:
            await self._send_html(update, "SL alerts: " + ", ".join(flagged))
        else:
            await self._send_html(update, "No active SL alerts")

    async def price(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        symbol = str(context.args[0]).upper() if context.args else self.s.trade_config.current_symbol
        if not symbol:
            await self._send_html(update, "Usage: <code>/price SYMBOL</code>")
            return
        pos_info = self.s.trader.pos_info_dict.get(symbol)
        if pos_info:
            p = pos_info.current_price()
            await self._send_html(update, f"{html.escape(symbol)}: <b>${p:,.2f}</b>")
        else:
            await self._send_html(update, f"{html.escape(symbol)} not found")

    # =========================
    # Help
    # =========================
    async def help_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._send_html(update, """<b>Commands</b>
━━━━━━━━━━━━━━━━━━━━
📊 <b>Status</b>
/status - System status
/market_state - Market state & mode
/state_history - State history (24h)
/balance - Position balances
/positions - All configs
/position_analysis - Position scores
/risk_report - Risk report

⚙️ <b>Config</b>
/current [SYMBOL] - Select symbol
/amount [amt] - Set trade amount
/max_amount [amt] - Set max limit
/target [SYMBOL] amt - VA target
/take_profit_ratio [%] - Set TP
/stop_loss_ratio [%] - Set SL

🎮 <b>Control</b>
/stop_all / /start_all
/stop_trade [on/off]
/close_position SYMBOL
/reduce_position SYMBOL %
/update [SYMBOL] / /update_all
/ignore_sl SYMBOL / /sl_status
/price [SYMBOL]
""")


# =========================
# Register
# =========================
def register(application: Application, cmds: Commands):
    command_map = {
        "status": cmds.status,
        "market_state": cmds.market_state,
        "state_history": cmds.state_history,
        "position_analysis": cmds.position_analysis,
        "risk_report": cmds.risk_report,
        "balance": cmds.balance,
        "positions": cmds.positions,
        "update_all": cmds.update_all,
        "update": cmds.update,
        "current": cmds.current,
        "amount": cmds.amount,
        "max_amount": cmds.max_amount,
        "target": cmds.target,
        "take_profit_ratio": cmds.take_profit_ratio,
        "stop_loss_ratio": cmds.stop_loss_ratio,
        "stop_all": cmds.stop_all,
        "start_all": cmds.start_all,
        "stop_trade": cmds.stop_trade,
        "close_position": cmds.close_position,
        "reduce_position": cmds.reduce_position,
        "ignore_sl": cmds.ignore_sl,
        "sl_status": cmds.sl_status,
        "price": cmds.price,
        "help": cmds.help_cmd,
    }
    for name, func in command_map.items():
        application.add_handler(CommandHandler(name, func))