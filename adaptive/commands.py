# adaptive/bot/commands.py
from dataclasses import dataclass
import logging
from typing import Any
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, Application


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

    # =========================
    # Status & Analysis
    # =========================
    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        sm = self.s.state_manager
        params = sm.get_strategy_params()
        output = "**System Status**\n"
        output += "━━━━━━━━━━━━━━━━━━━━\n"
        output += f"🔹 Market State: {sm.current_state}\n"
        output += f"🔹 Description: {params['description']}\n"
        output += f"🔹 Confidence: {sm.state_confidence:.1%}\n"
        output += f"🔹 Active Symbols: {', '.join(params['allowed_symbols'])}\n"
        output += f"\n**Trading Status**\n"

        active_count = 0
        stopped_count = 0
        for _, cfg in self.s.trade_config.trade_config.items():
            if cfg.stop_trade:
                stopped_count += 1
            else:
                active_count += 1

        output += f"• Active: {active_count} symbols\n"
        output += f"• Stopped: {stopped_count} symbols\n"
        await update.message.reply_text(output)

    async def market_state(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.s.state_manager.update_state()
        params = self.s.state_manager.get_strategy_params()
        output = (
            f"\nCurrent Market State: {self.s.state_manager.current_state}\n"
            f"Description: {params['description']}\n"
            f"Confidence: {self.s.state_manager.state_confidence:.1%}\n"
            f"Last Update: {self.s.state_manager.last_update_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        await update.message.reply_text(output)

    async def state_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        history = self.s.db_manager.get_state_history(24)
        if history:
            output = "Market State History (24h):\n"
            for record in history[:10]:
                output += f"{record['timestamp'].strftime('%m-%d %H:%M')} - {record['state']} (${record.get('btc_price', 0):,.0f})\n"
        else:
            output = "No history available"
        await update.message.reply_text(output)

    async def position_analysis(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        report = await self.s.trader.get_position_analysis_report()
        await update.message.reply_text(report)

    async def risk_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        report = await self.s.trader.get_risk_report()
        await update.message.reply_text(report)

    # =========================
    # Portfolio / Positions
    # =========================
    async def balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """현재 포지션 잔액"""
        output = "**Position Balance Report**\n"
        output += "━━━━━━━━━━━━━━━━━━━━\n"

        total_value = 0.0
        total_profit = 0.0

        # 포지션 갱신 (BinanceTrader.client 주입 필요)
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

                output += f"\n**{symbol}**\n"
                output += f"• Amount: {pos_long:.4f}\n"
                output += f"• Entry: ${entry:.2f}\n"
                output += f"• Current: ${current:.2f}\n"
                output += f"• Value: ${value:,.2f}\n"
                output += f"• P/L: {profit:+.2f}%\n"

                total_value += value
                total_profit += (value - (pos_long * entry))

        output += f"\n**Total**\n"
        output += f"• Value: ${total_value:,.2f}\n"
        output += f"• P/L: ${total_profit:+,.2f}\n"
        await update.message.reply_text(output)

    async def positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """모든 심볼의 현재 설정과 상태"""
        output = "**Current Symbol Configurations**\n"
        output += "━━━━━━━━━━━━━━━━━━━━\n"

        _ = self.s.state_manager.get_strategy_params()  # 현재는 출력에 직접 사용하진 않음

        for symbol, cfg in self.s.trade_config.trade_config.items():
            output += f"\n**{symbol}**\n"
            output += f"• Strategy: {'VA' if cfg.is_va else 'DCA'}\n"
            output += f"• Amount: ${cfg.open_amount:.0f} (Base: ${cfg.base_open_amount:.0f})\n"
            output += f"• Max: ${cfg.max_amount:.0f} (Base: ${cfg.base_max_amount:.0f})\n"
            output += f"• TP: {cfg.take_profit_ratio:.1f}%\n"
            output += f"• SL: {cfg.stop_loss_ratio:.1f}%\n"
            output += f"• LAO: {'Yes' if cfg.is_lao else 'No'}\n"
            output += f"• Stop: {'Yes' if cfg.stop_trade else 'No'}\n"
            output += f"• Reduce Only: {'Yes' if cfg.reduce_only else 'No'}\n"

            # 현재 포지션(있을 때만)
            pos_info = self.s.trader.pos_info_dict[symbol]
            pos = pos_info.position_amt["LONG"]
            if pos > 0:
                output += f"• Position: ${pos * pos_info.price:,.0f}\n"

        await update.message.reply_text(output)

    async def list_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        output = ""
        for k in self.s.trade_config.trade_config:
            output += str(self.s.trade_config.trade_config[k]) + "\n"
        if not output:
            output = "No symbols configured"
        await update.message.reply_text(output)

    async def symbol(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        output = self.s.trade_config.current_symbol
        await update.message.reply_text(output)

    async def update_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.s.trader.update_all()
        await update.message.reply_text("All positions updated")

    # =========================
    # Configuration
    # =========================
    async def current(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        TC = self.s.trade_config
        if len(context.args) == 0:
            if TC.current_symbol:
                cfg = TC.trade_config[TC.current_symbol]
                output = f"**Current Symbol: {TC.current_symbol}**\n"
                output += f"• Amount: ${cfg.open_amount:.0f}\n"
                output += f"• Max: ${cfg.max_amount:.0f}\n"
                output += f"• TP: {cfg.take_profit_ratio}%\n"
                output += f"• SL: {cfg.stop_loss_ratio}%\n"
            else:
                output = "No current symbol set"
        elif len(context.args) == 1:
            symbol = str(context.args[0]).upper()
            if symbol in TC.trade_config:
                TC.current_symbol = symbol
                output = f"Current symbol set to: {symbol}"
            else:
                output = f"Symbol {symbol} not found"
        else:
            output = "Usage: /current [SYMBOL]"
        await update.message.reply_text(output)

    async def volume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        TC = self.s.trade_config
        symbol_config = TC.current_symbol_config()
        if not symbol_config:
            await update.message.reply_text("No symbol selected. Use /current SYMBOL first")
            return

        if len(context.args) == 0:
            output = f"Trading Volume for {TC.current_symbol}: ${symbol_config.open_amount:.0f}"
        elif len(context.args) == 1:
            try:
                vol = float(context.args[0])
                symbol_config.base_open_amount = vol
                symbol_config.open_amount = vol
                output = f"Volume set to ${vol:.0f} for {TC.current_symbol}"
            except ValueError:
                output = "Invalid volume. Use: /volume [amount]"
        else:
            output = "Usage: /volume [amount]"
        await update.message.reply_text(output)

    async def max_amount(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        TC = self.s.trade_config
        symbol_config = TC.current_symbol_config()
        if not symbol_config:
            await update.message.reply_text("No symbol selected. Use /current SYMBOL first")
            return

        if len(context.args) == 0:
            output = f"Max Amount for {TC.current_symbol}: ${symbol_config.max_amount:.0f}"
        elif len(context.args) == 1:
            try:
                max_amt = float(context.args[0])
                symbol_config.base_max_amount = max_amt
                symbol_config.max_amount = max_amt
                output = f"Max amount set to ${max_amt:.0f} for {TC.current_symbol}"
            except ValueError:
                output = "Invalid amount. Use: /max_amount [amount]"
        else:
            output = "Usage: /max_amount [amount]"
        await update.message.reply_text(output)

    async def take_profit_ratio(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        TC = self.s.trade_config
        symbol_config = TC.current_symbol_config()
        if not symbol_config:
            await update.message.reply_text("No symbol selected. Use /current SYMBOL first")
            return

        if len(context.args) == 0:
            output = f"TP Ratio for {TC.current_symbol}: {symbol_config.take_profit_ratio}%"
        elif len(context.args) == 1:
            try:
                ratio = float(context.args[0])
                symbol_config.base_tp_ratio = ratio
                symbol_config.take_profit_ratio = ratio
                output = f"TP ratio set to {ratio}% for {TC.current_symbol}"
            except ValueError:
                output = "Invalid ratio. Use: /take_profit_ratio [percentage]"
        else:
            output = "Usage: /take_profit_ratio [percentage]"
        await update.message.reply_text(output)

    async def stop_loss_ratio(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        TC = self.s.trade_config
        symbol_config = TC.current_symbol_config()
        if not symbol_config:
            await update.message.reply_text("No symbol selected. Use /current SYMBOL first")
            return

        if len(context.args) == 0:
            output = f"SL Ratio for {TC.current_symbol}: {symbol_config.stop_loss_ratio}%"
        elif len(context.args) == 1:
            try:
                ratio = float(context.args[0])
                symbol_config.base_sl_ratio = ratio
                symbol_config.stop_loss_ratio = ratio
                output = f"SL ratio set to {ratio}% for {TC.current_symbol}"
            except ValueError:
                output = "Invalid ratio. Use: /stop_loss_ratio [percentage]"
        else:
            output = "Usage: /stop_loss_ratio [percentage]"
        await update.message.reply_text(output)

    # =========================
    # Trading Control
    # =========================
    async def stop_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        for cfg in self.s.trade_config.trade_config.values():
            cfg.stop_trade = True
        output = "⛔ **All Trading Stopped**\nUse /start_all to resume trading"
        await update.message.reply_text(output)

    async def start_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        for cfg in self.s.trade_config.trade_config.values():
            cfg.stop_trade = False
        output = "✅ **All Trading Resumed**\nTrading will continue based on market state"
        await update.message.reply_text(output)

    async def stop_trade(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        TC = self.s.trade_config
        symbol_config = TC.current_symbol_config()
        if not symbol_config:
            await update.message.reply_text("No symbol selected. Use /current SYMBOL first")
            return

        if len(context.args) == 0:
            output = f"Stop Trade for {TC.current_symbol}: {'Yes' if symbol_config.stop_trade else 'No'}"
        elif len(context.args) == 1:
            mode = str(context.args[0]).lower()
            if mode in ["on", "yes", "1", "true"]:
                symbol_config.stop_trade = True
                output = f"Trading stopped for {TC.current_symbol}"
            elif mode in ["off", "no", "0", "false"]:
                symbol_config.stop_trade = False
                output = f"Trading resumed for {TC.current_symbol}"
            else:
                output = "Use: /stop_trade [on/off]"
        else:
            output = "Usage: /stop_trade [on/off]"
        await update.message.reply_text(output)

    async def close_position(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if len(context.args) != 1:
            output = "Usage: /close_position SYMBOL"
        else:
            symbol = str(context.args[0]).upper()
            if symbol in self.s.trader.pos_info_dict:
                await self.s.trader.close_position(symbol)
                output = f"Closing all positions for {symbol}"
            else:
                output = f"Symbol {symbol} not found"
        await update.message.reply_text(output)

    async def reduce_position(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if len(context.args) != 2:
            output = "Usage: /reduce_position SYMBOL PERCENTAGE"
        else:
            symbol = str(context.args[0]).upper()
            try:
                percentage = float(context.args[1]) / 100.0
                if symbol in self.s.trader.pos_info_dict:
                    await self.s.trader.reduce_position(symbol, percentage)
                    output = f"Reducing {symbol} position by {percentage*100:.0f}%"
                else:
                    output = f"Symbol {symbol} not found"
            except ValueError:
                output = "Invalid percentage"
        await update.message.reply_text(output)

    # (도움말에 있었던 두 명령 보완)
    async def ignore_sl(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """손절 알림 무시 (플래그 해제)"""
        if len(context.args) != 1:
            await update.message.reply_text("Usage: /ignore_sl SYMBOL")
            return
        symbol = str(context.args[0]).upper()
        if symbol not in self.s.trader.pos_info_dict:
            await update.message.reply_text(f"Symbol {symbol} not found")
            return
        pos_info = self.s.trader.pos_info_dict[symbol]
        if hasattr(pos_info, "sl_alert_sent"):
            pos_info.sl_alert_sent = False
        await update.message.reply_text(f"Stop-loss alert for {symbol} is now ignored/reset")

    async def sl_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """손절 알림 상태 확인"""
        flagged = []
        for symbol, pos_info in self.s.trader.pos_info_dict.items():
            if hasattr(pos_info, "sl_alert_sent") and pos_info.sl_alert_sent:
                flagged.append(symbol)
        if flagged:
            await update.message.reply_text("SL alerts active for: " + ", ".join(flagged))
        else:
            await update.message.reply_text("No active SL alerts")

    # (도움말에 표기된 /price 간단 구현)
    async def price(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """현재가 조회: /price [SYMBOL] (없으면 현재 심볼)"""
        if len(context.args) == 0:
            symbol = self.s.trade_config.current_symbol
            if not symbol:
                await update.message.reply_text("No current symbol set. Use /current SYMBOL")
                return
        else:
            symbol = str(context.args[0]).upper()

        if symbol not in self.s.trader.pos_info_dict:
            await update.message.reply_text(f"Symbol {symbol} not found")
            return

        pos_info = self.s.trader.pos_info_dict[symbol]
        price = pos_info.current_price()
        await update.message.reply_text(f"{symbol} price: ${price:,.2f}")

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
/volume [amount] - Set trading volume
/max_amount [amount] - Set max limit
/take_profit_ratio [%] - Set TP ratio
/stop_loss_ratio [%] - Set SL ratio

🎮 <b>Trading Control</b>
/stop_all - Stop all trading
/start_all - Resume all trading
/stop_trade [on/off] - Stop specific symbol
/close_position SYMBOL - Close position
/reduce_position SYMBOL % - Reduce position
/update_all - Force update all
/ignore_sl SYMBOL - Ignore SL alert
/sl_status - Check stop loss status

💡 <b>Others</b>
/price [SYMBOL] - Current price
/list - List all symbols
/symbol - Show current symbol
/help - This message
"""
        await update.message.reply_text(output)

    async def guide(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        guide_text = """📚 **Trading Indicators Guide**
━━━━━━━━━━━━━━━━━━━━

**📊 Score (0-100)**
종합 위치 점수 - 현재 가격의 상대적 위치
- 0-30: 극단적 저점 (매수 강력 신호)
- 30-40: 지지선 근처 (매수 기회)
- 40-60: 중립 구간 (관망)
- 60-70: 저항선 근처 (주의)
- 70-85: 강한 저항 (익절 고려)
- 85-100: 극단적 고점 (익절 강력 신호)

**🏷️ Position Labels**
- EXTREME_OVERSOLD: 극단적 과매도
- STRONG_SUPPORT: 강한 지지선
- NEAR_SUPPORT: 지지선 접근
- NEUTRAL: 중립
- NEAR_RESISTANCE: 저항선 접근
- STRONG_RESISTANCE: 강한 저항선
- EXTREME_OVERBOUGHT: 극단적 과매수

**📈 RSI (0-100)**
14기간 상대강도지수
- 0-30: 과매도 (반등 가능)
- 30-50: 약세
- 50-70: 강세
- 70-100: 과매수 (조정 가능)

**🚀 Momentum (%)**
가격 변화율의 가중평균
- < -5%: 강한 하락세
- -2 ~ -5%: 하락 추세
- -2 ~ +2%: 횡보/약한 추세
- +2 ~ +5%: 상승 추세
- > +5%: 강한 상승세

━━━━━━━━━━━━━━━━━━━━
**💡 Trading Signals**

🔴 **Score > 70 (저항구간)**
- 신규 매수: 위험/중단
- 보유 중: 부분 익절
- 손절선: 타이트하게

🟡 **Score 40-60 (중립)**
- 신규 매수: 표준 전략
- 보유 중: 홀딩
- 손절선: 기본 설정

🟢 **Score < 30 (지지구간)**
- 신규 매수: 적극 진입
- 보유 중: 추가 매수
- 손절선: 여유있게

━━━━━━━━━━━━━━━━━━━━
💭 Use /position_analysis to check current status"""
        await update.message.reply_text(guide_text)

    async def guide_short(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        short_guide = """🎯 **Quick Reference**
━━━━━━━━━━━━━━━━
**Score Levels:**
- 85+: 🔴 극과매수 (매도)
- 70-85: 🟠 과매수 (익절)
- 60-70: 🟡 저항 (주의)
- 40-60: ⚪ 중립 (관망)
- 30-40: 🔵 지지 (매수고려)
- 15-30: 🟢 과매도 (매수)
- 0-15: 🟣 극과매도 (강력매수)

**RSI Levels:**
- 70+: 과매수 ⚠️
- 30-70: 정상 ✅
- 0-30: 과매도 💰

Type /guide for detailed explanation"""
        await update.message.reply_text(short_guide)


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

        # Configuration
        "current": cmds.current,
        "volume": cmds.volume,
        "max_amount": cmds.max_amount,
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
