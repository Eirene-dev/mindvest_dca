# Database
import os
import logging
import time
import psycopg2
from psycopg2.extras import RealDictCursor
import sqlalchemy
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

logger = logging.getLogger('DatabaseManager')

# ==========================================
# Configuration
# ==========================================
load_dotenv()

# PostgreSQL Connection
DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://user:password@localhost:5432/trading_db')

# postgres:// -> postgresql:// 자동 변환
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

print(f"Using database URL: {DATABASE_URL[:30]}...")

# ==========================================
# Database Manager
# ==========================================

class DatabaseManager:
    """PostgreSQL 데이터베이스 관리자"""
    
    def __init__(self):
        self.connection = None
        self.engine = None
        try:
            self.engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=10, max_overflow=20)
            self.reconnect()
        except Exception as e:
            logger.error(f"Database initialization failed: {e}")
            self.connection = None
    
    def reconnect(self):
        """데이터베이스 재연결"""
        try:
            if self.connection:
                try:
                    self.connection.close()
                except:
                    pass
            
            self.connection = psycopg2.connect(DATABASE_URL)
            self.connection.autocommit = True
            logger.info("Database connected successfully")
            return True
        except Exception as e:
            logger.error(f"Database connection failed: {e}")
            self.connection = None
            return False
    
    def get_latest_market_state(self):
        """최신 시장 상태 가져오기"""
        for attempt in range(3):
            try:
                if not self.connection or self.connection.closed:
                    if not self.reconnect():
                        time.sleep(1)
                        continue
                
                with self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
                    query = """
                        SELECT state, confidence, btc_price, breadth_above50, 
                               rsi_btc, funding_pct_btc, timestamp
                        FROM coin_ta_market_states 
                        ORDER BY timestamp DESC 
                        LIMIT 1
                    """
                    cursor.execute(query)
                    result = cursor.fetchone()
                    
                    if result:
                        logger.info(f"Latest market state: {result['state']} (confidence: {result.get('confidence', 0.5):.2f})")
                        return result
                    else:
                        logger.warning("No market state found in database")
                        break
                        
            except Exception as e:
                logger.error(f"Error fetching market state (attempt {attempt+1}): {e}")
                if attempt < 2:
                    time.sleep(1)
                    self.reconnect()
        
        logger.warning("Using default market state S4")
        return {'state': 'S4', 'confidence': 0.5, 'btc_price': 0}
    
    def get_latest_ai_analysis(self):
        """최신 AI 종합 분석 데이터 가져오기"""
        for attempt in range(3):
            try:
                if not self.connection or self.connection.closed:
                    if not self.reconnect():
                        time.sleep(1)
                        continue
                
                with self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
                    query = """
                        SELECT overall_signal, market_condition, created_at
                        FROM ai_comprehensive_analysis 
                        ORDER BY created_at DESC 
                        LIMIT 1
                    """
                    cursor.execute(query)
                    result = cursor.fetchone()
                    
                    if result:
                        logger.info(f"Latest AI analysis - Signal: {result['overall_signal']}, Condition: {result['market_condition']}")
                        return result
                    else:
                        logger.warning("No AI analysis data found in database")
                        break
                        
            except Exception as e:
                logger.error(f"Error fetching AI analysis data (attempt {attempt+1}): {e}")
                if attempt < 2:
                    time.sleep(1)
                    self.reconnect()
        
        logger.warning("Returning None for AI analysis data")
        return None
    
    def get_state_history(self, hours=24):
        """시장 상태 히스토리 가져오기"""
        try:
            if not self.connection or self.connection.closed:
                self.reconnect()
            
            if self.connection:
                with self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
                    query = """
                        SELECT state, timestamp, btc_price, confidence
                        FROM coin_ta_market_states 
                        WHERE timestamp >= NOW() - INTERVAL '%s hours'
                        ORDER BY timestamp DESC
                    """
                    cursor.execute(query, (hours,))
                    return cursor.fetchall()
        except Exception as e:
            logger.error(f"Error fetching state history: {e}")
            self.reconnect()
        
        return []