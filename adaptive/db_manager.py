import os
import logging
from contextlib import contextmanager
from sqlalchemy import create_engine, text, pool
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

logger = logging.getLogger('DatabaseManager')
load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://user:password@localhost:5432/trading_db')
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

class DatabaseManager:
    """PostgreSQL 데이터베이스 관리자 - Connection Pool 방식"""
    
    _engine = None
    _SessionLocal = None
    
    def __init__(self):
        """Singleton pattern으로 engine 생성"""
        if DatabaseManager._engine is None:
            try:
                DatabaseManager._engine = create_engine(
                    DATABASE_URL,
                    poolclass=pool.QueuePool,
                    pool_size=5,
                    max_overflow=10,
                    pool_timeout=30,
                    pool_recycle=3600,
                    pool_pre_ping=True,
                    connect_args={
                        'connect_timeout': 10,
                        'keepalives': 1,
                        'keepalives_idle': 30,
                        'keepalives_interval': 10,
                        'keepalives_count': 5,
                    }
                )
                
                DatabaseManager._SessionLocal = sessionmaker(
                    autocommit=False,
                    autoflush=False,
                    bind=DatabaseManager._engine
                )
                
                # 연결 테스트
                with DatabaseManager._engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                
                logger.info("Database connection pool initialized successfully")
            except Exception as e:
                logger.error(f"Database initialization failed: {e}")
                DatabaseManager._engine = None
    
    @property
    def connection(self):
        """하위 호환성을 위한 속성 - engine이 있으면 True 반환"""
        return self._engine is not None
    
    @property
    def engine(self):
        """Engine 직접 접근"""
        return self._engine
    
    def is_connected(self):
        """데이터베이스 연결 상태 확인"""
        if self._engine is None:
            return False
        
        try:
            with self._engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.error(f"Connection check failed: {e}")
            return False
    
    @contextmanager
    def get_connection(self):
        """Context manager로 안전한 연결 관리"""
        if self._engine is None:
            logger.error("Database engine not initialized")
            yield None
            return
        
        conn = None
        try:
            conn = self._engine.connect()
            yield conn
        except Exception as e:
            logger.error(f"Database connection error: {e}")
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                conn.close()  # Pool로 반환
    
    def get_latest_market_state(self):
        """최신 시장 상태 가져오기 - 개선 버전"""
        try:
            with self.get_connection() as conn:
                if conn is None:
                    logger.warning("No database connection, using default state")
                    return {'state': 'S4', 'confidence': 0.5, 'btc_price': 0}
                
                query = text("""
                    SELECT state, confidence, btc_price, breadth_above50, 
                           rsi_btc, funding_pct_btc, timestamp
                    FROM coin_ta_market_states 
                    ORDER BY timestamp DESC 
                    LIMIT 1
                """)
                
                result = conn.execute(query).fetchone()
                
                if result:
                    # SQLAlchemy 2.0+ Row를 dict로 변환
                    result_dict = result._asdict()
                    logger.info(f"Latest market state: {result_dict['state']} "
                              f"(confidence: {result_dict.get('confidence', 0.5):.2f})")
                    return result_dict
                else:
                    logger.warning("No market state found in database")
                    return {'state': 'S4', 'confidence': 0.5, 'btc_price': 0}
                    
        except Exception as e:
            logger.error(f"Error fetching market state: {e}")
            return {'state': 'S4', 'confidence': 0.5, 'btc_price': 0}
    
    def get_latest_ai_analysis(self):
        """최신 AI 종합 분석 데이터 가져오기 - 개선 버전"""
        try:
            with self.get_connection() as conn:
                if conn is None:
                    return None
                
                query = text("""
                    SELECT overall_signal, market_condition, created_at
                    FROM ai_comprehensive_analysis 
                    ORDER BY created_at DESC 
                    LIMIT 1
                """)
                
                result = conn.execute(query).fetchone()
                
                if result:
                    result_dict = result._asdict()
                    logger.info(f"Latest AI analysis - Signal: {result_dict['overall_signal']}, "
                              f"Condition: {result_dict['market_condition']}")
                    return result_dict
                else:
                    logger.warning("No AI analysis data found")
                    return None
                    
        except Exception as e:
            logger.error(f"Error fetching AI analysis: {e}")
            return None
    
    def get_state_history(self, hours=24):
        """시장 상태 히스토리 가져오기 - 개선 버전"""
        try:
            with self.get_connection() as conn:
                if conn is None:
                    return []
                
                query = text("""
                    SELECT state, timestamp, btc_price, confidence
                    FROM coin_ta_market_states 
                    WHERE timestamp >= NOW() - INTERVAL :hours
                    ORDER BY timestamp DESC
                """)
                
                result = conn.execute(query, {"hours": f"{hours} hours"}).fetchall()
                return [row._asdict() for row in result]
                
        except Exception as e:
            logger.error(f"Error fetching state history: {e}")
            return []
    
    def close(self):
        """애플리케이션 종료 시 pool 정리"""
        if self._engine:
            self._engine.dispose()
            logger.info("Database connection pool closed")
