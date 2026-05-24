import os
import glob
import json
from typing import List

from src.scheduler.models import TickRecord
from src.scheduler.offline_planner import generate_offline_schedule
from src.scheduler.acceptance_tester import AcceptanceTester
from src.scheduler.main_scheduler import run_72hr_simulation
from src.scheduler.data_loader import (
    load_price, load_processor_settings, load_periodic_tasks, load_online_tasks
)

def save_schedule_to_json(trajectory: List[TickRecord], output_path: str):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    formatted_data = {"schedule_result": [record.to_dict() for record in trajectory]}
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(formatted_data, f, indent=4, ensure_ascii=False)
    print(f"   [✅] 報表已匯出至：{output_path}")

def main():
    print("=" * 60)
    print("虛擬電廠 (VPP) 日前與線上動態調度系統 - 批次情境壓測")
    print("=" * 60)

    # 1. 載入靜態環境參數 (價格、週期任務)
    price_72hr = load_price("input/price_72hr.json")
    periodic_tasks = load_periodic_tasks("output/task_set.json")
    
    # 為了跑 DFS，先載入一次乾淨的設備狀態
    generators, renewables, batteries = load_processor_settings("input/processor_settings.json")

    # 2. 執行第一階段：單次日前離線排程 (Offline Schedule)
    print("\n[*] 正在計算 72 小時日前固定排程 (Offline DFS)...")
    schedule_dict, offline_slack = generate_offline_schedule(periodic_tasks, generators, batteries, renewables)
    
    if not schedule_dict:
        print("[❌ 關鍵錯誤] 日前排程失敗，無法找到初始可行解。程式終止。")
        return
    print(f"[✅] 日前排程成功，已產生 {len(schedule_dict)} 個擴展 Job。")

    # 3. 抓取所有隊友準備的情境測資
    scenario_files = sorted(glob.glob("output/sporadic_aperiodic_task/scenario_*.json"))
    if not scenario_files:
        print("[⚠️ 警告] 找不到任何線上情境測資。請確認路徑 output/sporadic_aperiodic_task/ 是否正確。")
        return

    print(f"\n[*] 偵測到 {len(scenario_files)} 組線上動態任務情境，開始進行批次模擬...")

    # 4. 針對每一個情境進行線上模擬
    for filepath in scenario_files:
        scenario_name = os.path.splitext(os.path.basename(filepath))[0]
        print(f"\n   >>> 正在模擬情境：{scenario_name}")
        
        gen_sim, ren_sim, bat_sim = load_processor_settings("input/processor_settings.json")
        sporadic_arr, aperiodic_arr = load_online_tasks(filepath)
        tester = AcceptanceTester(offline_slack.copy())
        
        log_list = [] # 【新增】建立空白日誌陣列
        
        try:
            trajectory = run_72hr_simulation(
                schedule_dict=schedule_dict,
                tester=tester,
                generators=gen_sim,
                batteries=bat_sim,
                renewables=ren_sim,
                price_72hr=price_72hr,
                offline_tasks=periodic_tasks,
                online_sporadic_arrivals=sporadic_arr,
                online_aperiodic_arrivals=aperiodic_arr,
                log_list=log_list # 【新增】傳入引擎
            )
            
            # 儲存 JSON 報表
            output_file = f"output/schedule_result_{scenario_name}.json"
            save_schedule_to_json(trajectory, output_file)
            
            # 【修正】儲存符合作業規範的 JSON 格式 Acceptance Test Log
            # 注意檔名前綴需吻合作業規範
            log_file = f"output/acceptance_test_log_{scenario_name}.json"
            log_data = {
                "scenario": scenario_name,
                "acceptance_test_log": log_list if log_list else ["本情境無任何線上突發任務抵達。"]
            }
            with open(log_file, "w", encoding="utf-8") as f:
                json.dump(log_data, f, indent=4, ensure_ascii=False)
            print(f"   [✅] 准入控制日誌已匯出至：{log_file}")
            
        except Exception as e:
            print(f"   [❌] 情境 {scenario_name} 模擬崩潰！錯誤原因：{str(e)}")

    print("\n" + "=" * 60)
    print("🎉 所有情境模擬完畢，準備交接給 evaluator.py！")

if __name__ == "__main__":
    main()