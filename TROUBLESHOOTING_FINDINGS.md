# Troubleshooting Report: Unexpected Claude Credit Exhaustion

## Incident Summary
Between May 31, 2026, 11:00 PM IST and June 1, 2026, 8:00 AM IST, the Claude Soma deployment exhausted approximately **20% of the daily Claude quota** plus **$200 USD in usage credits**. 

The investigation identified two primary vectors for this exhaustion:
1. **Autonomous High-Context Leads**: Two project leads remained active and "autonomous" overnight with extremely large context windows (up to 844k tokens).
2. **API-Level Claude -p Loop (Thunder Herd)**: A race condition in the routines API endpoint caused a "spiral of death" where multiple concurrent requests spawned multiple CPU-intensive Claude sessions.

---

## Detailed Findings

### 1. Autonomous Leads with Massive Context
The most significant drain on usage credits came from autonomous turns taken by project leads with bloated context windows.

*   **`soma-improver`**: Recorded with a **844k token context** window. 
    *   **Cost per turn**: Using Claude Opus at $15/MT (input), a single turn with this context costs **~$12.66**.
    *   **Activity**: The lead was marked as "autonomous" overnight. Only **16 turns** in this state would exhaust $200.
*   **`wan-manager`**: Recorded with a **210k token context**.
    *   **Activity**: Stayed in a "working" state for over **1 day and 5 hours**. It appeared to be stuck in a loop reporting the same checkpoint (`HF slot + ZeroGPU quota math`).
*   **Main Bot (`hermes`)**: Reached a **269.4k token context**.
    *   **Cost per turn**: ~$4.04.

**Root Cause**: Sessions were left with massive context windows and active autonomous tasks. In the "autonomous" mode, the model takes initiative to continue working, and at these context sizes, the cost is exponential.

### 2. API "Thunder Herd" Race Condition (`/api/routines`)
The `claude-soma-api` service contains a critical race condition in the `list_routines` endpoint.

*   **Logic**: The endpoint calls `claude -p "Use the RemoteTrigger tool with action=list"`. To mitigate the 12s+ startup time, it uses a 5-minute cache.
*   **The Bug**: The cache is only marked as "valid" **after** the subprocess returns.
*   **The Spiral**:
    1. If multiple requests hit `/api/routines` while the cache is invalid, they **all** spawn a `claude -p` process.
    2. Each `claude -p` process is CPU-heavy. As more spawn, the server load increases (Load Average reached **12.23**).
    3. High load causes `claude -p` to take longer than 30s, triggering a **timeout** in the API.
    4. Because it timed out, the cache is **never populated**.
    5. The next wave of requests spawns even more processes.
*   **Cost**: Each `claude -p` call is a fresh session prompt. While cheaper than long sessions, thousands of these calls triggered by a retry-loop in the frontend or a dashboard poller contributed to the drain.

### 3. Port Conflict Loop
The `hermes-api` service was found in a tight restart loop (every 2 seconds) due to a port conflict:
`notify listener failed to start: [Errno 98] Address already in use`
While this consumed CPU, it is a secondary issue to the token drain.

---

## Recommendations (For Information Only)

1.  **Context Management**: Implement automatic `/clear` or context truncation for leads when they reach > 100k tokens.
2.  **Cache Locking**: Use a proper lock (e.g., `asyncio.Lock` or a file lock) in `_query_cloud_routines_cached` to ensure only one `claude -p` process is spawned regardless of concurrent requests.
3.  **Autonomous Safety**: Ensure autonomous tasks have a turn limit or mandatory user check-in after N turns.
4.  **Usage Snapshot**: Fix the parser in `usage_snapshot.py` (which currently records 0.0) to correctly alert on spikes before they exhaust the entire credit pool.
