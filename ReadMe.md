## 4개 지표 통합 시스템 최종 설명

### 1. **계층적 의사결정 구조**

```
1단계: S0~S8 State (기본 골격)
   ↓
2단계: market_condition/sentiment (시장 온도)
   ↓
3단계: overall_signal (AI 검증)
   ↓
4단계: MarketRegime 결정 (통합 국면)
   ↓
5단계: PositionAnalyzer (개별 미세조정)
```

### 2. **각 지표의 역할**

#### **S0~S8 State (구조적 분석)**
- **역할**: 시장의 기본 구조/사이클 위치
- **데이터 소스**: `coin_ta_market_states` 테이블
- **활용**: MarketRegime 결정의 기본 뼈대

#### **market_condition (센티먼트)**
- **역할**: 시장 심리/과열도 측정
- **데이터 소스**: 
  - AI 테이블의 `market_condition` 컬럼 (있으면)
  - 없으면 RSI + Breadth로 추정
- **분류**: EXTREME_FEAR, FEAR, NEUTRAL, GREED, EXTREME_GREED
- **활용**: State와 결합하여 전환점 감지

#### **overall_signal (AI 검증)**
- **역할**: AI의 종합적 매수/매도 판단
- **데이터 소스**: `ai_comprehensive_analysis` 테이블
- **분류**: STRONG_BUY, BUY, NEUTRAL, SELL, STRONG_SELL
- **활용**: State+Sentiment 조합의 검증 및 보정

#### **PositionAnalyzer (개별 자산)**
- **역할**: 각 코인별 기술적 위치 분석
- **데이터**: 가격 히스토리, RSI, 지지/저항선
- **출력**: position_label (OVERSOLD ~ OVERBOUGHT)
- **활용**: 최종 amount/TP/SL 미세조정

### 3. **실제 작동 흐름**

```python
# Step 1: 3개 지표로 MarketRegime 결정
state = "S4"           # DB에서
sentiment = "NEUTRAL"  # RSI/Breadth 또는 AI에서
ai_signal = "BUY"      # AI 분석에서

# Step 2: MarketRegime 결정 (market_state.py)
if (state="S4", sentiment="NEUTRAL", ai_signal="BUY"):
    regime = MarketRegime.EARLY_RECOVERY
    
# Step 3: Regime에 따른 기본 파라미터
params = {
    'amount_multiplier': 1.5,      # 표준보다 50% 증가
    'max_amount_multiplier': 1.2,
    'tp_ratio': 12,
    'sl_ratio': -8,
    'allowed_symbols': ['BTC','ETH','SOL','LINK','XRP','ADA']
}

# Step 4: 개별 자산 조정 (adaptive_trading_bot.py)
BTC_position = "NEAR_SUPPORT"  # PositionAnalyzer 결과

# 2차원 매트릭스에서 조정값
adjustment = POSITION_ADJUSTMENT_MATRIX[('S4', 'NEAR_SUPPORT')]
# = {'amount_mult': 1.1, 'tp_mult': 1.1, ...}

# 최종 BTC 파라미터
BTC_amount = base * 1.5 * 1.1 = base * 1.65
```

### 4. **통합 예시 시나리오**

**시나리오 1: 바닥 매수 기회**
- State: S2 (Bearish)
- Sentiment: EXTREME_FEAR (RSI 18, Breadth 15%)
- AI Signal: BUY
- **→ Regime: ACCUMULATION (바닥 누적)**
- BTC Position: EXTREME_OVERSOLD
- **결과**: 공격적 매수 (2.5x × 1.3 = 3.25x)

**시나리오 2: 고점 경고**
- State: S7 (Overheated)
- Sentiment: EXTREME_GREED (RSI 82, Breadth 85%)
- AI Signal: SELL
- **→ Regime: DISTRIBUTION (분배)**
- SOL Position: EXTREME_OVERBOUGHT
- **결과**: 매수 중단, 기존 포지션 감축

### 5. **우선순위와 안전장치**

1. **State가 S0~S2**: AI가 BUY여도 매수 금지
2. **Sentiment EXTREME_FEAR + AI STRONG_BUY**: 강력 매수 신호
3. **Position EXTREME_OVERBOUGHT**: 개별 자산 amount 90% 감축
4. **AI 데이터 없음**: NEUTRAL로 처리, State 중심 운영

이렇게 4개 지표가 서로를 검증하고 보완하며 최종 트레이딩 결정을 만들어냅니다.





## 조정 프로세스

### 1단계: MarketRegime으로 기본 파라미터 설정
```python
# MarketRegime에 따른 기본값 (market_state.py)
MarketRegime.EARLY_RECOVERY → {
    'amount_multiplier': 1.5,       # 기본 배수
    'max_amount_multiplier': 1.2,
    'tp_ratio': 12,
    'sl_ratio': -8
}
```

### 2단계: POSITION_ADJUSTMENT_MATRIX로 추가 조정
```python
# 기존 POSITION_ADJUSTMENT_MATRIX 사용 (policies.py)
('S4', 'NEAR_SUPPORT'): {
    'amount_mult': 1.1,    # 추가 배수
    'max_mult': 1.1,
    'tp_mult': 1.1,
    'sl_mult': 0.9
}
```

### 실제 계산 과정 (AdaptiveSymbolInfo.apply_market_state_and_position)

```python
# 1. MarketRegime 기반 조정
base_amount = self.base_open_amount * 1.5  # Regime의 amount_multiplier
base_max = self.base_max_amount * 1.2      # Regime의 max_amount_multiplier
base_tp = 12                               # Regime의 tp_ratio
base_sl = -8                               # Regime의 sl_ratio

# 2. POSITION_ADJUSTMENT_MATRIX 추가 조정
self.open_amount = base_amount * 1.1       # Position의 amount_mult
self.max_amount = base_max * 1.1           # Position의 max_mult  
self.take_profit_ratio = base_tp * 1.1     # = 13.2%
self.stop_loss_ratio = base_sl * 0.9       # = -7.2%
```

## 왜 2단계로 나눴나?

1. **MarketRegime**: 전체 시장 국면에 대한 **매크로 조정**
   - 시장 전체가 ACCUMULATION이면 모든 자산 매수 증가
   - 시장 전체가 DISTRIBUTION이면 모든 자산 매수 감소

2. **POSITION_ADJUSTMENT_MATRIX**: 개별 자산의 **마이크로 조정**
   - BTC는 지지선 근처 → 추가 10% 증액
   - SOL은 저항선 근처 → 30% 감액
   - 같은 시장에서도 자산별 위치가 다름

## 예시: 실제 상황

**현재 상황:**
- MarketRegime: EARLY_RECOVERY (1.5x)
- BTC: NEAR_SUPPORT (1.1x)
- SOL: STRONG_RESISTANCE (0.4x)

**결과:**
- BTC: 100 × 1.5 × 1.1 = **165 USDT**
- SOL: 100 × 1.5 × 0.4 = **60 USDT**

같은 시장 국면에서도 BTC는 165% 매수, SOL은 60% 매수로 차별화됩니다.

## 기존 매트릭스 유지 이유

POSITION_ADJUSTMENT_MATRIX는 이미 잘 설계되어 있어서 그대로 사용합니다. State(S0~S8)와 Position Label의 조합은 여전히 유효하기 때문입니다. 다만 이제 State는 MarketRegime 결정에 사용되고, 최종 미세조정에만 다시 활용됩니다.