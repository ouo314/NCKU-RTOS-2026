## 檔案結構
```
NCKU-RTOS-2026/
├── README.md
├── report.pdf
├── src/
│ ├── task_generator.*
│ ├── scheduler.*
│ ├── evaluator.*
│ └── advanced_scheduler.* # Only Level 2
├── input/
│ ├── processor_settings.json
│ └── price_72hr.json
├── output/
│ ├── sporadic_aperiodic_task/
│ ├── task_set.json
│ ├── schedule_result.json
│ ├── evaluation_results.json
│ └── acceptance_test_log.json
└── runtime_config.* or crontab.txt # Only Level 2

```

## 📦 Periodic Task Set 生成策略與額外設計說明

本模組在滿足作業基礎規範外，額外引入以下工程設計，以確保產出的 Task Set 兼具「數學可排程性」與 `Demo` 穩定性。組員與評分時可參考此邏輯理解輸入資料特性：

| 額外設計 / 限制 | 設計動機與對排程器的影響 |
|:---|:---|
| **Non-preemptive 綁定於 `e=2` 而非 `e=3`** | 原規劃將最長執行時間設為不可中斷，但 `e=3, d=3` 會產生零鬆弛的連續區塊，極易造成排程碎片化與 MILP 求解震盪。改為 `e=2` 可在滿足 `e≠1` 規範的前提下，顯著降低連續時段分配難度，提升 Scheduler 收斂速度與 Acceptance Test 的插入成功率。 |
| **固定比例 `p=6` 短週期任務** | 用於精確錨定 Workload Density (DW) 於 `0.75~0.95` 區間。避免 DW 過低導致排程無挑戰性，或 DW > 1.0 造成理論過載。短週期提供穩定的基底負載，便於計算跨 Frame 的 Slack 餘裕，並降低儲能 SOC 劇烈波動的風險。 |
| **混合週期設計 (`6`, `11/12`, `15~24`) 與防禦性 Deadline 下限** | 未強制所有 period 為 3 的倍數，保留 `11/12` 以貼近真實非對齊週期情境。為確保 `f=3` 的 Frame 可視性 (`2f−gcd(f,p)≤d`)，對前 `N-2` 個 task 自動設定 `d≥6` 下限。此設計在數學上恆滿足限制式（例：`p=11` 時 `2×3−gcd(3,11)=5 ≤ 6`），避免寫死參數，同時測試排程器處理非對齊釋放點的能力。 |
| **Deadline 分層策略：前段寬鬆(≥6) / 末段緊迫(d=3)** | 末段固定 `d=3` 是為了穩定滿足 1-6 (`≥20% d=e`) 與 `f=3` 的邊界條件；前段保留寬鬆 Deadline 則提供排程器進行能量平移（儲能充放）與機組 Ramp 調整的彈性空間，避免所有 Job 競爭同一時間窗，利於優化 `f2` (發電成本) 與 `f3` (售電收益)。 |
| **確定性種子 + 自動回退驗證機制** | 固定 `RANDOM_SEED=2026` 並內建 `validate()` 斷言。若隨機組合觸發 DW 超標、Job 展開數不足或 Frame 可視性失敗，將自動重抽。確保每次執行輸出皆為「合法且可排程」的確定性輸入，方便組員除錯、CI 驗證與 Demo 重現。 |

