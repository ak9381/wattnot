import json,sys,re
p=sys.argv[1]; s=open(p).read()
# change the first energy-like number in the copy by one digit
m=re.search(r'"(saving_pct|energy_saving_pct|savings_pct|joules[a-z_]*|wh[a-z_]*)":\s*(-?\d+\.?\d*)',s)
if not m: m=re.search(r'":\s*(\d+\.\d+)',s); k='number'; old=m.group(1); s=s[:m.start(1)]+str(round(float(old)+1,4))+s[m.end(1):]
else: k=m.group(1); old=m.group(2); s=s[:m.start(2)]+str(round(float(old)+1,4))+s[m.end(2):]
open(p,'w').write(s); print(f"edited {k}: {old} -> {round(float(old)+1,4)}  (copy only)")
