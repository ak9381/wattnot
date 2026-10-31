import modal
app = modal.App("wattnot-t4-probe")
img = modal.Image.debian_slim().pip_install("nvidia-ml-py")

@app.function(gpu="T4", image=img, timeout=120)
def probe():
    import subprocess, time, json, pynvml
    def sh(c):
        r = subprocess.run(c, shell=True, capture_output=True, text=True)
        return {"cmd": c, "rc": r.returncode, "out": (r.stdout + r.stderr).strip()[:600]}
    out = {"smi": sh("nvidia-smi --query-gpu=name,driver_version,power.draw,power.limit,power.min_limit,power.max_limit --format=csv")}
    pynvml.nvmlInit(); h = pynvml.nvmlDeviceGetHandleByIndex(0)
    try:
        e1 = pynvml.nvmlDeviceGetTotalEnergyConsumption(h); t1 = time.time(); time.sleep(5)
        e2 = pynvml.nvmlDeviceGetTotalEnergyConsumption(h); t2 = time.time()
        out["energy"] = {"e1_mJ": e1, "e2_mJ": e2, "dt_s": round(t2 - t1, 2), "avg_W": round((e2 - e1) / 1000 / (t2 - t1), 2)}
    except Exception as ex:
        out["energy"] = {"error": repr(ex)}
    out["nvml_power_W"] = pynvml.nvmlDeviceGetPowerUsage(h) / 1000
    out["pl"] = sh("nvidia-smi -pl 60")
    try:
        pynvml.nvmlDeviceSetPowerManagementLimit(h, 60000); out["nvml_set"] = "ok"
    except Exception as ex:
        out["nvml_set"] = repr(ex)
    out["after"] = sh("nvidia-smi --query-gpu=power.limit --format=csv,noheader")
    return json.dumps(out, indent=1)

@app.local_entrypoint()
def main():
    print(probe.remote())
