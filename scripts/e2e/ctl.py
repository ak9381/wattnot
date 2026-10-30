"""E2E Networks GPU VM controller (launch / status / delete / balance watchdog).

Drives one GPU VM on the E2E Networks TIR API with hard safety rails: exactly one VM ever
(``vm.json`` is the lock), a lifetime cap, and a watchdog that tears the VM down before the
deadline or if the prepaid balance falls below a floor.

All credentials come from the environment and are NEVER hardcoded or printed. The HTTP helper
``post.call`` reads ``E2E_API_KEY`` / ``E2E_API_TOKEN`` from env; this module holds no keys,
tokens or credentialled IPs. The numeric ``project_id`` / SKU / security-group / ssh-key ids
below are account-scoped resource identifiers, not secrets.

Commands::

  ctl.py images                        list VM images (find the image_version_id to launch)
  ctl.py launch --image-version-id N   needs env RUN3_GO=1; refuses if vm.json already exists
  ctl.py status | wait | delete        delete = DELETE + verify (GET must be 404 / list empty)
  ctl.py watchdog                      deletes near the deadline, or if balance < Rs1500

This module is import-safe: importing it runs no API calls and has no required side effects, so
the launch-rule helpers can be unit tested on a host without the ``post`` helper.
"""
import argparse, datetime as dt, json, os, subprocess, sys, time

_POST_DIR = os.environ.get("E2E_POST_DIR", "/workspace/e2e")
if os.path.isdir(_POST_DIR) and _POST_DIR not in sys.path:
    sys.path.insert(0, _POST_DIR)
try:
    from post import call  # reads E2E_API_KEY / E2E_API_TOKEN from env, never printed
except ImportError:
    call = None  # post is only present on the control host; the pure helpers stay importable for tests
D = "/workspace/hackathon/e2e-glimmer-test"
P = dict(project_id=18993, location="Delhi")
SKU_ID, PRICE_ID = 525, 7087          # GDC.1xH10080-26.250GB_SXM_v3, Rs379.05/h (same as RUN1)
SG, SSHKEY = 34486, 4364
MAX_H = float(os.environ.get("RUN3_MAX_H", "3.5"))   # Glimmer test VM: 3.5 h booking (Akash, Oct 30); delete as soon as the plan ends
# Default-on spend backstop for the watchdog (INR, taxed). Sized for the 3.5 h test (3.5 h x Rs451.2 =
# Rs1579, under this), so it is a backstop, not the primary rail; raise it for a longer run.
BUDGET_CAP_INR = float(os.environ.get("RUN3_BUDGET_CAP_INR", "2000"))
INR_PER_H = 451.2   # E2E H100 + GST + disk (MEASURED price list), matches the driver
DISK_GB = int(os.environ.get("RUN3_DISK_GB", "500"))   # first 30 GB free, then ~Rs5/GB/month (about Rs350/month for 100 GB; hours here = a few rupees)
IMAGE_VERSION_ID = 111   # Ubuntu 24.04, CUDA 13.0 (checked in the E2E image list, Delhi)
# E2E's launch gate needs the prepaid balance to cover the whole VM lifetime at
# the list price. For the documented 24 h long run that is 24 x Rs379.05 = Rs9097
# (long-run-plan.md). The old precheck hard-coded Rs8122, which is below that gate
# and would let a launch be refused by E2E (or cut off) mid-run.
LONG_RUN_FLOOR = 9097
def required_balance(max_h, price):
    """Rupees the balance must hold before launch.
    Long run (>2 h): at least the E2E launch rule (full lifetime x list price),
    never below the documented Rs9097 24 h floor. 2 h test: its taxed cost plus
    the Rs1,500 safety floor (documented)."""
    if max_h > 2:
        return max(max_h * price, LONG_RUN_FLOOR)
    return max_h * 451.2 + 1500   # 2 h test: its cost (with tax) plus the Rs 1,500 floor
def vm(): return json.load(open(f"{D}/vm.json"))
def list_nodes():
    """All VMs in the project (used for the idempotency check before a launch)."""
    s, j = call("GET", "gpu/notebooks/", **P)
    return (j.get("data") or []) if isinstance(j, dict) else []
def find_by_name(nodes, name):
    """First VM whose name matches, else None. Mirrors wattnot.proof.launch_guard."""
    return next((n for n in nodes if isinstance(n, dict) and n.get("name") == name), None)
