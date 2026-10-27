"""Original two-sheet arrangement and honest fallback for older snapshots."""
import re
from .legacy_output import fixed
from .wire_selection import recorded

COLUMNS = 76


def byte_width(text):
    return sum(1 if ord(c) < 128 else 2 for c in text)


def frame(text=''):
    return '* ' + text + ' ' * max(0, COLUMNS-3-byte_width(text)) + '*'


def band(snapshot):
    p = snapshot['inputs']
    def n(key, digits=0):
        value = p.get(key)
        return '-' if value is None else f'{value:.{digits}f}'
    return [frame(f'型号:{p.get("SizeX", snapshot["name"])} 功率:{n("kW",2)}kW 极数:{n("p")} 接法:{p.get("Jiefa","-")} 电压:{n("Ufai")} 频率:{n("f")}'),
            frame(f'硅钢片:{p.get("Paihao","-")}  绝缘:{p.get("Jueyuan","-")}')]


def fallback(snapshot):
    """Missing intermediate results stay absent, never borrowed from another run."""
    p, v = snapshot['inputs'], snapshot['values']
    wire = recorded(snapshot)['used'] or {}
    def P(key,w=6,d=2):return fixed(p.get(key),w,d)
    def V(key,w=7,d=4):return fixed(v.get(key),w,d)
    def W(key,w=4,d=2):return fixed(wire.get(key),w,d)
    rule = '*' + '.'*74 + '*'
    first = ['*'*25+' 电磁设计计算单 '+'*'*25]+[frame() for _ in range(21)]+[rule]
    d2 = p.get('Di1',0)-2*p.get('g',0) if p.get('Di1') is not None and p.get('g') is not None else None
    rows = [
        f'Q1={P("Q1",3,0)} D1 ={P("D1",5,1)} D2={fixed(d2,5,1)}     :  STATOR       ROTOR',
        f'Q2={P("Q2",3,0)} Di1={P("Di1",5,1)} g ={P("g",4,2)}    U1:{P("Ufai",7,0)}                    Ty={V("Ty",7,2)}',
        f'L ={P("L",6,1)} Leff=     -        A :{P("a",6,0)}                    Fd1={V("fd",7,2)}',
        f'L1=     -    L2=     -        zf:       -                    d ={P("d",7,1)}',
        f'b01={P("b01",5)} b02={P("bc02",5)}         z :{P("ZZ",6,0)}                    cs=      -',
        f'bs1={P("bs1",5)} br1={P("br1",5)}        Czn:{P("czn",6,0)}                    Dr={V("Dr",7,1)}',
        f'Rs =     - br2={P("br2",5)}      N1*d1:{W("JJ",4,0)}*{W("Djj")}',
        f'hs1=     - br3={P("br3",5)}      N2*d2:{W("MM",4,0)}*{W("Dmm")}',
        f'hs2=     - br4={P("br4",5)}         S :{V("S1",9,3)}',
        f'Hs0={P("hs0",5)} hr0={P("hr0",5)}         Y :{P("Y1",9,2)}',
        f'bt1={V("bt1a",5,2)} hr1=    -          Lz:{V("Lz",9,2)} Gfe:{V("Gfe",8,3)}',
        f'bt2={V("bt2mi",5,2)} hr2=    -         Gcu:{V("Gcu",9,3)}     {V("Gal",8,3)}',
        f'bt3=     - hr3={P("hr3",5)}         R1:{V("R1a",9,4)}           -',
        f't1 =     - t2 =     -         J :{V("J1",8,2)} {V("J2",5,2)} /{V("Jr",5,2)}',
        f'Zs1={P("zs1",5)} Zs2={P("zs2",5)}         Sk:{P("sk",9,2)}           -',
        f'hc1={V("hc1",6,2)} hc2={V("hc2",6,2)}       Se:        -',
        f'Di2={P("Di2",5,1)} Zc={P("zc",3,0)}              Sf:{V("Sf",9,4)}   Cost:{V("CostFx",10,2)}',
    ]
    first += list(map(frame,rows))+[rule]
    first += [frame('        mm*mm     B       L        AT/cm      AT      i2=    -         -')]
    for prefix,key,loss,value in [('st','Bts','pcu1','Pcu'),('sc','Bys','pcu2','Pal'),('rt','Btru','pfe','Pfe'),('','Btrl','Ps','Ps1'),('rc','Byr','pfw','Pfw1'),('g0','Bg','pg',None)]:
        first.append(frame(f' {prefix:2s}:        - {V(key,8,0)}      -          -        -    {loss:4s}=     - {V(value,8,1)}'))
    first += [frame(f' Ikw=     - a    c1=    -   c2=    -     AT=       -   p1={V("P1",8,3)}'),
              frame(f' Im ={V("Im",6,2)} a    Btrmu={V("btrmu",6,0)}   bt2min={V("bt2mi",6,2)}'),rule,
              frame('       Eff       Pf        Tm       Tst      Ist        AJ       Sn'),
              frame(f'    {V("ATA1",6,4)}    {V("PF1",6,4)}      {V("TM1",4,2)}      {V("TST1",4,2)}     {V("IST1",4,2)}    {V("Aj",8,1)} {V("Sn",7,4)}'),
              frame(f' JB:{P("ATA",6,4)}    {P("PF",6,4)}      {P("TM",4,2)}      {P("TST",4,2)}     {P("IST",4,2)}   n={V("n",7,1)}'),rule,*band(snapshot)]
    second=[frame('          kd        kp       kdp      zfk       kc  Eff0=      -'),
            frame(' stator:     -         -         -        -        -   ip=      -'),
            frame(' rotor:                                           -   im=      -'),
            frame(' Tp =     -    Fs =     -    Ft =     -             -   ix=      -'),
            frame(' Ke0=     -    Ke1=     -    Ft0=     -                 ir=      -'),
            frame(f' Fa=         -                                        i1=     - {V("I1",7,2)}'),rule,
            frame('         B0         Cm**3     w/cm**3     p             Si    Wi    Hw'),
            frame(f'  st:        -          -          -        -         {P("Si",5)} {P("Wi",5)} {P("hw",5,1)}'),
            frame('  sc:        -          -          -        -'),
            frame(f'  Gfe={V("Gfe",7,1)}(kg)   PPfe={V("Pfe",7,1)}  I10={V("I0",6,2)} a P10={V("P0",8,1)} S0=     -'),rule,
            frame(f' A1={V("A11",7,2)}                           H1=     -   LB={P("L",6,1)} SB=      -'),
            frame(f' Cx=      -                            H2=     -   Dr={V("Dr",6,1)} Sr={P("Srr",7,0)}'),
            frame(' Ku1=     -   Spu1=     -   Ku2=  -   Spu2=     -   rb=     -   Rb=      -'),
            frame(' KL1=     -   SpL1=     -   KL2=  -   SpL2=     -   re=     -   Re=      -'),
            frame(f'              Sp1=     -             Sp2=     -   r2={V("r2",6,4)}   R2=      -'),rule]
    left=['Xs1','Xd1','Xe1','X1','Xs2','Xd2','Xe2','Xsk','X2','X','r1','R1','r2','']
    mid=['Xs1s','Xd1s','Xe1','X1s','Xs2s','Xd2s','Xe2','Xsks','X2s','Xst','r2s','rst','zst','']
    right=['Kcc','XXo','rro','ATst','BL','kz','1-kz','Cs1','Cs2','dspu1','dspu2','spl2s','sp1s','sp2s']
    mapping={'X1':'X1','X2':'X2','X':'X','X1s':'X1s','X2s':'X2s','Xst':'Xst','r1':'r1b','R1':'R1a','r2':'r2','r2s':'r2st','rst':'rst'}
    for a,b,c in zip(left,mid,right):
        second.append(frame(f' {a:>4s}={V(mapping.get(a),7,4)}              {b:>4s}={V(mapping.get(b),7,4)}            {c:>5s}=      -'))
    second += [rule,*band(snapshot),'#'*76,'']
    groups=[('AA','f Ufai Paihao kfe hw Si Wi L01 L02 L02u dk nk dk2 d kt ky Ccu Cfe Cal'),
            ('BB','kW p Q1 Q2 D1 Df Dn Xn Di1 Di2 g Y1 sk pfw ps Srr Jc srl hrr hrl le lel'),
            ('CC','b01 hs0 zs1 bs1 bs2 hs12 zc bc02 hr0 zs2 br1 br2 br3 br4 hr12 hr3 hr4 L a ZZ JJ Djj MM Dmm'),
            ('DD','ATA PF IST TST TM CORR')]
    for label,keys in groups:
        second.append(' '+label+':');keys=keys.split()
        for start in range(0,len(keys),8):
            second.append(' '+' '.join(fixed(p.get(k) if isinstance(p.get(k),(int,float)) else None,8,4) for k in keys[start:start+8]))
    return first,second


