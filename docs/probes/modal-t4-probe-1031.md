# Modal 1xT4 probe (31 Oct 2026, 09:17 IST)

Script: scripts/modal/t4_probe.py.

Result: **did not run.** Modal refused to start the T4 function: "Please add a payment method to use T4 GPU functions."

- App: wattnot-t4-probe (ap-QxyDKmgpaqyWlqyJCc1g7W). The image build ran on Modal's free CPU build step; no GPU container ever started.
- `modal app list` after the attempt: the app is stopped with 0 tasks. Nothing is left running.
- Spend: $0.00 (ESTIMATE; no GPU time was billed, and the account has no payment method).
- Not measured: nvidia-smi power.draw, the NVML total energy counter (two reads 5 s apart), and `nvidia-smi -pl 60` together with the NVML set-limit call. No numbers exist for these.
- To run it: add a payment method on Modal (Akash's call), then `modal run scripts/modal/t4_probe.py`. The function's timeout is 120 s, it returns its results as JSON, and it exits on its own. Expected cost is about $0.01 (ESTIMATE).
