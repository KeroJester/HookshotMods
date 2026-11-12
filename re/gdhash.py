import pickle, struct, sys
def mm3(key, seed=0):
    if isinstance(key, str): key = key.encode()
    h=seed; c1,c2=0xcc9e2d51,0x1b873593; n=len(key)
    for i in range(0, n//4*4, 4):
        k=struct.unpack_from("<I",key,i)[0]; k=(k*c1)&0xffffffff; k=((k<<15)|(k>>17))&0xffffffff; k=(k*c2)&0xffffffff
        h^=k; h=((h<<13)|(h>>19))&0xffffffff; h=(h*5+0xe6546b64)&0xffffffff
    t=key[n//4*4:]; k=0
    for i,b in enumerate(t): k|=b<<(8*i)
    if t: k=(k*c1)&0xffffffff; k=((k<<15)|(k>>17))&0xffffffff; k=(k*c2)&0xffffffff; h^=k
    h^=n; h^=h>>16; h=(h*0x85ebca6b)&0xffffffff; h^=h>>13; h=(h*0xc2b2ae35)&0xffffffff; h^=h>>16; return h
def index():
    r=pickle.load(open("re/gamedatalist.pkl","rb"))["Data"]
    idx={}
    for t,lst in r.items():
        for e in lst:
            if isinstance(e,dict) and "Hash" in e: idx.setdefault(e["Hash"],[]).append((t,e))
    return idx
if __name__=="__main__":
    idx=index(); found={}
    for line in open(sys.argv[1], errors="ignore"):
        s=line.rstrip("\n")
        for cand in {s, s.strip()}:
            h=mm3(cand)
            if h in idx and cand not in found: found[cand]=[t for t,_ in idx[h]]
    for k,v in sorted(found.items()): print(v, k)
