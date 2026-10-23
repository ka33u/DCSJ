"""Bounded VB-style arithmetic interpreter for the optional TX module.

The private resource supplies decoded IR, constants and cell bindings. No native
binary, Excel automation, Windows runtime or file IO is executed. Each call owns
its stack/heap; instruction and memory budgets apply to all paths.
"""
from datetime import date
from functools import lru_cache
import base64
import gzip
import json
import math
import struct
from pathlib import Path
from .numeric_vm import _VM, NumericKernelError, NumericKernelLimitError, _signed


def format_number(value, pattern):
    if pattern is None or isinstance(value,str):return str(value)
    if pattern not in {'.##','.###','.####','.#####','0.###'}:
        raise NumericKernelError('TX 数值格式不支持')
    digits=len(pattern.split('.')[1])
    text=f'{value:.{digits}f}'.rstrip('0').rstrip('.')
    if text in {'-0',''}:text='0'
    return text


@lru_cache(maxsize=4)
def load_program(path, checksum):
    # Immutable resource path plus checksum isolates installed versions.
    from .resources import digest
    path=Path(path)
    if digest(path)!=checksum:raise NumericKernelError('TX 计算资源校验失败')
    with gzip.open(path,'rb') as incoming:data=incoming.read(16*1024*1024+1)
    if len(data)>16*1024*1024:raise NumericKernelError('TX 计算资源过大')
    p=json.loads(data)
    if p.get('format')!='tx-numeric-ir' or p.get('format_version')!=1:
        raise NumericKernelError('TX 计算资源版本不兼容')
    if p.get('image_size')!=0x30000 or p.get('stack_size')!=0x100000 or len(p['instructions'])>50000:
        raise NumericKernelError('TX 计算资源边界无效')
    if (p.get('image_base'),p.get('stack_base'),p.get('initial_ebp'))!=(0x400000,0x1000000,0x10ff000):
        raise NumericKernelError('TX 计算状态布局无效')
    for row in p['instructions']:
        if not isinstance(row,list) or len(row)!=4 or any(type(n) is not int or not 0<=n<2**32 for n in row[:2]) or not isinstance(row[2],str) or not isinstance(row[3],list):
            raise NumericKernelError('TX 指令结构无效')
        for operand in row[3]:
            if not isinstance(operand,list) or not operand or operand[0] not in {'i','r','m'} or len(operand)!=(6 if operand[0]=='m' else 3) or operand[-1] not in {1,2,4,8,10}:
                raise NumericKernelError('TX 操作数结构无效')
    if len({r[0] for r in p['instructions']})!=len(p['instructions']):raise NumericKernelError('TX 指令地址重复')
    if len(p['arrays'])>32 or any(type(o) is not int or not -0x1700<=o<=-28 for o in p['arrays']):
        raise NumericKernelError('TX 数组布局无效')
    p['code']={r[0]:(r[1],r[2],r[3]) for r in p.pop('instructions')}
    p['imports']={int(k):v for k,v in p['imports'].items()}
    p['io_skips']={int(k):v for k,v in p['io_skips'].items()}
    p['initial_segments']=[(s['address'],base64.b64decode(s['data_base64'],validate=True)) for s in p.pop('segments')]
    if any(not 0x400000<=a<=a+len(b)<=0x430000 for a,b in p['initial_segments']):
        raise NumericKernelError('TX 初始常量超出内存范围')
    if not p['entry'] in p['code'] or len(p['bindings'])!=94:raise NumericKernelError('TX 计算入口或输入定义缺失')
    return p