def native_pages(snapshot):
    packet=snapshot.get('legacy_report',{})
    text=packet.get('text') if packet.get('status')=='complete' else None
    if not isinstance(text,str) or len(text)>100000:
        return fallback(snapshot),False
    lines=[line[3:] if line.startswith('   ') else line for line in text.splitlines()]
    start=next((i for i,line in enumerate(lines) if 'STATOR' in line and 'ROTOR' in line),None)
    split=next((i for i,line in enumerate(lines) if 'kdp' in line and 'zfk' in line),None)
    end=next((i for i,line in enumerate(lines) if line.strip()=='DD:'),None)
    if start is None or split is None or end is None or not start<split<end:
        return fallback(snapshot),False
    # Original RCO sheet reserves its first 21 blank lines for the slot drawing.
    first=lines[1:split]
    material=next((i for i,line in enumerate(first) if '硅钢片:' in line),len(first)-1)
    first=first[:material+1]
    second=lines[split:]
    while second and not second[-1].strip():second.pop()
    return (first,second),True


def material_line(line,snapshot):
    if '硅钢片:' not in line or 'B25:' not in line:return line
    name=str(snapshot['inputs'].get('Paihao','-'))
    short=''
    for ch in name:
        if byte_width(short+ch)>9:break
        short+=ch
    return re.sub(r'硅钢片:.*?B25:',lambda _: '硅钢片:'+short+' '*(10-byte_width(short))+'B25:',line)
