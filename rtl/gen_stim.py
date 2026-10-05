import sys, random, math
nb=int(sys.argv[1]); ne=int(sys.argv[2]); seed=int(sys.argv[3]); mode=sys.argv[4]
rnd=random.Random(seed)
w=[1/((k+1)**1.1) for k in range(8)]            # Zipf(1.1) over 8 classes, as in the paper
with open('stim.hex','w') as f:
    for _ in range(nb):
        if mode=='paper':
            ln=int(round(math.exp(rnd.uniform(math.log(8),math.log(128)))))   # log-uniform 32..512 KiB
            c=rnd.choices(range(8),w)[0]; rel=0
        else:                                    # stress: uniform classes, relocation batches mixed in
            rel=1 if rnd.random()<0.25 else 0
            ln=64 if rel else rnd.randrange(8,129)
            c=rnd.randrange(8)
        f.write('%03x\n'%((rel<<11)|(ln<<3)|c))
with open('host.hex','w') as f:
    busy=0; sig=0
    for _ in range(ne):
        if rnd.random()<0.15: busy^=1
        if rnd.random()<0.15: sig^=1
        backlog=rnd.randrange(4001,9000) if busy else rnd.randrange(0,4001)
        safe=rnd.choice([0,1,2,17,17,17])
        f.write('%07x\n'%((safe<<17)|(sig<<16)|backlog))
