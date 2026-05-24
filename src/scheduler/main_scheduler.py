import json
from typing import List, Dict, Any
from dataclasses import dataclass

from src.scheduler.models import SporadicTask, AperiodicTask, TickRecord, ThermalGenerator, Battery, RenewableGenerator
from src.scheduler.acceptance_tester import AcceptanceTester
from src.scheduler.power_tracer import trace_power_flows
from src.scheduler.offline_planner import get_valid_bounds

@dataclass
class ActiveJob:
    id: str
    w: int

def run_72hr_simulation(
    schedule_dict: Dict[str, List[int]],
    tester: AcceptanceTester,
    generators: List[ThermalGenerator],
    batteries: List[Battery],
    renewables: List[RenewableGenerator],
    price_72hr: List[float],
    offline_tasks: List[Any],
    online_sporadic_arrivals: Dict[int, List[SporadicTask]],
    online_aperiodic_arrivals: Dict[int, List[AperiodicTask]]
) -> List[TickRecord]:
    """
    Main 72-hour dynamic scheduling simulation loop.
    """
    trajectory = []
    
    # 建立離線任務的 w 對照表
    offline_w_map = {}
    for t_obj in offline_tasks:
        # t_obj 可能是 PeriodicTask
        offline_w_map[t_obj.id] = t_obj.w

    for t in range(72):
        price_t = price_72hr[t]
        
        # ---------------------------------------------------------
        # Step 1: 突發任務抵達與准入
        # ---------------------------------------------------------
        tester.rejected_sporadic_this_tick.clear()
        
        # 推進佇列並取得從 Queue 中丟棄的清單
        missed_aperiodic = tester.process_queue_at_tick(t)
        
        if t in online_sporadic_arrivals:
            for stask in online_sporadic_arrivals[t]:
                tester.test_sporadic(stask, current_t=t)
                
        if t in online_aperiodic_arrivals:
            for atask in online_aperiodic_arrivals[t]:
                # 如果連門都進不去 (物理不可能)，直接判定為 Soft Deadline Miss
                is_admitted = tester.test_aperiodic(atask, current_t=t)
                if not is_admitted:
                    missed_aperiodic.append(atask.id)
                
        rejected_sporadic = list(tester.rejected_sporadic_this_tick)
        
        # ---------------------------------------------------------
        # Step 2: 任務盤點與淨負載計算
        # ---------------------------------------------------------
        active_jobs = []
        
        # 收集離線排定的任務
        for job_id, hours in schedule_dict.items():
            if t in hours:
                task_id = job_id.split('_')[0] if '_' in job_id else job_id
                w = offline_w_map.get(task_id, 0)
                active_jobs.append(ActiveJob(id=job_id, w=w))
                
        # 收集線上 Accept 排定的任務
        for task_id, hours in tester.online_schedule.items():
            if t in hours:
                # 需找到 task 的 w，從抵達紀錄中尋找
                w = 0
                found = False
                for arr_t, tasks in online_sporadic_arrivals.items():
                    for st in tasks:
                        if st.id == task_id:
                            w = st.w
                            found = True
                            break
                    if found: break
                if not found:
                    for arr_t, tasks in online_aperiodic_arrivals.items():
                        for at in tasks:
                            if at.id == task_id:
                                w = at.w
                                found = True
                                break
                        if found: break
                active_jobs.append(ActiveJob(id=task_id, w=w))
                
        W_t = sum(job.w for job in active_jobs)
        
        # ---------------------------------------------------------
        # Step 3: 綠電優先與套利決策 (Arbitrage & Trade-off)
        # ---------------------------------------------------------
        P_ren = 0.0
        renewable_outputs = {}
        for r in renewables:
            out = r.capacity * r.forecast[t]
            P_ren += out
            renewable_outputs[r.id] = float(out)
            
        W_net = max(0, W_t - P_ren)
        
        # 1. 取得所有發電機的合法邊界
        gen_bounds_map = {}
        for gen in generators:
            bounds = get_valid_bounds(gen)
            if not bounds:
                raise Exception(f"Generator {gen.id} locked out at t={t}")
            gen_bounds_map[gen.id] = bounds
            
        # 2. 保命為先：建立「最低合法基載」
        gen_targets = {}
        allocated = 0
        for gen in generators:
            min_target = gen_bounds_map[gen.id][0]
            gen_targets[gen.id] = min_target
            allocated += min_target
            
        deficit = W_net - allocated
        
        # 3. 補足淨負載 (確保系統存活，邏輯與 DFS 完美對齊)
        sorted_gens = sorted(generators, key=lambda g: g.cost_variable)
        if deficit > 0:
            for gen in sorted_gens:
                bounds = gen_bounds_map[gen.id]
                current = gen_targets[gen.id]
                
                if current == 0 and len(bounds) >= 3:
                    lb, ub = bounds[1], bounds[-1]
                    increase = min(deficit, ub)
                    target = max(lb, increase)
                    gen_targets[gen.id] = target
                    deficit -= target
                elif current > 0 and len(bounds) >= 2:
                    ub = bounds[-1]
                    increase = min(deficit, ub - current)
                    gen_targets[gen.id] += increase
                    deficit -= increase
                    
                if deficit <= 0: break
                
        # 4. 高價套利 (Arbitrage) - 僅針對「已開機」的機組推升出力
        for gen in sorted_gens:
            if price_t > gen.cost_variable:
                bounds = gen_bounds_map[gen.id]
                current = gen_targets[gen.id]
                # 為了避免打亂未來的啟停計畫，我們只針對這小時必須運行的機組，直接油門踩到底賺價差
                if current > 0 and len(bounds) >= 2:
                    gen_targets[gen.id] = bounds[-1]
                    
        # 5. 電池放電外援 (終極防線)
        battery_discharges = {b.id: 0.0 for b in batteries}
        if deficit > 0:
            for bat in batteries:
                max_dis = min(bat.discharge_max, bat.current_soc - bat.soc_min)
                if max_dis > 0:
                    dis = min(deficit, max_dis)
                    battery_discharges[bat.id] = float(dis)
                    deficit -= dis
                if deficit <= 0: break
                
        if deficit > 0:
            raise Exception(f"Fatal: Cannot satisfy load at t={t}. Deficit: {deficit}")
        # ---------------------------------------------------------
        # Step 4: 狀態結算 (State Settlement)
        # ---------------------------------------------------------
        for gen in generators:
            target = gen_targets[gen.id]
            if target > 0:
                if gen.current_output == 0:
                    gen.consecutive_on_time = 1
                else:
                    gen.consecutive_on_time += 1
                gen.consecutive_off_time = 0
            else:
                if gen.current_output > 0:
                    gen.consecutive_off_time = 1
                else:
                    gen.consecutive_off_time += 1
                gen.consecutive_on_time = 0
            gen.current_output = target
            
        for bat in batteries:
            dis = battery_discharges[bat.id]
            bat.current_soc -= dis
            
        # ---------------------------------------------------------
        # Step 5: 能量流溯源 (Power Flow Tracing)
        # ---------------------------------------------------------
        # 能量流追蹤會將多餘電量算進 sell 裡面
        k_matrix, total_sell = trace_power_flows(
            active_jobs=active_jobs,
            generators=generators,
            battery_discharges=battery_discharges,
            renewable_outputs=renewable_outputs
        )
        
        # ---------------------------------------------------------
        # Step 6: 紀錄快照 (Record Snapshot)
        # ---------------------------------------------------------
        P_dict = {}
        for r_id, out in renewable_outputs.items():
            P_dict[r_id] = out
        for gen in generators:
            if gen.current_output > 0:
                P_dict[gen.id] = float(gen.current_output)
        for b_id, dis in battery_discharges.items():
            if dis > 0:
                P_dict[b_id] = dis
                
        soc_dict = {b.id: float(b.current_soc) for b in batteries}
        
        record = TickRecord(
            t=t,
            P=P_dict,
            k=k_matrix,
            sell=total_sell,
            soc=soc_dict,
            missed_aperiodic=missed_aperiodic,
            rejected_sporadic=rejected_sporadic
        )
        trajectory.append(record)
        
    return trajectory
