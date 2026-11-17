import numpy as np, zlib, struct, sys
def png(p):
    d=open(p,'rb').read(); o=8; idat=b''
    while o<len(d):
        n=struct.unpack('>I',d[o:o+4])[0]; t=d[o+4:o+8]
        if t==b'IHDR': w,h,bd,ct=struct.unpack('>IIBB',d[o+8:o+18])
        if t==b'IDAT': idat+=d[o+8:o+8+n]
        o+=12+n
    raw=zlib.decompress(idat); bpp=4 if ct==6 else 3; stride=w*bpp
    img=np.zeros((h,stride),np.int32); prev=np.zeros(stride,np.int32); p=0
    for y in range(h):
        f=raw[p]; line=np.frombuffer(raw[p+1:p+1+stride],np.uint8).astype(np.int32); p+=1+stride
        cur=line.copy()
        if f==1:
            for x in range(bpp,stride): cur[x]=(cur[x]+cur[x-bpp])&255
        elif f==2: cur=(line+prev)&255
        elif f in (3,4):
            for x in range(stride):
                a=cur[x-bpp] if x>=bpp else 0; b=prev[x]; c=prev[x-bpp] if x>=bpp else 0
                if f==3: cur[x]=(cur[x]+(a+b)//2)&255
                else:
                    pa,pb,pc=abs(b-c),abs(a-c),abs(a+b-2*c); pr=a if pa<=pb and pa<=pc else (b if pb<=pc else c); cur[x]=(cur[x]+pr)&255
        img[y]=cur; prev=cur
    return img.reshape(h,w,bpp)[...,:3]
R={'sky':(0,60,820,900),'left walls':(60,330,0,450),'right walls':(60,330,850,1280),'tower base':(180,330,450,830),'floor sunlit':(380,520,800,1200),'Link':(410,660,590,690)}
if __name__=='__main__':
    ims=[png(p) for p in sys.argv[1:]]
    for nm,(y0,y1,x0,x1) in R.items():
        print(f'{nm:13s}', '  '.join(str(im[y0:y1,x0:x1].reshape(-1,3).mean(0).round().astype(int)) for im in ims))
