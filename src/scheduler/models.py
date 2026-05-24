from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple

@dataclass
class TickRecord:
    """記錄單一小時 (t) 系統內所有能量流向與任務狀態的快照"""
    t: int
    
    # 每台發電設備 (火力、太陽能、電池放電) 在該時間點的總供電量 (MWh)
    # 格式: {"thermal_1": 25.0, "pv_1": 5.0, "battery_1": 5.0}
    P: Dict[str, float] = field(default_factory=dict)
    
    # 每個任務 (包含充電) 從各設備獲得的電量分配 (MWh)
    # 格式: {"p1": {"thermal_1": 10.0, "pv_1": 5.0}, "battery_1_chg": {"thermal_1": 0.0}}
    k: Dict[str, Dict[str, float]] = field(default_factory=dict)
    
    # 售電量 (MWh)
    sell: float = 0.0
    
    # 儲能設備在該時間點結束後的剩餘電量 (MWh)
    # 格式: {"battery_1": 45.0}
    soc: Dict[str, float] = field(default_factory=dict)
    
    # 逾期的 Aperiodic tasks (Soft deadline miss)
    missed_aperiodic: List[str] = field(default_factory=list)
    
    # 被拒絕的 Sporadic tasks (Acceptance test 未通過)
    rejected_sporadic: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """將物件轉換為嚴格符合作業說明書附錄 G 的 JSON 格式"""
        return {
            "t": self.t,
            "P": self.P,
            "k": self.k,
            "sell": round(self.sell, 2), # 避免浮點數誤差
            "soc": self.soc,
            "missed_aperiodic": self.missed_aperiodic,
            "rejected_sporadic": self.rejected_sporadic
        }
# ==========================================
# 1. 發電與儲能設備模型 (Power System Models)
# ==========================================

@dataclass
class ThermalGenerator:
    """傳統發電機組模型"""
    id: str
    output_min: int
    output_max: int
    ramp_up_rate: int
    ramp_down_rate: int
    min_up_time: int
    min_down_time: int
    cost_fixed: int
    cost_variable: int
    initial_on_time: int
    initial_off_time: int
    initial_energy: int
    
    # 運行時動態狀態 (Runtime States)
    current_output: int = field(init=False)
    consecutive_on_time: int = field(init=False)
    consecutive_off_time: int = field(init=False)

    def __post_init__(self):
        """物件建立後，自動將歷史狀態載入運行時變數"""
        self.current_output = self.initial_energy
        self.consecutive_on_time = self.initial_on_time
        self.consecutive_off_time = self.initial_off_time

@dataclass
class RenewableGenerator:
    """再生能源機組模型"""
    id: str
    capacity: int
    forecast: List[float]  # 長度為 72 的陣列，儲存每小時預測百分比

@dataclass
class Battery:
    """儲能設備模型"""
    id: str
    soc_min: int
    soc_max: int
    discharge_max: int
    charge_max: int
    soc_init: int
    
    # 運行時動態狀態
    current_soc: int = field(init=False)

    def __post_init__(self):
        self.current_soc = self.soc_init

# ==========================================
# 2. 用電需求任務模型 (Task Models)
# ==========================================

@dataclass
class BaseTask:
    """所有任務的基礎共用模型"""
    id: str
    r: int        # release time (最早可開始時間)
    e: int        # execution time (所需執行總時間段)
    w: int        # energy demand (每小時需消耗的固定電能量)
    preempt: int  # 1 = preemptable, 0 = non-preemptable
    
    # 運行時動態狀態
    remaining_execution: int = field(init=False)
    is_completed: bool = field(default=False, init=False)

    def __post_init__(self):
        self.remaining_execution = self.e

@dataclass
class PeriodicTask(BaseTask):
    """週期性用電需求 (Hard Deadline)"""
    p: int        # period (執行週期)
    d: int        # relative deadline

@dataclass
class SporadicTask(BaseTask):
    """零星用電需求 (Hard Deadline)"""
    d: int        # relative deadline

@dataclass
class AperiodicTask(BaseTask):
    """非週期性用電需求 (Soft Deadline)"""
    # Aperiodic task 在附錄中可能不強制給定 relative deadline，
    # 若有給定則用於計算 tardiness，預設為 None。
    d: Optional[int] = None