def find_ip(o):
    """First public-looking IPv4 under any key with 'ip' in its name (response layout not known)."""
    import re, ipaddress
    if isinstance(o, dict):
        for k, v in o.items():
            if "ip" in k.lower() and isinstance(v, str) and re.fullmatch(r"\d+\.\d+\.\d+\.\d+", v) \
                    and not ipaddress.ip_address(v).is_private: return v
        for v in o.values():
            r = find_ip(v)
            if r: return r
    elif isinstance(o, list):
        for v in o:
            r = find_ip(v)
            if r: return r
    return None
def balance():
    s, j = call("GET", "customer/details/")
    return float((j.get("data") or {}).get("prepaid_user_balance"))
def sku_price():
    s, j = call("GET", "gpu/gpu_service/sku/", service="vm", **P)
    for x in ((j.get("data") or {}).get("GPU") or []):
        if x.get("sku_id") == SKU_ID:
            for p in x.get("plans") or []:
                if p.get("sku_item_price_id") == PRICE_ID:
                    return x["name"], p["unit_price"], x.get("available_inventory_count")
    return None, None, None
def delete():
    v = vm(); ok = False
    for _ in range(10):
        s, j = call("DELETE", f"gpu/notebooks/{v['id']}/", **P)
        open(f"{D}/ctl.log", "a").write(f"{dt.datetime.now()} DELETE {s} {str(j)[:160]}\n")
        time.sleep(15)
        g, _ = call("GET", f"gpu/notebooks/{v['id']}/", **P)
        if g == 404: ok = True; break
    s, j = call("GET", "gpu/notebooks/", **P)
    left = [n for n in (j.get("data") or []) if n.get("id") == v["id"]] if isinstance(j, dict) else None
    if ok and left == []:   # the list read itself must succeed and not hold the VM
        open(f"{D}/DELETED", "w").write(str(dt.datetime.now()))
        os.replace(f"{D}/vm.json", f"{D}/vm.{v['id']}.deleted.json")   # kept for the record; frees the next launch
        print("DELETED+VERIFIED")
    else: print("NOT CONFIRMED - retry delete / check console", file=sys.stderr); sys.exit(2)
