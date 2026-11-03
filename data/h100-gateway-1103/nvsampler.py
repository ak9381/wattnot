# 1 Hz NVML sampler: power, cap and the energy counter, MEASURED. Feeds the live page and the per-arm energy.
import json,time,pynvml as n
n.nvmlInit();h=n.nvmlDeviceGetHandleByIndex(0)
f=open("out/gateway/node.jsonl","a")
while True:
    w=n.nvmlDeviceGetPowerUsage(h)/1000;cap=n.nvmlDeviceGetPowerManagementLimit(h)//1000;e=n.nvmlDeviceGetTotalEnergyConsumption(h)/1000
    f.write(json.dumps({"kind":"sample","ts":time.time(),"gpu":0,"power_w":round(w,1),"joules":round(w,2),"cap_w":cap,"energy_counter_j":round(e,1),"label":"MEASURED","src":"sampler"})+"\n");f.flush()
    print(time.strftime("%H:%M:%S"),f"power {w:6.1f} W  cap {cap} W",flush=True);time.sleep(1)
