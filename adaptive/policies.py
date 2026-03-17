#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Trading policy matrices & helpers
- 리스크 티어
- 마켓 상태 전략 매트릭스
- 포지션 조정 매트릭스
- 조회/헬퍼 함수

다른 모듈에서 import 하여 사용하세요:
from adaptive.policies import (
    SYMBOL_RISK_TIERS, TIER_MAX_MULTIPLIERS,
    STATE_STRATEGY_MATRIX, POSITION_ADJUSTMENT_MATRIX,
    get_state_params, get_adjustments, get_symbol_tier, get_tier_multiplier,
)
"""

from typing import Optional, Dict, Any, Tuple, List

# ==========================================
# Risk Tier System
# ==========================================

SYMBOL_RISK_TIERS: Dict[str, List[str]] = {
    'TIER_1': ['BTC', 'ETH'],           # Core assets
    'TIER_2': ['SOL', 'LINK'],          # Quality alts
    'TIER_3': ['XRP', 'ADA', 'DOGE']    # High-beta/meme
}

# Max amount multipliers by tier
TIER_MAX_MULTIPLIERS: Dict[str, float] = {
    'TIER_1': 1.0,   # BTC, ETH - 100% of base max
    'TIER_2': 0.7,   # SOL, LINK - 70% of base max
    'TIER_3': 0.4    # XRP, ADA, DOGE - 40% of base max
}

# ==========================================
# Market State Strategy Matrix
# ==========================================

STATE_STRATEGY_MATRIX: Dict[str, Dict[str, Any]] = {
    'S0': {  # Market Crash
        'enabled': False,
        'amount_multiplier': 0.0,
        'max_amount_multiplier': 0.0,
        'interval_hours': 24,
        'tp_ratio': 5,
        'sl_ratio': -3,
        'allowed_symbols': [],
        'symbol_weights': {},
        'max_total_exposure': 0.0,
        'max_alt_exposure': 0.0,
        'description': 'Market Crash - Stop All Trading'
    },
    'S1': {  # Extreme Bearish
        'enabled': False,
        'amount_multiplier': 0.0,
        'max_amount_multiplier': 0.0,
        'interval_hours': 24,
        'tp_ratio': 5,
        'sl_ratio': -3,
        'allowed_symbols': [],
        'symbol_weights': {},
        'max_total_exposure': 0.0,
        'max_alt_exposure': 0.0,
        'description': 'Extreme Bearish - Stop Trading'
    },
    'S2': {  # Bearish
        'enabled': False,
        'amount_multiplier': 0.0,
        'max_amount_multiplier': 0.0,
        'interval_hours': 24,
        'tp_ratio': 5,
        'sl_ratio': -3,
        'allowed_symbols': [],
        'symbol_weights': {},
        'max_total_exposure': 0.0,
        'max_alt_exposure': 0.0,
        'description': 'Bearish - Stop Trading'
    },
    'S3': {  # Consolidation Low
        'enabled': True,
        'amount_multiplier': 0.5,
        'max_amount_multiplier': 0.3,
        'interval_hours': 12,
        'tp_ratio': 3,
        'sl_ratio': -7,
        'allowed_symbols': ['BTC', 'ETH'],
        'symbol_weights': {'BTC': 1.0, 'ETH': 1.0},
        'max_total_exposure': 0.4,
        'max_alt_exposure': 0.0,
        'description': 'Consolidation Low - Core only'
    },
    'S4': {  # Neutral
        'enabled': True,
        'amount_multiplier': 0.7,
        'max_amount_multiplier': 0.5,
        'interval_hours': 8,
        'tp_ratio': 5,
        'sl_ratio': -10,
        'allowed_symbols': ['BTC', 'ETH', 'SOL', 'LINK'],
        'symbol_weights': {'BTC': 1.0, 'ETH': 1.0, 'SOL': 0.7, 'LINK': 0.5},
        'max_total_exposure': 0.5,
        'max_alt_exposure': 0.1,
        'description': 'Neutral - Conservative'
    },
    'S5': {  # Early Bull
        'enabled': True,
        'amount_multiplier': 1.0,
        'max_amount_multiplier': 0.7,
        'interval_hours': 6,
        'tp_ratio': 7,
        'sl_ratio': -10,
        'allowed_symbols': ['BTC', 'ETH', 'SOL', 'LINK', 'XRP', 'ADA', 'DOGE'],
        'symbol_weights': {
            'BTC': 1.0, 'ETH': 1.0,
            'SOL': 0.8, 'LINK': 0.7,
            'XRP': 0.5, 'ADA': 0.4, 'DOGE': 0.3
        },
        'max_total_exposure': 0.7,
        'max_alt_exposure': 0.3,
        'description': 'Early Bull - Active (alts on)'
    },
    'S6': {  # Strong Bull
        'enabled': True,
        'amount_multiplier': 1.2,
        'max_amount_multiplier': 1.0,
        'interval_hours': 4,
        'tp_ratio': 10,
        'sl_ratio': -12,
        'allowed_symbols': ['BTC', 'ETH', 'SOL', 'LINK', 'XRP', 'ADA', 'DOGE'],
        'symbol_weights': {
            'BTC': 1.0, 'ETH': 1.0,
            'SOL': 0.9, 'LINK': 0.8,
            'XRP': 0.6, 'ADA': 0.5, 'DOGE': 0.3
        },
        'max_total_exposure': 0.8,
        'max_alt_exposure': 0.4,
        'description': 'Strong Bull - Max exposure'
    },
    'S7': {  # Overheated
        'enabled': True,
        'amount_multiplier': 0.8,
        'max_amount_multiplier': 0.6,
        'interval_hours': 4,
        'tp_ratio': 5,
        'sl_ratio': -8,
        'allowed_symbols': ['BTC', 'ETH', 'LINK'],
        'symbol_weights': {'BTC': 1.0, 'ETH': 1.0, 'LINK': 0.5},
        'max_total_exposure': 0.6,
        'max_alt_exposure': 0.1,
        'description': 'Overheated - Reduce risk'
    },
    'S8': {  # Distribution
        'enabled': True,
        'amount_multiplier': 0.3,
        'max_amount_multiplier': 0.3,
        'interval_hours': 8,
        'tp_ratio': 3,
        'sl_ratio': -5,
        'allowed_symbols': ['BTC', 'ETH'],
        'symbol_weights': {'BTC': 1.0, 'ETH': 0.8},
        'max_total_exposure': 0.3,
        'max_alt_exposure': 0.0,
        'description': 'Distribution - Core only'
    },
}

# ==========================================
# 2D Position Adjustment Matrix (max_mult 추가)
# ==========================================

# POSITION_ADJUSTMENT_MATRIX: Dict[Tuple[str, str], Dict[str, float]] = {
#     # S6 (Strong Bull)
#     ('S6', 'EXTREME_OVERBOUGHT'): {'amount_mult': 0.1, 'max_mult': 0.3, 'tp_mult': 0.5, 'sl_mult': 2.0},
#     ('S6', 'STRONG_RESISTANCE'): {'amount_mult': 0.3, 'max_mult': 0.5, 'tp_mult': 0.6, 'sl_mult': 1.5},
#     ('S6', 'NEAR_RESISTANCE'): {'amount_mult': 0.5, 'max_mult': 0.7, 'tp_mult': 0.8, 'sl_mult': 1.2},
#     ('S6', 'NEUTRAL'): {'amount_mult': 1.0, 'max_mult': 1.0, 'tp_mult': 1.0, 'sl_mult': 1.0},
#     ('S6', 'NEAR_SUPPORT'): {'amount_mult': 1.3, 'max_mult': 1.2, 'tp_mult': 1.2, 'sl_mult': 0.8},
#     ('S6', 'STRONG_SUPPORT'): {'amount_mult': 1.5, 'max_mult': 1.5, 'tp_mult': 1.3, 'sl_mult': 0.7},
#     ('S6', 'EXTREME_OVERSOLD'): {'amount_mult': 2.0, 'max_mult': 1.3, 'tp_mult': 1.5, 'sl_mult': 0.6},

#     # S5 (Early Bull)
#     ('S5', 'EXTREME_OVERBOUGHT'): {'amount_mult': 0.2, 'max_mult': 0.4, 'tp_mult': 0.5, 'sl_mult': 1.8},
#     ('S5', 'STRONG_RESISTANCE'): {'amount_mult': 0.4, 'max_mult': 0.6, 'tp_mult': 0.7, 'sl_mult': 1.4},
#     ('S5', 'NEAR_RESISTANCE'): {'amount_mult': 0.7, 'max_mult': 0.8, 'tp_mult': 0.9, 'sl_mult': 1.1},
#     ('S5', 'NEUTRAL'): {'amount_mult': 1.0, 'max_mult': 1.0, 'tp_mult': 1.0, 'sl_mult': 1.0},
#     ('S5', 'NEAR_SUPPORT'): {'amount_mult': 1.2, 'max_mult': 1.2, 'tp_mult': 1.1, 'sl_mult': 0.9},
#     ('S5', 'STRONG_SUPPORT'): {'amount_mult': 1.4, 'max_mult': 1.4, 'tp_mult': 1.2, 'sl_mult': 0.8},
#     ('S5', 'EXTREME_OVERSOLD'): {'amount_mult': 1.6, 'max_mult': 1.3, 'tp_mult': 1.3, 'sl_mult': 0.7},

#     # S4 (Neutral)
#     ('S4', 'EXTREME_OVERBOUGHT'): {'amount_mult': 0.0, 'max_mult': 0.2, 'tp_mult': 0.4, 'sl_mult': 2.0},
#     ('S4', 'STRONG_RESISTANCE'): {'amount_mult': 0.3, 'max_mult': 0.4, 'tp_mult': 0.6, 'sl_mult': 1.5},
#     ('S4', 'NEAR_RESISTANCE'): {'amount_mult': 0.6, 'max_mult': 0.7, 'tp_mult': 0.8, 'sl_mult': 1.2},
#     ('S4', 'NEUTRAL'): {'amount_mult': 1.0, 'max_mult': 1.0, 'tp_mult': 1.0, 'sl_mult': 1.0},
#     ('S4', 'NEAR_SUPPORT'): {'amount_mult': 1.1, 'max_mult': 1.1, 'tp_mult': 1.1, 'sl_mult': 0.9},
#     ('S4', 'STRONG_SUPPORT'): {'amount_mult': 1.2, 'max_mult': 1.3, 'tp_mult': 1.2, 'sl_mult': 0.8},
#     ('S4', 'EXTREME_OVERSOLD'): {'amount_mult': 1.3, 'max_mult': 1.2, 'tp_mult': 1.3, 'sl_mult': 0.7},

#     # S3 (Consolidation Low)
#     ('S3', 'EXTREME_OVERBOUGHT'): {'amount_mult': 0.0, 'max_mult': 0.1, 'tp_mult': 0.3, 'sl_mult': 3.0},
#     ('S3', 'STRONG_RESISTANCE'): {'amount_mult': 0.1, 'max_mult': 0.2, 'tp_mult': 0.5, 'sl_mult': 2.0},
#     ('S3', 'NEAR_RESISTANCE'): {'amount_mult': 0.3, 'max_mult': 0.4, 'tp_mult': 0.7, 'sl_mult': 1.5},
#     ('S3', 'NEUTRAL'): {'amount_mult': 0.7, 'max_mult': 1.0, 'tp_mult': 1.0, 'sl_mult': 1.0},
#     ('S3', 'NEAR_SUPPORT'): {'amount_mult': 1.0, 'max_mult': 1.2, 'tp_mult': 1.2, 'sl_mult': 0.8},
#     ('S3', 'STRONG_SUPPORT'): {'amount_mult': 1.2, 'max_mult': 1.5, 'tp_mult': 1.3, 'sl_mult': 0.7},
#     ('S3', 'EXTREME_OVERSOLD'): {'amount_mult': 1.3, 'max_mult': 1.3, 'tp_mult': 1.4, 'sl_mult': 0.6},

#     # 기본값
#     ('DEFAULT', 'DEFAULT'): {'amount_mult': 0.5, 'max_mult': 0.5, 'tp_mult': 1.0, 'sl_mult': 1.0},
# }
# ==========================================
# 2D Position Adjustment Matrix (완전판)
# ==========================================

POSITION_ADJUSTMENT_MATRIX: Dict[Tuple[str, str], Dict[str, float]] = {
    
    # ========== S0 (Market Crash) - 거래 중단 ==========
    ('S0', 'EXTREME_OVERBOUGHT'): {'amount_mult': 0.0, 'max_mult': 0.0, 'tp_mult': 0.3, 'sl_mult': 3.0},
    ('S0', 'STRONG_RESISTANCE'): {'amount_mult': 0.0, 'max_mult': 0.0, 'tp_mult': 0.3, 'sl_mult': 3.0},
    ('S0', 'NEAR_RESISTANCE'): {'amount_mult': 0.0, 'max_mult': 0.0, 'tp_mult': 0.3, 'sl_mult': 3.0},
    ('S0', 'NEUTRAL'): {'amount_mult': 0.0, 'max_mult': 0.0, 'tp_mult': 0.3, 'sl_mult': 3.0},
    ('S0', 'NEAR_SUPPORT'): {'amount_mult': 0.0, 'max_mult': 0.0, 'tp_mult': 0.3, 'sl_mult': 3.0},
    ('S0', 'STRONG_SUPPORT'): {'amount_mult': 0.0, 'max_mult': 0.0, 'tp_mult': 0.3, 'sl_mult': 3.0},
    ('S0', 'EXTREME_OVERSOLD'): {'amount_mult': 0.0, 'max_mult': 0.0, 'tp_mult': 0.3, 'sl_mult': 3.0},
    ('S0', 'BULLISH_MOMENTUM'): {'amount_mult': 0.0, 'max_mult': 0.0, 'tp_mult': 0.3, 'sl_mult': 3.0},
    ('S0', 'BEARISH_MOMENTUM'): {'amount_mult': 0.0, 'max_mult': 0.0, 'tp_mult': 0.3, 'sl_mult': 3.0},
    
    # ========== S1 (Extreme Bearish) - 거래 중단 ==========
    ('S1', 'EXTREME_OVERBOUGHT'): {'amount_mult': 0.0, 'max_mult': 0.0, 'tp_mult': 0.4, 'sl_mult': 2.5},
    ('S1', 'STRONG_RESISTANCE'): {'amount_mult': 0.0, 'max_mult': 0.0, 'tp_mult': 0.4, 'sl_mult': 2.5},
    ('S1', 'NEAR_RESISTANCE'): {'amount_mult': 0.0, 'max_mult': 0.0, 'tp_mult': 0.4, 'sl_mult': 2.5},
    ('S1', 'NEUTRAL'): {'amount_mult': 0.0, 'max_mult': 0.0, 'tp_mult': 0.5, 'sl_mult': 2.0},
    ('S1', 'NEAR_SUPPORT'): {'amount_mult': 0.0, 'max_mult': 0.0, 'tp_mult': 0.5, 'sl_mult': 2.0},
    ('S1', 'STRONG_SUPPORT'): {'amount_mult': 0.0, 'max_mult': 0.0, 'tp_mult': 0.6, 'sl_mult': 1.5},
    ('S1', 'EXTREME_OVERSOLD'): {'amount_mult': 0.0, 'max_mult': 0.0, 'tp_mult': 0.7, 'sl_mult': 1.0},
    ('S1', 'BULLISH_MOMENTUM'): {'amount_mult': 0.0, 'max_mult': 0.0, 'tp_mult': 0.5, 'sl_mult': 2.0},
    ('S1', 'BEARISH_MOMENTUM'): {'amount_mult': 0.0, 'max_mult': 0.0, 'tp_mult': 0.4, 'sl_mult': 2.5},
    
    # ========== S2 (Bearish) - 거래 중단 ==========
    ('S2', 'EXTREME_OVERBOUGHT'): {'amount_mult': 0.0, 'max_mult': 0.0, 'tp_mult': 0.4, 'sl_mult': 2.5},
    ('S2', 'STRONG_RESISTANCE'): {'amount_mult': 0.0, 'max_mult': 0.0, 'tp_mult': 0.5, 'sl_mult': 2.0},
    ('S2', 'NEAR_RESISTANCE'): {'amount_mult': 0.0, 'max_mult': 0.1, 'tp_mult': 0.6, 'sl_mult': 1.8},
    ('S2', 'NEUTRAL'): {'amount_mult': 0.0, 'max_mult': 0.2, 'tp_mult': 0.7, 'sl_mult': 1.5},
    ('S2', 'NEAR_SUPPORT'): {'amount_mult': 0.0, 'max_mult': 0.3, 'tp_mult': 0.8, 'sl_mult': 1.2},
    ('S2', 'STRONG_SUPPORT'): {'amount_mult': 0.0, 'max_mult': 0.4, 'tp_mult': 0.9, 'sl_mult': 1.0},
    ('S2', 'EXTREME_OVERSOLD'): {'amount_mult': 0.0, 'max_mult': 0.5, 'tp_mult': 1.0, 'sl_mult': 0.8},
    ('S2', 'BULLISH_MOMENTUM'): {'amount_mult': 0.0, 'max_mult': 0.3, 'tp_mult': 0.8, 'sl_mult': 1.3},
    ('S2', 'BEARISH_MOMENTUM'): {'amount_mult': 0.0, 'max_mult': 0.1, 'tp_mult': 0.5, 'sl_mult': 2.0},
    
    # ========== S3 (Consolidation Low) - 보수적 거래 ==========
    ('S3', 'EXTREME_OVERBOUGHT'): {'amount_mult': 0.0, 'max_mult': 0.1, 'tp_mult': 0.3, 'sl_mult': 3.0},
    ('S3', 'STRONG_RESISTANCE'): {'amount_mult': 0.1, 'max_mult': 0.2, 'tp_mult': 0.5, 'sl_mult': 2.0},
    ('S3', 'NEAR_RESISTANCE'): {'amount_mult': 0.3, 'max_mult': 0.4, 'tp_mult': 0.7, 'sl_mult': 1.5},
    ('S3', 'NEUTRAL'): {'amount_mult': 0.7, 'max_mult': 1.0, 'tp_mult': 1.0, 'sl_mult': 1.0},
    ('S3', 'NEAR_SUPPORT'): {'amount_mult': 1.0, 'max_mult': 1.2, 'tp_mult': 1.2, 'sl_mult': 0.8},
    ('S3', 'STRONG_SUPPORT'): {'amount_mult': 1.2, 'max_mult': 1.5, 'tp_mult': 1.3, 'sl_mult': 0.7},
    ('S3', 'EXTREME_OVERSOLD'): {'amount_mult': 1.3, 'max_mult': 1.3, 'tp_mult': 1.4, 'sl_mult': 0.6},
    ('S3', 'BULLISH_MOMENTUM'): {'amount_mult': 0.8, 'max_mult': 1.0, 'tp_mult': 1.1, 'sl_mult': 0.9},
    ('S3', 'BEARISH_MOMENTUM'): {'amount_mult': 0.5, 'max_mult': 0.7, 'tp_mult': 0.8, 'sl_mult': 1.3},
    
    # ========== S4 (Neutral) - 표준 거래 ==========
    ('S4', 'EXTREME_OVERBOUGHT'): {'amount_mult': 0.0, 'max_mult': 0.2, 'tp_mult': 0.4, 'sl_mult': 2.0},
    ('S4', 'STRONG_RESISTANCE'): {'amount_mult': 0.3, 'max_mult': 0.4, 'tp_mult': 0.6, 'sl_mult': 1.5},
    ('S4', 'NEAR_RESISTANCE'): {'amount_mult': 0.6, 'max_mult': 0.7, 'tp_mult': 0.8, 'sl_mult': 1.2},
    ('S4', 'NEUTRAL'): {'amount_mult': 1.0, 'max_mult': 1.0, 'tp_mult': 1.0, 'sl_mult': 1.0},
    ('S4', 'NEAR_SUPPORT'): {'amount_mult': 1.1, 'max_mult': 1.1, 'tp_mult': 1.1, 'sl_mult': 0.9},
    ('S4', 'STRONG_SUPPORT'): {'amount_mult': 1.2, 'max_mult': 1.3, 'tp_mult': 1.2, 'sl_mult': 0.8},
    ('S4', 'EXTREME_OVERSOLD'): {'amount_mult': 1.3, 'max_mult': 1.2, 'tp_mult': 1.3, 'sl_mult': 0.7},
    ('S4', 'BULLISH_MOMENTUM'): {'amount_mult': 1.1, 'max_mult': 1.0, 'tp_mult': 1.1, 'sl_mult': 0.9},
    ('S4', 'BEARISH_MOMENTUM'): {'amount_mult': 0.8, 'max_mult': 0.8, 'tp_mult': 0.9, 'sl_mult': 1.2},
    
    # ========== S5 (Early Bull) - 적극적 거래 ==========
    ('S5', 'EXTREME_OVERBOUGHT'): {'amount_mult': 0.2, 'max_mult': 0.4, 'tp_mult': 0.5, 'sl_mult': 1.8},
    ('S5', 'STRONG_RESISTANCE'): {'amount_mult': 0.4, 'max_mult': 0.6, 'tp_mult': 0.7, 'sl_mult': 1.4},
    ('S5', 'NEAR_RESISTANCE'): {'amount_mult': 0.7, 'max_mult': 0.8, 'tp_mult': 0.9, 'sl_mult': 1.1},
    ('S5', 'NEUTRAL'): {'amount_mult': 1.0, 'max_mult': 1.0, 'tp_mult': 1.0, 'sl_mult': 1.0},
    ('S5', 'NEAR_SUPPORT'): {'amount_mult': 1.2, 'max_mult': 1.2, 'tp_mult': 1.1, 'sl_mult': 0.9},
    ('S5', 'STRONG_SUPPORT'): {'amount_mult': 1.4, 'max_mult': 1.4, 'tp_mult': 1.2, 'sl_mult': 0.8},
    ('S5', 'EXTREME_OVERSOLD'): {'amount_mult': 1.6, 'max_mult': 1.3, 'tp_mult': 1.3, 'sl_mult': 0.7},
    ('S5', 'BULLISH_MOMENTUM'): {'amount_mult': 1.2, 'max_mult': 1.1, 'tp_mult': 1.2, 'sl_mult': 0.9},
    ('S5', 'BEARISH_MOMENTUM'): {'amount_mult': 0.6, 'max_mult': 0.7, 'tp_mult': 0.8, 'sl_mult': 1.3},
    
    # ========== S6 (Strong Bull) - 최대 공격적 ==========
    ('S6', 'EXTREME_OVERBOUGHT'): {'amount_mult': 0.1, 'max_mult': 0.3, 'tp_mult': 0.5, 'sl_mult': 2.0},
    ('S6', 'STRONG_RESISTANCE'): {'amount_mult': 0.3, 'max_mult': 0.5, 'tp_mult': 0.6, 'sl_mult': 1.5},
    ('S6', 'NEAR_RESISTANCE'): {'amount_mult': 0.5, 'max_mult': 0.7, 'tp_mult': 0.8, 'sl_mult': 1.2},
    ('S6', 'NEUTRAL'): {'amount_mult': 1.0, 'max_mult': 1.0, 'tp_mult': 1.0, 'sl_mult': 1.0},
    ('S6', 'NEAR_SUPPORT'): {'amount_mult': 1.3, 'max_mult': 1.2, 'tp_mult': 1.2, 'sl_mult': 0.8},
    ('S6', 'STRONG_SUPPORT'): {'amount_mult': 1.5, 'max_mult': 1.5, 'tp_mult': 1.3, 'sl_mult': 0.7},
    ('S6', 'EXTREME_OVERSOLD'): {'amount_mult': 2.0, 'max_mult': 1.3, 'tp_mult': 1.5, 'sl_mult': 0.6},
    ('S6', 'BULLISH_MOMENTUM'): {'amount_mult': 1.3, 'max_mult': 1.2, 'tp_mult': 1.3, 'sl_mult': 0.8},
    ('S6', 'BEARISH_MOMENTUM'): {'amount_mult': 0.4, 'max_mult': 0.6, 'tp_mult': 0.7, 'sl_mult': 1.4},
    
    # ========== S7 (Overheated) - 리스크 축소 ==========
    ('S7', 'EXTREME_OVERBOUGHT'): {'amount_mult': 0.0, 'max_mult': 0.2, 'tp_mult': 0.4, 'sl_mult': 2.5},
    ('S7', 'STRONG_RESISTANCE'): {'amount_mult': 0.2, 'max_mult': 0.3, 'tp_mult': 0.5, 'sl_mult': 2.0},
    ('S7', 'NEAR_RESISTANCE'): {'amount_mult': 0.4, 'max_mult': 0.5, 'tp_mult': 0.7, 'sl_mult': 1.5},
    ('S7', 'NEUTRAL'): {'amount_mult': 0.6, 'max_mult': 0.7, 'tp_mult': 0.9, 'sl_mult': 1.2},
    ('S7', 'NEAR_SUPPORT'): {'amount_mult': 0.8, 'max_mult': 0.9, 'tp_mult': 1.0, 'sl_mult': 1.0},
    ('S7', 'STRONG_SUPPORT'): {'amount_mult': 1.0, 'max_mult': 1.1, 'tp_mult': 1.1, 'sl_mult': 0.9},
    ('S7', 'EXTREME_OVERSOLD'): {'amount_mult': 1.2, 'max_mult': 1.0, 'tp_mult': 1.2, 'sl_mult': 0.8},
    ('S7', 'BULLISH_MOMENTUM'): {'amount_mult': 0.5, 'max_mult': 0.6, 'tp_mult': 0.8, 'sl_mult': 1.3},
    ('S7', 'BEARISH_MOMENTUM'): {'amount_mult': 0.3, 'max_mult': 0.4, 'tp_mult': 0.6, 'sl_mult': 1.8},
    
    # ========== S8 (Distribution) - 최소 활동 ==========
    ('S8', 'EXTREME_OVERBOUGHT'): {'amount_mult': 0.0, 'max_mult': 0.1, 'tp_mult': 0.3, 'sl_mult': 3.0},
    ('S8', 'STRONG_RESISTANCE'): {'amount_mult': 0.1, 'max_mult': 0.2, 'tp_mult': 0.4, 'sl_mult': 2.5},
    ('S8', 'NEAR_RESISTANCE'): {'amount_mult': 0.2, 'max_mult': 0.3, 'tp_mult': 0.5, 'sl_mult': 2.0},
    ('S8', 'NEUTRAL'): {'amount_mult': 0.3, 'max_mult': 0.4, 'tp_mult': 0.7, 'sl_mult': 1.5},
    ('S8', 'NEAR_SUPPORT'): {'amount_mult': 0.4, 'max_mult': 0.5, 'tp_mult': 0.8, 'sl_mult': 1.2},
    ('S8', 'STRONG_SUPPORT'): {'amount_mult': 0.5, 'max_mult': 0.6, 'tp_mult': 0.9, 'sl_mult': 1.0},
    ('S8', 'EXTREME_OVERSOLD'): {'amount_mult': 0.6, 'max_mult': 0.7, 'tp_mult': 1.0, 'sl_mult': 0.8},
    ('S8', 'BULLISH_MOMENTUM'): {'amount_mult': 0.4, 'max_mult': 0.5, 'tp_mult': 0.8, 'sl_mult': 1.3},
    ('S8', 'BEARISH_MOMENTUM'): {'amount_mult': 0.2, 'max_mult': 0.3, 'tp_mult': 0.5, 'sl_mult': 2.0},
    
    # ========== 기본값 ==========
    ('DEFAULT', 'DEFAULT'): {'amount_mult': 0.5, 'max_mult': 0.5, 'tp_mult': 1.0, 'sl_mult': 1.0},
}

# ==========================================
# Helpers
# ==========================================

def get_state_params(state: str) -> Dict[str, Any]:
    """현재 state에 해당하는 전략 파라미터(없으면 S4 반환)."""
    return STATE_STRATEGY_MATRIX.get(state, STATE_STRATEGY_MATRIX['S4'])

def get_adjustments(state: str, position_label: str) -> Dict[str, float]:
    """(state, position_label)에 해당하는 포지션 조정 파라미터를 반환."""
    return POSITION_ADJUSTMENT_MATRIX.get(
        (state, position_label),
        POSITION_ADJUSTMENT_MATRIX[('DEFAULT', 'DEFAULT')]
    )

def get_symbol_tier(symbol: str) -> Optional[str]:
    for tier, symbols in SYMBOL_RISK_TIERS.items():
        if symbol in symbols:
            return tier
    return None

def get_tier_multiplier(symbol: str, default: float = 0.5) -> float:
    tier = get_symbol_tier(symbol)
    return TIER_MAX_MULTIPLIERS.get(tier, default)

__all__ = [
    'SYMBOL_RISK_TIERS',
    'TIER_MAX_MULTIPLIERS',
    'STATE_STRATEGY_MATRIX',
    'POSITION_ADJUSTMENT_MATRIX',
    'get_state_params',
    'get_adjustments',
    'get_symbol_tier',
    'get_tier_multiplier',
]