class TXVM(_VM):
    def __init__(self,program,parameters):
        super().__init__(program,{})
        self.heap=0x1000100;self.gs=[];self.printed=[];self.formatted=[]
        self.date=date.today().isoformat();self.budget=500000
        for address in program['imports']:self.memory.set_uint(address,address)
        for offset in program['arrays']:
            self.memory.set_uint(self.reg('ebp')+offset+12,self.alloc(16*100))
        for binding in program['bindings']:
            self.put(self.reg('ebp')+binding['offset'],parameters[binding['key']])
        for key,offset in program['strings'].items():
            self.memory.set_uint(self.reg('ebp')+offset,self.bstr(parameters[key]))
        self.set_reg('esp',self.reg('ebp')-0x2500)

    def alloc(self,n):
        a=self.heap; self.heap+=(n+7)&~7
        if self.heap>=0x10e0000:raise ValueError('heap budget')
        return a

    def bstr(self,s):
        b=str(s).encode('utf-16le');a=self.alloc(len(b)+6);self.memory.set_uint(a,len(b));self.memory.write(a+4,b+b'\0\0');return a+4

    def string(self,p):
        if not p:return ''
        n=self.memory.uint(p-4)
        if n>10000 or n%2:raise ValueError(('not bstr',hex(p),n))
        return self.memory.read(p,n).decode('utf-16le')

    def val(self,p):
        t=self.memory.uint(p,2)
        if t&0x4000:return self.val(self.memory.uint(p+8))
        t&=0xfff
        if t==0:return 0.
        if t in (2,11):return self.memory.sint(p+8,2)
        if t==3:return self.memory.sint(p+8)
        if t==4:return self.memory.f32(p+8)
        if t==5:return self.memory.f64(p+8)
        if t==8:return self.string(self.memory.uint(p+8))
        raise ValueError(('variant type',t,hex(p),hex(self.pc)))

    def put(self,p,v):
        if isinstance(v,str):self.memory.write(p,struct.pack('<IIII',8,0,self.bstr(v),0))
        else:
            if not isinstance(v,(int,float)) or not math.isfinite(v):raise NumericKernelError('TX 中间结果不是有限数值')
            self.memory.write(p,struct.pack('<IId',5,0,float(v)))

    def arg(self,i):return self.memory.uint(self.reg('esp')+4*i)
    def numeric(self,p):return float(self.val(p))

    def callback(self):
        if self.pc in self.program['io_skips']:
            self.pc=self.program['io_skips'][self.pc]
            return True
        name=self.program['imports'].get(self.pc)
        if not name:return False
        self.calls[name]+=1
        if name in ['__vbaVarAdd','__vbaVarSub','__vbaVarMul','__vbaVarDiv','__vbaVarPow','__vbaVarAnd','__vbaVarOr','__vbaVarCmpEq','__vbaVarCmpNe','__vbaVarCmpLt']:
            out=self.arg(1);a=self.val(self.arg(3));b=self.val(self.arg(2))
            operations={'__vbaVarAdd':lambda:a+b,'__vbaVarSub':lambda:a-b,'__vbaVarMul':lambda:a*b,'__vbaVarDiv':lambda:a/b,'__vbaVarPow':lambda:a**b,'__vbaVarAnd':lambda:int(a)&int(b),'__vbaVarOr':lambda:int(a)|int(b),'__vbaVarCmpEq':lambda:-int(a==b),'__vbaVarCmpNe':lambda:-int(a!=b),'__vbaVarCmpLt':lambda:-int(a<b)}
            self.put(out,operations[name]());self.set_reg('eax',out);self.ret(12)
        elif name.startswith('__vbaVarTst'):
            a=self.val(self.arg(2));b=self.val(self.arg(1));op=name[-2:]
            result={'Eq':a==b,'Ne':a!=b,'Lt':a<b,'Le':a<=b,'Gt':a>b,'Ge':a>=b}[op]
            self.set_reg('eax',-int(result));self.ret(8)
        elif name in ['__vbaVarMove','__vbaVarCopy','__vbaVarDup']:
            a=self.reg('ecx');b=self.reg('edx');self.memory.write(a,self.memory.read(b,16));self.set_reg('eax',a);self.ret()
        elif name in ['__vbaFreeVar','__vbaFreeObj','__vbaFreeStr']:
            self.ret()
        elif name in ['__vbaFreeVarList','__vbaFreeObjList','__vbaFreeStrList']:
            self.ret()
        elif name in ['__vbaVarFix','__vbaVarInt','__vbaVarAbs','__vbaVarNeg']:
            a=self.arg(1);v=self.numeric(self.arg(2));self.put(a,{'__vbaVarFix':math.trunc,'__vbaVarInt':math.floor,'__vbaVarAbs':abs,'__vbaVarNeg':lambda x:-x}[name](v));self.set_reg('eax',a);self.ret(8)
        elif name in ['__vbaR8Var','__vbaI4Var','__vbaI2Var','__vbaBoolVarNull']:
            v=self.numeric(self.arg(1))
            if name=='__vbaR8Var':self.push_float(v)
            else:self.set_reg('eax',round(v))
            self.ret(4)
        elif name=='__vbaOnGoCheck':self.set_reg('eax',self.arg(1));self.ret(4)
        elif name=='__vbaChkstk':
            ret=self.pop();self.set_reg('esp',self.reg('esp')-self.reg('eax'));self.pc=ret
        elif name=='__vbaVarForInit':
            counter,limit,step,start,stop,increment=[self.arg(i) for i in range(1,7)]
            a=self.numeric(start);b=self.numeric(stop);c=self.numeric(increment)
            self.put(counter,a);self.put(limit,b);self.put(step,c);self.set_reg('eax',-int(a<=b if c>0 else a>=b));self.ret(24)
        elif name=='__vbaVarForNext':
            counter,limit,step=[self.arg(i) for i in range(1,4)];c=self.numeric(step);a=self.numeric(counter)+c;b=self.numeric(limit)
            self.put(counter,a);self.set_reg('eax',-int(a<=b if c>0 else a>=b));self.ret(12)
        elif name=='__vbaGosub':
            target=self.arg(0);self.gs.append(target);self.set_reg('eax',0);self.ret(4)
        elif name=='__vbaGosubReturn':
            self.pop();self.set_reg('esp',self.reg('esp')+4);self.pc=self.gs.pop();self.set_reg('eax',1)
        elif name=='#614':self.push_float(math.sqrt(self.memory.f64(self.reg('esp')+4)));self.ret(8)
        elif name in ['#582','#583','#584','#585','#586','#587']:
            f={'#582':math.sin,'#583':math.cos,'#584':math.tan,'#585':math.atan,'#586':math.exp,'#587':math.log}[name]
            self.push_float(f(self.memory.f64(self.reg('esp')+4)));self.ret(8)
        elif name=='#660':
            dest=self.arg(1);value=self.val(self.arg(2));fmt=self.val(self.arg(3)) if self.memory.uint(self.arg(3),2)!=10 else None;self.formatted.append({'site':self.arg(0),'value':value,'format':fmt,'pointer':self.arg(2)})
            self.put(dest,format_number(value,fmt));self.set_reg('eax',dest);self.ret(20)
        elif name=='#609':self.set_reg('eax',self.bstr(self.date));self.ret()
        elif name=='__vbaStrMove':
            self.memory.set_uint(self.reg('ecx'),self.reg('edx'));self.set_reg('eax',self.reg('edx'));self.ret()
        elif name=='__vbaFileClose':
            self.ret()
        elif name=='__vbaPrintFile':
            nxt=self.arg(0); _,op,args=self.program['code'][nxt];n=args[1][1]//4 if op=='add' else 1
            descriptor=self.arg(1);count=self.memory.uint(descriptor,2)
            if count>120 or len(self.printed)>400:raise NumericKernelLimitError('TX 报告体积超过限制')
            parts=[];tokens=[]
            for i in range(count):
                kind=self.memory.uint(descriptor+2+i,1);a=self.arg(i+3)
                if kind&0x3f==8:v=self.string(a)
                elif kind&0x3f==12:v=self.val(a)
                elif kind&0x3f==33:v=a
                else:raise ValueError(('print kind',hex(kind)))
                parts.append(v);tokens.append({'kind':kind,'value':v})
            self.printed.append({'site':nxt,'parts':parts,'tokens':tokens});self.ret()
        else:raise NotImplementedError((name,hex(self.pc),hex(self.arg(0))))
        return True

    def execute(self,next_pc,op,args):
        if op in ['shl','shr','sar']:
            a=self.integer(args[0]);n=self.integer(args[1])&31
            v=a<<n if op=='shl' else (_signed(a,args[0][-1]) if op=='sar' else a)>>n
            self.set_integer(args[0],v);self.flags(v,args[0][-1]);self.pc=next_pc
        elif op=='movsx':self.set_integer(args[0],_signed(self.integer(args[1]),args[1][-1]));self.pc=next_pc
        elif op=='inc':
            a=self.integer(args[0]);c=self.cf;self.set_integer(args[0],a+1);self.flags(a+1,args[0][-1],a,1,'add');self.cf=c;self.pc=next_pc
        elif op=='fnclex':self.pc=next_pc
        elif op=='jo':
            if self.of:raise ArithmeticError('overflow')
            self.pc=next_pc
        else:super().execute(next_pc,op,args)

    def run(self):
        while self.steps<self.budget:
            if self.pc==self.program['exit']:
                if self.gs:raise NumericKernelError('TX 子程序未正常返回')
                return self.printed
            self.steps+=1
            if not self.callback():
                if self.pc not in self.program['code']:raise NumericKernelError('TX 计算跳转无效')
                self.execute(*self.program['code'][self.pc])
        raise NumericKernelLimitError('TX 计算未在限定步数内收敛')
