#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Position Analyzer Module for Crypto Trading Bot
개별 암호화폐 자산의 상대적 위치를 분석하는 모듈
"""

import logging
from typing import Dict, List, Tuple, Optional
from collections import deque
from datetime import datetime, timedelta
import numpy as np

logger = logging.getLogger('PositionAnalyzer')

class PositionAnalyzer:
    """개별 자산의 상대적 위치를 분석하는 클래스"""
    
    def __init__(self, max_history_length: int = 600):
        """
        Args:
            max_history_length: 보관할 최대 가격 이력 길이
        """
        self.price_history = {}  # symbol별 가격 이력
        self.volume_history = {}  # symbol별 거래량 이력
        self.max_history = max_history_length
        
        self.lookback_periods = {
            'short': 24,     # 1일 (시간봉 기준)
            'medium': 168,   # 1주
            'long': 720,     # 30일
            'very_long': 4800  # 200일 (장기 트렌드)
        }
        
        # RSI 계산용 이전 값 저장
        self.rsi_gains = {}
        self.rsi_losses = {}
    
    def update_history(self, symbol: str, price: float, volume: float = 0):
        """가격/거래량 이력 업데이트
        
        Args:
            symbol: 심볼명 (예: 'BTC')
            price: 현재 가격
            volume: 거래량 (옵션)
        """
        if symbol not in self.price_history:
            self.price_history[symbol] = deque(maxlen=self.max_history)
            self.volume_history[symbol] = deque(maxlen=self.max_history)
            logger.info(f"Initialized history for {symbol}")
            
        self.price_history[symbol].append(price)
        self.volume_history[symbol].append(volume)
    
    def calculate_relative_position(self, symbol: str) -> float:
        """다중 시간대 상대 위치 계산
        
        Returns:
            0-100 스케일 값 (0=저점, 100=고점)
        """
        if symbol not in self.price_history:
            logger.warning(f"No price history for {symbol}")
            return 50.0
            
        prices = list(self.price_history[symbol])
        
        if len(prices) < self.lookback_periods['short']:
            logger.debug(f"Insufficient data for {symbol}: {len(prices)} prices")
            return 50.0
            
        positions = {}
        current_price = prices[-1]
        
        # 단기 위치
        if len(prices) >= self.lookback_periods['short']:
            recent = prices[-self.lookback_periods['short']:]
            high = max(recent)
            low = min(recent)
            if high > low:
                positions['short'] = ((current_price - low) / (high - low)) * 100
            else:
                positions['short'] = 50.0
        
        # 중기 위치
        if len(prices) >= self.lookback_periods['medium']:
            medium = prices[-self.lookback_periods['medium']:]
            high = max(medium)
            low = min(medium)
            if high > low:
                positions['medium'] = ((current_price - low) / (high - low)) * 100
            else:
                positions['medium'] = 50.0
        
        # 장기 위치
        if len(prices) >= self.lookback_periods['long']:
            long_term = prices[-self.lookback_periods['long']:]
            high = max(long_term)
            low = min(long_term)
            if high > low:
                positions['long'] = ((current_price - low) / (high - low)) * 100
            else:
                positions['long'] = 50.0
        
        # 가중평균 계산 (장기 비중 높게)
        if 'long' in positions:
            weighted = (positions.get('short', 50) * 0.2 + 
                       positions.get('medium', 50) * 0.3 + 
                       positions['long'] * 0.5)
        elif 'medium' in positions:
            weighted = (positions.get('short', 50) * 0.4 + 
                       positions['medium'] * 0.6)
        else:
            weighted = positions.get('short', 50.0)
            
        return min(100.0, max(0.0, weighted))
    
    def calculate_rsi(self, symbol: str, period: int = 14) -> float:
        """RSI (Relative Strength Index) 계산
        
        Args:
            symbol: 심볼명
            period: RSI 계산 기간 (기본 14)
            
        Returns:
            RSI 값 (0-100)
        """
        if symbol not in self.price_history:
            return 50.0
            
        prices = list(self.price_history[symbol])
        
        if len(prices) < period + 1:
            return 50.0
        
        # 가격 변화 계산
        price_changes = [prices[i] - prices[i-1] for i in range(1, len(prices))]
        recent_changes = price_changes[-period:]
        
        # 평균 상승/하락 계산
        gains = [change if change > 0 else 0 for change in recent_changes]
        losses = [-change if change < 0 else 0 for change in recent_changes]
        
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0
            
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def calculate_bollinger_position(self, symbol: str, period: int = 20, std_dev: float = 2.0) -> float:
        """볼린저 밴드 내 위치 계산
        
        Returns:
            0-100 (0=하단밴드, 50=중간, 100=상단밴드)
        """
        if symbol not in self.price_history or len(self.price_history[symbol]) < period:
            return 50.0
            
        prices = list(self.price_history[symbol])[-period:]
        current_price = prices[-1]
        
        mean = sum(prices) / len(prices)
        variance = sum((x - mean) ** 2 for x in prices) / len(prices)
        std = variance ** 0.5
        
        upper_band = mean + (std * std_dev)
        lower_band = mean - (std * std_dev)
        
        if upper_band > lower_band:
            position = ((current_price - lower_band) / (upper_band - lower_band)) * 100
            return min(100.0, max(0.0, position))
        else:
            return 50.0
    
    def find_support_resistance(self, symbol: str, lookback: int = 100) -> Tuple[float, float]:
        """지지/저항 레벨 찾기
        
        Returns:
            (support, resistance) 튜플
        """
        if symbol not in self.price_history:
            return 0, float('inf')
            
        prices = list(self.price_history[symbol])
        
        if len(prices) < 20:
            return 0, float('inf')
            
        # 최근 lookback 기간의 가격만 사용
        prices = prices[-min(lookback, len(prices)):]
        current_price = prices[-1]
        
        # 스윙 하이/로우 찾기
        highs = []
        lows = []
        
        for i in range(2, len(prices)-2):
            # 스윙 하이: 주변보다 높은 지점
            if (prices[i] > prices[i-1] and prices[i] > prices[i-2] and
                prices[i] > prices[i+1] and prices[i] > prices[i+2]):
                highs.append(prices[i])
                
            # 스윙 로우: 주변보다 낮은 지점
            if (prices[i] < prices[i-1] and prices[i] < prices[i-2] and
                prices[i] < prices[i+1] and prices[i] < prices[i+2]):
                lows.append(prices[i])
        
        # 현재 가격 기준 가장 가까운 지지/저항 찾기
        support_levels = [l for l in lows if l < current_price]
        resistance_levels = [h for h in highs if h > current_price]
        
        support = max(support_levels) if support_levels else 0
        resistance = min(resistance_levels) if resistance_levels else float('inf')
        
        return support, resistance
    
    def calculate_volume_profile_position(self, symbol: str) -> float:
        """거래량 프로파일 기반 위치 계산
        
        Returns:
            0-100 (높은 거래량 구간 대비 현재 위치)
        """
        if symbol not in self.price_history or symbol not in self.volume_history:
            return 50.0
            
        prices = list(self.price_history[symbol])
        volumes = list(self.volume_history[symbol])
        
        if len(prices) < 50:
            return 50.0
            
        # 가격 구간별 거래량 집계
        price_bins = 20
        min_price = min(prices)
        max_price = max(prices)
        
        if max_price <= min_price:
            return 50.0
            
        bin_size = (max_price - min_price) / price_bins
        volume_profile = [0] * price_bins
        
        for price, volume in zip(prices, volumes):
            bin_index = min(int((price - min_price) / bin_size), price_bins - 1)
            volume_profile[bin_index] += volume
        
        # 최대 거래량 구간 찾기
        max_volume_index = volume_profile.index(max(volume_profile))
        high_volume_price = min_price + (max_volume_index + 0.5) * bin_size
        
        # 현재 가격의 상대적 위치
        current_price = prices[-1]
        
        if current_price > high_volume_price:
            # 고거래량 구간 위
            position = 50 + min(50, ((current_price - high_volume_price) / high_volume_price) * 100)
        else:
            # 고거래량 구간 아래
            position = max(0, 50 - ((high_volume_price - current_price) / high_volume_price) * 100)
            
        return position
    
    def calculate_momentum(self, symbol: str, periods: List[int] = None) -> float:
        """모멘텀 점수 계산
        
        Args:
            periods: 모멘텀 계산 기간 리스트 (기본: [7, 14, 30])
            
        Returns:
            -100 to 100 (음수=하락모멘텀, 양수=상승모멘텀)
        """
        if periods is None:
            periods = [7, 14, 30]
            
        if symbol not in self.price_history:
            return 0.0
            
        prices = list(self.price_history[symbol])
        momentum_scores = []
        
        for period in periods:
            if len(prices) >= period:
                change = ((prices[-1] / prices[-period]) - 1) * 100
                momentum_scores.append(change)
        
        if not momentum_scores:
            return 0.0
            
        # 가중평균 (최근 기간 높은 가중치)
        weights = [0.5, 0.3, 0.2][:len(momentum_scores)]
        weighted_sum = sum(s * w for s, w in zip(momentum_scores, weights))
        total_weight = sum(weights)
        
        return weighted_sum / total_weight if total_weight > 0 else 0.0
    
    def get_position_score(self, symbol: str) -> Dict:
        """종합 위치 점수 계산
        
        Returns:
            위치 분석 결과 딕셔너리
        """
        score = {
            'symbol': symbol,
            'relative_position': self.calculate_relative_position(symbol),
            'rsi': self.calculate_rsi(symbol),
            'bollinger_position': self.calculate_bollinger_position(symbol),
            'volume_profile_position': self.calculate_volume_profile_position(symbol),
            'momentum': self.calculate_momentum(symbol),
            'support': 0,
            'resistance': 0,
            'position_label': 'NEUTRAL',
            'combined_score': 50.0,
            'timestamp': datetime.now()
        }
        
        # 지지/저항 레벨
        support, resistance = self.find_support_resistance(symbol)
        score['support'] = support
        score['resistance'] = resistance
        
        # 지지/저항 대비 위치
        if support > 0 and resistance < float('inf') and resistance > support:
            current_price = self.price_history[symbol][-1] if symbol in self.price_history else 0
            sr_position = ((current_price - support) / (resistance - support)) * 100
            sr_position = min(100, max(0, sr_position))
        else:
            sr_position = 50
        
        # 종합 점수 계산 (각 지표별 가중치)
        weights = {
            'relative_position': 0.30,
            'rsi': 0.20,
            'bollinger_position': 0.15,
            'sr_position': 0.20,
            'volume_profile_position': 0.15
        }
        
        combined_score = (
            score['relative_position'] * weights['relative_position'] +
            score['rsi'] * weights['rsi'] +
            score['bollinger_position'] * weights['bollinger_position'] +
            sr_position * weights['sr_position'] +
            score['volume_profile_position'] * weights['volume_profile_position']
        )
        
        score['combined_score'] = combined_score
        
        # 포지션 라벨 결정
        if combined_score >= 85:
            score['position_label'] = 'EXTREME_OVERBOUGHT'
        elif combined_score >= 70:
            score['position_label'] = 'STRONG_RESISTANCE'
        elif combined_score >= 60:
            score['position_label'] = 'NEAR_RESISTANCE'
        elif combined_score <= 15:
            score['position_label'] = 'EXTREME_OVERSOLD'
        elif combined_score <= 30:
            score['position_label'] = 'STRONG_SUPPORT'
        elif combined_score <= 40:
            score['position_label'] = 'NEAR_SUPPORT'
        else:
            score['position_label'] = 'NEUTRAL'
        
        # 모멘텀 기반 조정
        if abs(score['momentum']) > 20:
            if score['momentum'] > 0 and score['position_label'] == 'NEUTRAL':
                score['position_label'] = 'BULLISH_MOMENTUM'
            elif score['momentum'] < 0 and score['position_label'] == 'NEUTRAL':
                score['position_label'] = 'BEARISH_MOMENTUM'
        
        logger.debug(f"{symbol} Position Analysis: {score['position_label']} "
                    f"(Score: {combined_score:.1f}, RSI: {score['rsi']:.1f})")
        
        return score
    
    def get_all_positions(self) -> Dict[str, Dict]:
        """모든 심볼의 위치 분석 결과 반환"""
        results = {}
        for symbol in self.price_history.keys():
            results[symbol] = self.get_position_score(symbol)
        return results