def _nomock_or_die():
    """Run the no-mock preflight before a live launch; refuse if any live input/step is a mock.
    Importing wattnot needs the repo src on the path (ctl.py runs from scripts/e2e)."""
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                       "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    try:
        from wattnot.longrun import nomock
    except Exception as e:  # noqa: BLE001 - never silently skip; a broken guard blocks the launch
        print(f"no-mock preflight could not load ({type(e).__name__}: {e}); refusing to launch",
              file=sys.stderr)
        sys.exit(6)
    report = nomock.audit_plan("glimmer-test")
    if not report["ok"]:
        print("NO-MOCK preflight FAILED; refusing to launch the live VM:", file=sys.stderr)
        for f in report["findings"]:
            print(f"  - {f['where']}: {f['what']}\n      fix: {f['fix']}", file=sys.stderr)
        sys.exit(6)
    print(f"no-mock preflight OK ({len(report['checked'])} blocks checked)")
    # the unattended sweep approves only through an operator-set policy: check it BEFORE spend
    from pathlib import Path

    from wattnot.longrun.driver import APPROVAL_POLICY, POLICY_DIR, approval_policy_problem
    why = approval_policy_problem(Path(APPROVAL_POLICY))
    if why:
        print(f"APPROVAL POLICY: {why}; refusing to launch. Set it with:\n  wattnot proposals "
              f"--dir {POLICY_DIR}/proposals policy set --by '<your name>' --file {APPROVAL_POLICY}",
              file=sys.stderr)
        sys.exit(6)
    print("approval policy OK")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    # subcommands + one-line help mirror the "Commands::" block above. No command / an unknown one
    # makes argparse print usage to stderr and exit 2 (never an IndexError on sys.argv[1]).
    p = argparse.ArgumentParser(prog="ctl.py", description=(__doc__ or "").strip().split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True, metavar="command")
    sub.add_parser("images", help="list VM images (find the image_version_id to launch)")
    pl = sub.add_parser("launch", help="needs env RUN3_GO=1; refuses if vm.json already exists")
    pl.add_argument("--image-version-id", type=int, default=None,
                    help=f"image_version_id to launch (default {IMAGE_VERSION_ID})")
    sub.add_parser("status", help="show VM status + IP (writes the ip file when found)")
    sub.add_parser("wait", help="poll until the IP shows (no GPU work happens here)")
    sub.add_parser("delete", help="DELETE + verify (GET must be 404 / list empty)")
    sub.add_parser("watchdog", help="deletes near the deadline, on low balance, or over the spend cap")
    a = p.parse_args(argv)
    c = a.cmd
    if c == "images":
        s, j = call("GET", "gpu/gpu_service/image/", category="vm", location="Delhi")
        for im in (j.get("data") or []):
            print(im["image_id"], im["name"], [(x["image_version_id"], x["version"]) for x in im["versions"]][:6])
    elif c == "launch":
        assert os.environ.get("RUN3_GO") == "1", "no go-ahead: set RUN3_GO=1 only after Akash approves"
        _nomock_or_die()   # refuse to launch a live VM onto mock/placeholder/stub inputs
        VM_NAME = "wattnot-run3"
        # (1) O_EXCL lock: atomic, so two launchers can never both get past this point and both
        #     POST a VM. A second attempt (or a crashed prior one) refuses instead of racing.
        #     Canonical logic: wattnot.proof.launch_guard.launch_lock / guarded_launch.
        lock = f"{D}/launch.lock"
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            print(f"launch lock {lock} held: {open(lock).read()!r}. Another launch is in "
                  f"progress or crashed; verify no VM is running, then rm the lock.", file=sys.stderr)
            sys.exit(4)
        os.write(fd, json.dumps(dict(pid=os.getpid(), ts=time.time(), name=VM_NAME)).encode()); os.close(fd)
        try:
            # (record check) a VM was already launched for RUN3 -> never launch a second.
            assert not os.path.exists(f"{D}/vm.json"), "a VM was already launched for RUN3"
            if os.path.exists(f"{D}/DELETED"):   # an old marker would make the new watchdog exit at once
                os.replace(f"{D}/DELETED", f"{D}/DELETED.{int(time.time())}")
            # (2) idempotency: if the provider already has a VM named wattnot-run3, adopt it and
            #     do NOT create a duplicate. Record it so the watchdog can tear it down.
            existing = find_by_name(list_nodes(), VM_NAME)
            if existing is not None:
                now = dt.datetime.now()
                # never re-arm a fresh lifetime on adoption: start the clock at the recorded intent
                try:
                    now = min(now, dt.datetime.fromisoformat(
                        json.load(open(f"{D}/vm.intent.json"))["ts"]))
                except Exception:  # noqa: BLE001 - no intent record: clock starts now (logged)
                    print("adopting with no vm.intent.json: deadline counted from now")
                json.dump(dict(id=existing["id"], launch_ts=now.isoformat(), adopted=True,
                               deadline=(now + dt.timedelta(hours=MAX_H)).isoformat(), sg=SG,
                               ssh_key_pk=SSHKEY), open(f"{D}/vm.json", "w"))
                print("adopted existing", existing["id"], "(no new VM created)")
                subprocess.Popen([sys.executable, __file__, "watchdog"], stdout=open(f"{D}/wd.out", "a"),
                                 stderr=subprocess.STDOUT, start_new_session=True)
                return
            ivid = a.image_version_id if a.image_version_id is not None else IMAGE_VERSION_ID
            pub = open(os.path.expanduser("~/.ssh/e2e_h100.pub")).read().strip()
            body = dict(name=VM_NAME, cluster_type="tir-cluster", image_type="pre-built", kind="vm",
                        is_jupyterlab_enabled=False, notebook_type="new", notebook_url="", disk_size_in_gb=DISK_GB,
                        dataset_id_list=[], enable_ssh=False, add_ons=[], init_script_id_list=[],
                        image_version_id=ivid, sku_id=SKU_ID, sku_item_price_id=PRICE_ID,
                        instance_type="paid_usage", public_key=[pub], security_group_id=SG,
                        is_pvc_encrypted=False)
            b = balance(); name, price, inv = sku_price()
            assert price and price <= 379.05, ("price changed", price)
            open(f"{D}/ctl.log","a").write(f"{dt.datetime.now()} PRELAUNCH balance=Rs{b:.2f} sku={SKU_ID}/{PRICE_ID} {name} price={price} inv={inv} disk={DISK_GB}GB\n")
            print("prelaunch balance", round(b,2), "sku", SKU_ID, name, "price/h", price, "disk", DISK_GB)
            need = required_balance(MAX_H, price)
            assert b >= need, f"balance Rs{b:.0f} does not cover Rs{need:.0f} for {MAX_H} h"
            now = dt.datetime.now()
            # (3) persist intent BEFORE the POST, so a crash mid-create leaves a breadcrumb (the
            #     VM name) to find and tear down the orphan.
            json.dump(dict(name=VM_NAME, ts=now.isoformat(), stage="pre-create"),
                      open(f"{D}/vm.intent.json", "w"))
            s, j = call("POST", "gpu/notebooks/", body, **P)
            assert s in (200, 201), (s, str(j)[:300])
            json.dump(dict(id=j["data"]["id"], launch_ts=now.isoformat(),
                           deadline=(now + dt.timedelta(hours=MAX_H)).isoformat(), sg=SG, ssh_key_pk=SSHKEY),
                      open(f"{D}/vm.json", "w"))
            try: os.unlink(f"{D}/vm.intent.json")
            except OSError: pass
            subprocess.Popen([sys.executable, __file__, "watchdog"], stdout=open(f"{D}/wd.out", "a"),
                             stderr=subprocess.STDOUT, start_new_session=True)
            print("launched", j["data"]["id"])
        finally:
            try: os.unlink(lock)   # release the launch lock no matter how we leave
            except OSError: pass
    elif c == "status":
        s, j = call("GET", f"gpu/notebooks/{vm()['id']}/", **P); d = (j.get("data") or {}) if isinstance(j, dict) else {}
        ip = find_ip(d)
        if ip: open(f"{D}/ip", "w").write(ip)
        print(s, d.get("status"), ip, "" if ip else f"(no IP found; keys: {sorted(d)[:25]})")
    elif c == "wait":   # poll until the IP shows (no GPU work happens here)
        for _ in range(60):
            s, j = call("GET", f"gpu/notebooks/{vm()['id']}/", **P); d = (j.get("data") or {}) if isinstance(j, dict) else {}
            ip = find_ip(d)
            if ip: open(f"{D}/ip", "w").write(ip); print(d.get("status"), ip); return
            time.sleep(15)
        print("no IP after 15 min", file=sys.stderr); sys.exit(3)
    elif c == "delete": delete()
    elif c == "watchdog":
        launch_ts = dt.datetime.fromisoformat(vm()["launch_ts"])
        dl = dt.datetime.fromisoformat(vm()["deadline"]) - dt.timedelta(minutes=8)
        while dt.datetime.now() < dl:
            if os.path.exists(f"{D}/DELETED"): return
            # default-on spend backstop: tear down if the elapsed VM time has cost more than the cap
            spent = (dt.datetime.now() - launch_ts).total_seconds() / 3600.0 * INR_PER_H
            if spent >= BUDGET_CAP_INR:
                open(f"{D}/ctl.log","a").write(f"{dt.datetime.now()} SPEND WATCHDOG Rs{spent:.0f} >= cap Rs{BUDGET_CAP_INR:.0f}\n"); break
            try:
                b = balance(); open(f"{D}/balance.log","a").write(f"{dt.datetime.now()} {b:.2f}\n")
                if b < 1500: open(f"{D}/ctl.log","a").write(f"{dt.datetime.now()} BALANCE WATCHDOG {b:.2f}\n"); break
            except Exception as e: open(f"{D}/ctl.log","a").write(f"{dt.datetime.now()} bal err {type(e).__name__}\n")
            time.sleep(120)
        # a failed or crashed delete must never end the watchdog while the VM still bills
        i = 0
        while not os.path.exists(f"{D}/DELETED"):   # never give up while the VM may still bill
            i += 1
            try:
                delete(); return
            except SystemExit:
                pass
            except Exception as e:  # noqa: BLE001 - network error: log the type only, retry
                open(f"{D}/ctl.log","a").write(f"{dt.datetime.now()} delete err {type(e).__name__}\n")
            open(f"{D}/ctl.log","a").write(f"{dt.datetime.now()} WATCHDOG delete retry {i}\n")
            time.sleep(min(60 * i, 300))
if __name__ == "__main__":
    main()
