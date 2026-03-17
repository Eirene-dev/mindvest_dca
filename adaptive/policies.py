#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Trading policy - 3-Mode System (STOP / NORMAL / CAUTIOUS)

분류기(S0-S8)의 결과를 3가지 트레이딩 모드로 변환합니다.
- STOP:     하락장 → 매수 중단
- NORMAL:   정상 → YAML 설정 그대로
- CAUTIOUS: 과열 → 매수 축소, BTC/ETH만
"""

from typing import Dict, Any, List
from enum import Enum

# ==========================================
# 3-Mode 정의
# ==========================================

class TradingMode(Enum):
    STOP = "STOP"
    NORMAL = "NORMAL"
    CAUTIOUS = "CAUTIOUS"


# State → Mode 매핑 (분류기 기준)
# S0: 쇼크/패닉, S1: 데드캣 반등, S2: 하락 추세
# S3: 상단 분배, S4: 횡보, S5: 상승 내 조정
# S6: 조용한 상승, S7: 강한 상승, S8: 유포리아
STATE_TO_MODE: Dict[str, TradingMode] = {
    'S0': TradingMode.STOP,
    'S1': TradingMode.STOP,
    'S2': TradingMode.STOP,
    'S3': TradingMode.NORMAL,
    'S4': TradingMode.NORMAL,
    'S5': TradingMode.NORMAL,
    'S6': TradingMode.NORMAL,
    'S7': TradingMode.NORMAL,
    'S8': TradingMode.CAUTIOUS,
}

# 모드별 파라미터
MODE_PARAMS: Dict[TradingMode, Dict[str, Any]] = {
    TradingMode.STOP: {
        'amount_multiplier': 0.0,
        'interval_hours': 24,
        'allowed_tickers': [],        # 빈 리스트 = 전부 차단
        'description': '하락장 - 매수 중단',
    },
    TradingMode.NORMAL: {
        'amount_multiplier': 1.0,
        'interval_hours': 8,
        'allowed_tickers': None,      # None = YAML 설정 전체 허용
        'description': '정상 - YAML 설정대로 매수',
    },
    TradingMode.CAUTIOUS: {
        'amount_multiplier': 0.3,
        'interval_hours': 12,
        'allowed_tickers': ['BTC', 'ETH'],
        'description': '과열 - BTC/ETH만 축소 매수',
    },
}

# 심볼 리스크 티어 (포지션 전환 시 청산 순서용)
SYMBOL_RISK_TIERS: Dict[str, List[str]] = {
    'TIER_1': ['BTC', 'ETH', 'SOL'],
    'TIER_2': ['ADA', 'LINK'],
    'TIER_3': ['XRP', 'DOGE'],
}


# ==========================================
# Helper 함수
# ==========================================

def get_mode(state: str) -> TradingMode:
    """State(S0-S8) → TradingMode 변환"""
    return STATE_TO_MODE.get(state, TradingMode.STOP)


def get_mode_params(state: str) -> Dict[str, Any]:
    """State에 해당하는 모드 파라미터 반환"""
    mode = get_mode(state)
    return MODE_PARAMS[mode]


def is_symbol_allowed(state: str, symbol: str) -> bool:
    """현재 상태에서 해당 심볼 거래 허용 여부"""
    mode = get_mode(state)
    allowed = MODE_PARAMS[mode]['allowed_tickers']
    if allowed is None:
        return True           # NORMAL 모드: 전부 허용
    return symbol in allowed  # STOP/CAUTIOUS: 목록 체크


__all__ = [
    'TradingMode',
    'STATE_TO_MODE',
    'MODE_PARAMS',
    'SYMBOL_RISK_TIERS',
    'get_mode',
    'get_mode_params',
    'is_symbol_allowed',
]