# adaptive/bot/commands.py
from dataclasses import dataclass
import logging
from typing import Any
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, Application
from telegram.constants import ParseMode
import html  # 표 값/심볼 안전 이스케이프용

SEP = "━━━━━━━━━━━━━━━━━━━━"

# 순환 import 방지를 위해 문자열 타입 힌트 사용
@dataclass
class Services:
    trader: "EnhancedTrader"
    state_manager: "MarketStateManager"
    db_manager: "DatabaseManager"
    trade_config: "TradeConfig"
    logger: logging.Logger
    # balance 명령에서 포지션 갱신용으로 사용 (예: BinanceTrader.client)
    binance_client: Any = None


class Commands:
    def __init__(self, services: Services):
        self.s = services

    # 공통 HTML 응답 유틸
    async def _send_html(self, update: Update, text: str):
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    # =========================
    # Status & Analysis (Updated)
    # =========================
    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        sm = self.s.state_manager
        params = sm.get_strategy_params()

        symbols = ", ".join(html.escape(s) for s in params["allowed_symbols"])

        # MarketRegime 정보 추가
        regime_str = "N/A"
        if hasattr(sm, 'current_regime'):
            regime_str = html.escape(sm.current_regime.value)
        
        sentiment_str = "N/A"
        if hasattr(sm, 'current_sentiment'):
            sentiment_str = html.escape(sm.current_sentiment)
        
        ai_signal_str = "N/A"
        if hasattr(sm, 'current_ai_signal'):
            ai_signal_str = html.escape(sm.current_ai_signal)
        
        regime_confidence = 0.5
        if hasattr(sm, 'regime_confidence'):
            regime_confidence = sm.regime_confidence

        output = (
            "<b>System Status</b>\n"
            f"{SEP}\n"
            f"📍 <b>Market Indicators</b>\n"
            f"• State: <code>{html.escape(sm.current_state)}</code>\n"
            f"• Regime: <b>{regime_str}</b>\n"
            f"• Sentiment: {sentiment_str}\n"
            f"• AI Signal: {ai_signal_str}\n"
            f"• Confidence: {regime_confidence:.1%}\n"
            f"\n📋 <b>Strategy</b>\n"
            f"• Description: {html.escape(params['description'])}\n"
            f"• Active Symbols: {symbols}\n"
            f"\n<b>Trading Status</b>\n"
        )

        active_count = sum(1 for _, cfg in self.s.trade_config.trade_config.items() if not cfg.stop_trade)
        stopped_count = sum(1 for _, cfg in self.s.trade_config.trade_config.items() if cfg.stop_trade)

        output += f"• Active: <b>{active_count}</b> symbols\n"
        output += f"• Stopped: <b>{stopped_count}</b> symbols\n"

        await self._send_html(update, output)

    async def market_state(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.s.state_manager.update_state()
        sm = self.s.state_manager
        params = sm.get_strategy_params()
        
        # MarketRegime 정보 추가
        regime_str = "N/A"
        if hasattr(sm, 'current_regime'):
            regime_str = html.escape(sm.current_regime.value)
        
        sentiment_str = "N/A"
        if hasattr(sm, 'current_sentiment'):
            sentiment_str = html.escape(sm.current_sentiment)
        
        ai_signal_str = "N/A"
        if hasattr(sm, 'current_ai_signal'):
            ai_signal_str = html.escape(sm.current_ai_signal)
        
        regime_confidence = 0.5
        if hasattr(sm, 'regime_confidence'):
            regime_confidence = sm.regime_confidence
        
        output = (
            "<b>Current Market State</b>\n"
            f"{SEP}\n"
            f"🔹 <b>Core State</b>\n"
            f"• State: <code>{html.escape(sm.current_state)}</code>\n"
            f"• State Confidence: {sm.state_confidence:.1%}\n"
            f"\n🔹 <b>Market Regime</b>\n"
            f"• Regime: <b>{regime_str}</b>\n"
            f"• Regime Confidence: {regime_confidence:.1%}\n"
            f"\n🔹 <b>Market Signals</b>\n"
            f"• Sentiment: {sentiment_str}\n"
            f"• AI Signal: {ai_signal_str}\n"
            f"\n🔹 <b>Strategy</b>\n"
            f"• Description: {html.escape(params['description'])}\n"
            f"• Amount Mult: {params.get('amount_multiplier', 0)}x\n"
            f"• Interval: {params.get('interval_hours', 0)}h\n"
            f"• Last Update: {html.escape(sm.last_update_time.strftime('%Y-%m-%d %H:%M:%S'))}\n"
        )
        await self._send_html(update, output)

    async def state_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        history = self.s.db_manager.get_state_history(24)
        if history:
            lines = ["<b>Market State History (24h)</b>", SEP]
            for record in history[:10]:
                ts = html.escape(record['timestamp'].strftime('%m-%d %H:%M'))
                st = html.escape(record['state'])
                price = f"${record.get('btc_price', 0):,.0f}"
                lines.append(f"{ts} - <code>{st}</code> ({price})")
            output = "\n".join(lines)
        else:
            output = "<i>No history available</i>"
        await self._send_html(update, output)

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
        """현재 포지션 잔액"""
        lines = ["<b>Position Balance Report</b>", SEP]

        total_value = 0.0
        total_profit = 0.0

        for symbol, pos_info in self.s.trader.pos_info_dict.items():
            try:
                if self.s.binance_client is not None:
                    pos_info.update(self.s.binance_client)
            except Exception as e:
                self.s.logger.error(f"Balance update error for {symbol}: {e}")
            pos_long = pos_info.position_amt["LONG"]

            if pos_long > 0:
                entry = pos_info.entry_price["LONG"]
                current = pos_info.current_price()
                value = pos_long * current
                profit = ((current - entry) / entry * 100) if entry > 0 else 0

                lines.append(f"\n<b>{html.escape(symbol)}</b>")
                lines.append(f"• Volume: {pos_long:.4f}")
                lines.append(f"• Entry: ${entry:.2f}")
                lines.append(f"• Current: ${current:.2f}")
                lines.append(f"• Value: ${value:,.2f}")
                lines.append(f"• P/L: {profit:+.2f}%")

                total_value += value
                total_profit += (value - (pos_long * entry))

        lines.append("\n<b>Total</b>")
        lines.append(f"• Value: ${total_value:,.2f}")
        lines.append(f"• P/L: ${total_profit:+,.2f}")

        await self._send_html(update, "\n".join(lines))

    async def positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """모든 심볼의 현재 설정과 상태"""
        lines = ["<b>Current Symbol Configurations</b>", SEP]

        for symbol, cfg in self.s.trade_config.trade_config.items():
            lines.append(f"\n<b>{html.escape(symbol)}</b>")
            lines.append(f"• Strategy: {'VA' if cfg.is_va else 'DCA'}")
            lines.append(f"• Amount: ${cfg.open_amount:.0f} (Base: ${cfg.base_open_amount:.0f})")
            lines.append(f"• Max: ${cfg.max_amount:.0f} (Base: ${cfg.base_max_amount:.0f})")
            lines.append(f"• TP: {cfg.take_profit_ratio:.1f}%")
            lines.append(f"• SL: {cfg.stop_loss_ratio:.1f}%")
            lines.append(f"• LAO: {'Yes' if cfg.is_lao else 'No'}")
            lines.append(f"• Stop: {'Yes' if cfg.stop_trade else 'No'}")
            lines.append(f"• Reduce Only: {'Yes' if cfg.reduce_only else 'No'}")

            pos_info = self.s.trader.pos_info_dict[symbol]
            pos = pos_info.position_amt["LONG"]
            if pos > 0:
                lines.append(f"• Position: ${pos * pos_info.price:,.0f}")

        await self._send_html(update, "\n".join(lines))

    async def list_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.s.trade_config.trade_config:
            await self._send_html(update, "<i>No symbols configured</i>")
            return
        # __str__ 출력은 그대로 <pre> 처리
        output = "\n".join(str(cfg) for cfg in self.s.trade_config.trade_config.values())
        await self._send_html(update, output)

    async def symbol(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        output = html.escape(self.s.trade_config.current_symbol or "No current symbol set")
        await self._send_html(update, output)

    async def update_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.s.trader.update_all()
        await self._send_html(update, "All positions updated")

    async def update(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        /update              -> 현재 심볼만 업데이트+트레이드
        /update SYMBOL       -> 특정 심볼만 업데이트+트레이드
        """
        TC = self.s.trade_config

        # 대상 심볼 결정
        if len(context.args) == 0:
            if not TC.current_symbol:
                await self._send_html(update, "No current symbol set. Use <code>/current SYMBOL</code> first")
                return
            symbol = TC.current_symbol
        elif len(context.args) == 1:
            symbol = str(context.args[0]).upper()
        else:
            await self._send_html(update, "Usage: <code>/update</code> or <code>/update SYMBOL</code>")
            return

        pos_info = self.s.trader.pos_info_dict.get(symbol)
        if not pos_info:
            await self._send_html(update, f"Symbol <b>{html.escape(symbol)}</b> not found")
            return

        # 실행
        ok = await self.s.trader.update_symbol(symbol)

        # 응답 메시지
        if not ok:
            # stop_trade 등으로 스킵된 경우
            cfg = self.s.trade_config.trade_config.get(symbol)
            reason = ""
            if cfg and getattr(cfg, "stop_trade", False):
                reason = " (trading is <b>stopped</b> for this symbol)"
            await self._send_html(update, f"Update skipped for <b>{html.escape(symbol)}</b>{reason}")
            return

        # 간단 요약
        price = pos_info.price
        pos = pos_info.position_amt["LONG"]
        entry = pos_info.entry_price["LONG"]
        pnl = ((price - entry) / entry * 100) if entry > 0 else 0.0
        lines = [
            f"✅ <b>Updated {html.escape(symbol)}</b>",
            f"{SEP}",
            f"• Market State: <code>{html.escape(self.s.state_manager.current_state)}</code>",
            f"• Price: ${price:,.2f}",
        ]
        if pos > 0:
            lines.append(f"• Position: {pos:.4f}  |  P/L: {pnl:+.2f}%")
        await self._send_html(update, "\n".join(lines))

    # =========================
    # Configuration
    # =========================
    async def current(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        TC = self.s.trade_config
        if len(context.args) == 0:
            if TC.current_symbol:
                cfg = TC.trade_config[TC.current_symbol]
                output = (
                    f"<b>Current Symbol: {html.escape(TC.current_symbol)}</b>\n"
                    f"• Amount: ${cfg.open_amount:.0f}\n"
                    f"• Max: ${cfg.max_amount:.0f}\n"
                    f"• TP: {cfg.take_profit_ratio}%\n"
                    f"• SL: {cfg.stop_loss_ratio}%\n"
                )
            else:
                output = "<i>No current symbol set</i>"
        elif len(context.args) == 1:
            symbol = str(context.args[0]).upper()
            if symbol in TC.trade_config:
                TC.current_symbol = symbol
                output = f"Current symbol set to: <b>{html.escape(symbol)}</b>"
            else:
                output = f"Symbol <b>{html.escape(symbol)}</b> not found"
        else:
            output = "Usage: <code>/current [SYMBOL]</code>"
        await self._send_html(update, output)

    async def amount(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        TC = self.s.trade_config
        symbol_config = TC.current_symbol_config()
        if not symbol_config:
            await self._send_html(update, "No symbol selected. Use <code>/current SYMBOL</code> first")
            return

        if len(context.args) == 0:
            output = f"Trading Amount for <b>{html.escape(TC.current_symbol)}</b>: ${symbol_config.open_amount:.0f}"
        elif len(context.args) == 1:
            try:
                vol = float(context.args[0])
                symbol_config.base_open_amount = vol
                symbol_config.open_amount = vol
                output = f"Amount set to ${vol:.0f} for <b>{html.escape(TC.current_symbol)}</b>"
            except ValueError:
                output = "Invalid amount. Use: <code>/amount [amount]</code>"
        else:
            output = "Usage: <code>/amount [amount]</code>"
        await self._send_html(update, output)

    async def max_amount(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        TC = self.s.trade_config
        symbol_config = TC.current_symbol_config()
        if not symbol_config:
            await self._send_html(update, "No symbol selected. Use <code>/current SYMBOL</code> first")
            return

        if len(context.args) == 0:
            output = f"Max Amount for <b>{html.escape(TC.current_symbol)}</b>: ${symbol_config.max_amount:.0f}"
        elif len(context.args) == 1:
            try:
                max_amt = float(context.args[0])
                symbol_config.base_max_amount = max_amt
                symbol_config.max_amount = max_amt
                output = f"Max amount set to ${max_amt:.0f} for <b>{html.escape(TC.current_symbol)}</b>"
            except ValueError:
                output = "Invalid amount. Use: <code>/max_amount [amount]</code>"
        else:
            output = "Usage: <code>/max_amount [amount]</code>"
        await self._send_html(update, output)

    async def target(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        /target                -> 현재 심볼의 타겟 조회
        /target AMOUNT         -> 현재 심볼의 타겟 설정
        /target SYMBOL AMOUNT  -> 특정 심볼의 타겟 설정
        """
        TC = self.s.trade_config

        # 0개 인자: 조회 (현재 심볼 필요)
        if len(context.args) == 0:
            if not TC.current_symbol:
                await self._send_html(update, "No current symbol set. Use <code>/current SYMBOL</code> first")
                return
            symbol = TC.current_symbol
            pos_info = self.s.trader.pos_info_dict.get(symbol)
            if not pos_info:
                await self._send_html(update, f"Symbol <b>{html.escape(symbol)}</b> not found")
                return
            note = ""
            cfg = TC.trade_config.get(symbol)
            if cfg and not getattr(cfg, "is_va", False):
                note = " <i>(DCA mode; VA target is used only in VA strategy)</i>"
            await self._send_html(
                update,
                f"VA target for <b>{html.escape(symbol)}</b>: <b>${pos_info.va_target_amount:,.0f}</b>{note}"
            )
            return

        # 1개 인자: 현재 심볼의 타겟 설정
        if len(context.args) == 1:
            if not TC.current_symbol:
                await self._send_html(update, "No current symbol set. Use <code>/current SYMBOL</code> first")
                return
            symbol = TC.current_symbol
            try:
                amount = float(context.args[0])
            except ValueError:
                await self._send_html(update, "Invalid amount. Use: <code>/target [amount]</code> or <code>/target SYMBOL amount</code>")
                return

        # 2개 인자: 특정 심볼의 타겟 설정
        elif len(context.args) == 2:
            symbol = str(context.args[0]).upper()
            try:
                amount = float(context.args[1])
            except ValueError:
                await self._send_html(update, "Invalid amount. Use: <code>/target SYMBOL amount</code>")
                return
        else:
            await self._send_html(update, "Usage: <code>/target [amount]</code> or <code>/target SYMBOL amount</code>")
            return

        # 공통: 설정 적용
        if amount <= 0:
            await self._send_html(update, "Amount must be positive.")
            return

        pos_info = self.s.trader.pos_info_dict.get(symbol)
        if not pos_info:
            await self._send_html(update, f"Symbol <b>{html.escape(symbol)}</b> not found")
            return

        old = pos_info.va_target_amount
        pos_info.va_target_amount = amount

        extra = ""
        cfg = TC.trade_config.get(symbol)
        if cfg and not getattr(cfg, "is_va", False):
            extra = " <i>(Note: current strategy is DCA; VA target is only used in VA)</i>"

        await self._send_html(
            update,
            f"VA target for <b>{html.escape(symbol)}</b> updated from "
            f"<b>${old:,.0f}</b> to <b>${amount:,.0f}</b>.{extra}"
        )

    async def take_profit_ratio(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        TC = self.s.trade_config
        symbol_config = TC.current_symbol_config()
        if not symbol_config:
            await self._send_html(update, "No symbol selected. Use <code>/current SYMBOL</code> first")
            return

        if len(context.args) == 0:
            output = f"TP Ratio for <b>{html.escape(TC.current_symbol)}</b>: {symbol_config.take_profit_ratio}%"
        elif len(context.args) == 1:
            try:
                ratio = float(context.args[0])
                symbol_config.base_tp_ratio = ratio
                symbol_config.take_profit_ratio = ratio
                output = f"TP ratio set to {ratio}% for <b>{html.escape(TC.current_symbol)}</b>"
            except ValueError:
                output = "Invalid ratio. Use: <code>/take_profit_ratio [percentage]</code>"
        else:
            output = "Usage: <code>/take_profit_ratio [percentage]</code>"
        await self._send_html(update, output)

    async def stop_loss_ratio(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        TC = self.s.trade_config
        symbol_config = TC.current_symbol_config()
        if not symbol_config:
            await self._send_html(update, "No symbol selected. Use <code>/current SYMBOL</code> first")
            return

        if len(context.args) == 0:
            output = f"SL Ratio for <b>{html.escape(TC.current_symbol)}</b>: {symbol_config.stop_loss_ratio}%"
        elif len(context.args) == 1:
            try:
                ratio = float(context.args[0])
                symbol_config.base_sl_ratio = ratio
                symbol_config.stop_loss_ratio = ratio
                output = f"SL ratio set to {ratio}% for <b>{html.escape(TC.current_symbol)}</b>"
            except ValueError:
                output = "Invalid ratio. Use: <code>/stop_loss_ratio [percentage]</code>"
        else:
            output = "Usage: <code>/stop_loss_ratio [percentage]</code>"
        await self._send_html(update, output)

    # =========================
    # Trading Control
    # =========================
    async def stop_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        for cfg in self.s.trade_config.trade_config.values():
            cfg.stop_trade = True
        output = "⛔ <b>All Trading Stopped</b>\nUse <code>/start_all</code> to resume trading"
        await self._send_html(update, output)

    async def start_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        for cfg in self.s.trade_config.trade_config.values():
            cfg.stop_trade = False
        output = "✅ <b>All Trading Resumed</b>\nTrading will continue based on market state"
        await self._send_html(update, output)

    async def stop_trade(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        TC = self.s.trade_config
        symbol_config = TC.current_symbol_config()
        if not symbol_config:
            await self._send_html(update, "No symbol selected. Use <code>/current SYMBOL</code> first")
            return

        if len(context.args) == 0:
            output = f"Stop Trade for <b>{html.escape(TC.current_symbol)}</b>: {'Yes' if symbol_config.stop_trade else 'No'}"
        elif len(context.args) == 1:
            mode = str(context.args[0]).lower()
            if mode in ["on", "yes", "1", "true"]:
                symbol_config.stop_trade = True
                output = f"Trading stopped for <b>{html.escape(TC.current_symbol)}</b>"
            elif mode in ["off", "no", "0", "false"]:
                symbol_config.stop_trade = False
                output = f"Trading resumed for <b>{html.escape(TC.current_symbol)}</b>"
            else:
                output = "Use: <code>/stop_trade [on/off]</code>"
        else:
            output = "Usage: <code>/stop_trade [on/off]</code>"
        await self._send_html(update, output)

    async def close_position(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if len(context.args) != 1:
            output = "Usage: <code>/close_position SYMBOL</code>"
        else:
            symbol = str(context.args[0]).upper()
            if symbol in self.s.trader.pos_info_dict:
                await self.s.trader.close_position(symbol)
                output = f"Closing all positions for <b>{html.escape(symbol)}</b>"
            else:
                output = f"Symbol <b>{html.escape(symbol)}</b> not found"
        await self._send_html(update, output)

    async def reduce_position(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if len(context.args) != 2:
            output = "Usage: <code>/reduce_position SYMBOL PERCENTAGE</code>"
        else:
            symbol = str(context.args[0]).upper()
            try:
                percentage = float(context.args[1]) / 100.0
                if symbol in self.s.trader.pos_info_dict:
                    await self.s.trader.reduce_position(symbol, percentage)
                    output = f"Reducing <b>{html.escape(symbol)}</b> position by {percentage*100:.0f}%"
                else:
                    output = f"Symbol <b>{html.escape(symbol)}</b> not found"
            except ValueError:
                output = "Invalid percentage"
        await self._send_html(update, output)

    async def ignore_sl(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """손절 알림 무시 (플래그 해제)"""
        if len(context.args) != 1:
            await self._send_html(update, "Usage: <code>/ignore_sl SYMBOL</code>")
            return
        symbol = str(context.args[0]).upper()
        if symbol not in self.s.trader.pos_info_dict:
            await self._send_html(update, f"Symbol <b>{html.escape(symbol)}</b> not found")
            return
        pos_info = self.s.trader.pos_info_dict[symbol]
        if hasattr(pos_info, "sl_alert_sent"):
            pos_info.sl_alert_sent = False
        await self._send_html(update, f"Stop-loss alert for <b>{html.escape(symbol)}</b> is now ignored/reset")

    async def sl_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """손절 알림 상태 확인"""
        flagged = []
        for symbol, pos_info in self.s.trader.pos_info_dict.items():
            if hasattr(pos_info, "sl_alert_sent") and pos_info.sl_alert_sent:
                flagged.append(symbol)
        if flagged:
            await self._send_html(update, "SL alerts active for: " + ", ".join(html.escape(s) for s in flagged))
        else:
            await self._send_html(update, "No active SL alerts")

    async def price(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """현재가 조회: /price [SYMBOL] (없으면 현재 심볼)"""
        if len(context.args) == 0:
            symbol = self.s.trade_config.current_symbol
            if not symbol:
                await self._send_html(update, "No current symbol set. Use <code>/current SYMBOL</code>")
                return
        else:
            symbol = str(context.args[0]).upper()

        if symbol not in self.s.trader.pos_info_dict:
            await self._send_html(update, f"Symbol <b>{html.escape(symbol)}</b> not found")
            return

        pos_info = self.s.trader.pos_info_dict[symbol]
        price = pos_info.current_price()
        await self._send_html(update, f"{html.escape(symbol)} price: <b>${price:,.2f}</b>")

    # =========================
    # Guides / Help
    # =========================
    async def help_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        output = """<b>Available Commands</b>
━━━━━━━━━━━━━━━━━━━━
📊 <b>Status & Analysis</b>
/status - System status
/balance - Position balances
/positions - All configurations
/market_state - Current market state
/position_analysis - Asset position analysis
/risk_report - Risk management report
/guide - Indicator interpretation guide
/guide_short - Quick reference

⚙️ <b>Configuration</b>
/current [SYMBOL] - Select/view symbol
/amount [amount] - Set trading amount
/max_amount [amount] - Set max limit
/target [amount]|SYMBOL amount - Set/view VA target
/take_profit_ratio [%] - Set TP ratio
/stop_loss_ratio [%] - Set SL ratio

🎮 <b>Trading Control</b>
/stop_all - Stop all trading
/start_all - Resume all trading
/stop_trade [on/off] - Stop specific symbol
/close_position SYMBOL - Close position
/reduce_position SYMBOL % - Reduce position
/update_all - Force update all
/update SYMBOL - Update & trade the specified symbol
/ignore_sl SYMBOL - Ignore SL alert
/sl_status - Check stop loss status

💡 <b>Others</b>
/price [SYMBOL] - Current price
/list - List all symbols
/symbol - Show current symbol
/help - This message
"""
        await self._send_html(update, output)

    async def guide(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        guide_text = """📚 Trading Indicators Guide
━━━━━━━━━━━━━━━━━━━━

📊 <b>Score (0-100)</b>
종합 위치 점수 - 현재 가격의 상대적 위치
- 0-30: 극단적 저점 (매수 강력 신호)
- 30-40: 지지선 근처 (매수 기회)
- 40-60: 중립 구간 (관망)
- 60-70: 저항선 근처 (주의)
- 70-85: 강한 저항 (익절 고려)
- 85-100: 극단적 고점 (익절 강력 신호)

🏷️ <b>Position Labels</b>
- EXTREME_OVERSOLD: 극단적 과매도
- STRONG_SUPPORT: 강한 지지선
- NEAR_SUPPORT: 지지선 접근
- NEUTRAL: 중립
- NEAR_RESISTANCE: 저항선 접근
- STRONG_RESISTANCE: 강한 저항선
- EXTREME_OVERBOUGHT: 극단적 과매수

📈 <b>RSI (0-100)</b>
14기간 상대강도지수
- 0-30: 과매도 (반등 가능)
- 30-50: 약세
- 50-70: 강세
- 70-100: 과매수 (조정 가능)

🚀 <b>Momentum (%)</b>
가격 변화율의 가중평균
- &lt; -5%: 강한 하락세
- -2 ~ -5%: 하락 추세
- -2 ~ +2%: 횡보/약한 추세
- +2 ~ +5%: 상승 추세
- &gt; +5%: 강한 상승세

━━━━━━━━━━━━━━━━━━━━
<b>Trading Signals</b>

🔴 <b>Score &gt; 70 (저항구간)</b>
- 신규 매수: 위험/중단
- 보유 중: 부분 익절
- 손절선: 타이트하게

🟡 <b>Score 40-60 (중립)</b>
- 신규 매수: 표준 전략
- 보유 중: 홀딩
- 손절선: 기본 설정

🟢 <b>Score &lt; 30 (지지)</b>
- 신규 매수: 적극 진입
- 보유 중: 추가 매수
- 손절선: 여유있게

━━━━━━━━━━━━━━━━━━━━
💭 Use <code>/position_analysis</code> to check current status
"""
        await self._send_html(update, guide_text)

    async def guide_short(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        short_guide = """🎯 <b>Quick Reference</b>
━━━━━━━━━━━━━━━━
<b>Score Levels:</b>
- 85+: 🔴 극과매수 (매도)
- 70-85: 🟠 과매수 (익절)
- 60-70: 🟡 저항 (주의)
- 40-60: ⚪ 중립 (관망)
- 30-40: 🔵 지지 (매수고려)
- 15-30: 🟢 과매도 (매수)
- 0-15: 🟣 극과매도 (강력매수)

<b>RSI Levels:</b>
- 70+: 과매수 ⚠️
- 30-70: 정상 ✅
- 0-30: 과매도 💰

Type <code>/guide</code> for detailed explanation
"""
        await self._send_html(update, short_guide)


# =========================
# Register helpers
# =========================
def register(application: Application, cmds: Commands):
    command_map = {
        # Status & Analysis
        "status": cmds.status,
        "market_state": cmds.market_state,
        "state_history": cmds.state_history,
        "position_analysis": cmds.position_analysis,
        "risk_report": cmds.risk_report,

        # Portfolio / Positions
        "balance": cmds.balance,
        "positions": cmds.positions,
        "list": cmds.list_cmd,
        "symbol": cmds.symbol,
        "update_all": cmds.update_all,
        "update": cmds.update,

        # Configuration
        "current": cmds.current,
        "amount": cmds.amount,
        "max_amount": cmds.max_amount,
        "target": cmds.target,
        "take_profit_ratio": cmds.take_profit_ratio,
        "stop_loss_ratio": cmds.stop_loss_ratio,

        # Trading Control
        "stop_all": cmds.stop_all,
        "start_all": cmds.start_all,
        "stop_trade": cmds.stop_trade,
        "close_position": cmds.close_position,
        "reduce_position": cmds.reduce_position,
        "ignore_sl": cmds.ignore_sl,
        "sl_status": cmds.sl_status,

        # Guides / Help / Utility
        "price": cmds.price,
        "guide": cmds.guide,
        "guide_short": cmds.guide_short,
        "help": cmds.help_cmd,
    }

    for name, func in command_map.items():
        application.add_handler(CommandHandler(name, func))
