"""Facing stator/rotor sketch used by the original-style calculation sheet."""
from copy import deepcopy
from .slot_diagrams import slot_drawings


def combined_slots(parameters):
    panel = dict(width=270, height=300, title='定转子槽型组合图', description='尺寸符号与原版计算单对应', paths=[], lines=[], texts=[])
    def text(x,y,label,size=10,rotate=0,anchor='middle'):
        panel['texts'].append(dict(x=x,y=y,text=label,size=size,anchor=anchor,rotate=rotate))
    def line(x1,y1,x2,y2):
        panel['lines'].append(dict(x1=x1,y1=y1,x2=x2,y2=y2,stroke='#000'))
    def path(commands,fill='none'):
        panel['paths'].append(dict(commands=commands,stroke='#000',fill=fill))
    def arrow(x,y,dx,dy):
        path([['M',x,y],['L',x+dx*5-dy*1.4,y+dy*5+dx*1.4],['L',x+dx*5+dy*1.4,y+dy*5-dx*1.4],['Z']],'#000')
    def vertical(x,y1,y2,label,label_left=True):
        line(x,y1,x,y2);arrow(x,y1,0,1);arrow(x,y2,0,-1)
        text(x-5 if label_left else x+11,(y1+y2)/2,label,9,-90)
    def horizontal(x1,x2,y,label,above=True):
        line(x1,y,x2,y);arrow(x1,y,1,0);arrow(x2,y,-1,0)
        text((x1+x2)/2,y-4 if above else y+11,label,9)
    stator,rotor=slot_drawings(parameters)
    # Reuse verified profile paths; only callout arrangement changes.
    for source,target_mouth,source_mouth,scale in ((stator,141,126,1.22),(rotor,161,36,1.18)):
        tx=104-76*scale;ty=target_mouth-source_mouth*scale
        if not source.get('supported'):
            for item in source['texts']:
                if item['y']<30:continue
                clone=deepcopy(item);clone.update(x=16+(item['x']-5)*1.2,y=(30 if source is stator else 176)+(item['y']-35)*.75,size=8)
                panel['texts'].append(clone)
            continue
        for item in source['paths']:
            clone=deepcopy(item)
            clone['commands']=[[op,*[n*scale+(tx if i%2==0 else ty) for i,n in enumerate(nums)]] for op,*nums in item['commands']]
            panel['paths'].append(clone)
        # Bore surfaces on either side of the open slot.
        mouth=next(item for item in source['paths'])['commands'][0][1]
        half=abs(mouth-76)*scale
        line(47,target_mouth,104-half,target_mouth);line(104+half,target_mouth,177,target_mouth)
    # Standard single-cage callouts mirror the reference sheet.
    if stator.get('supported'):
        geometry=stator['geometry'];scale=geometry['scale']*1.22
        top=141-geometry['total_depth']*scale
        end=141-(parameters['hs0']+parameters['hs12'])*scale
        shoulder=141-(parameters['hs0']+geometry['shoulder_height'])*scale
        ws=parameters['bs1']*scale/2;we=abs(parameters['bs2'])*scale/2;wo=parameters['b01']*scale/2
        line(104+we,end,204,end);line(104+wo,141-parameters['hs0']*scale,194,141-parameters['hs0']*scale)
        vertical(193,141-parameters['hs0']*scale,end,'hs12',False)
        vertical(177,shoulder,end,'hs0')
        horizontal(104-ws,104+ws,shoulder-10,'bs1')
        line(104-ws,shoulder-13,104-ws,shoulder+2);line(104+ws,shoulder-13,104+ws,shoulder+2)
        horizontal(104-wo,104+wo,147,'',False)
        text(137,150,'b01',9,anchor='start')
        line(104+wo,147,133,147)
        line(104-wo,141,104-wo,155);line(104+wo,141,104+wo,155)
        line(104,top,143,top);vertical(143,12,top,'hc1')
        vertical(224,12,141,'D1',False);vertical(207,122,141,'Di1',False)
        text(39,24,'Q1槽',11);text(61,27,'Rs',10)
        line(65,31,90,top+6);arrow(90,top+6,-.7,-.7)
        text(48,100,'bt1',10);line(59,103,82,shoulder-9);arrow(82,shoulder-9,-1,0)
        text(49,126,'ZS1',10);line(61,128,91,shoulder);arrow(91,shoulder,-1,0)
    if rotor.get('supported'):
        if parameters.get('zc') in (1,2):
            # The shared conventional path uses origin 38, not 36.
            for item in panel['paths'][len(stator['paths']):len(stator['paths'])+len(rotor['paths'])]:
                for command in item['commands']:
                    for i in range(2,len(command),2):command[i]-=2*1.18
            geometry=rotor['geometry'];scale=geometry['scale']*1.18
            end=161+(parameters['hr0']+parameters['hr12'])*scale
            bottom=161+geometry['total_depth']*scale
            shoulder=161+(parameters['hr0']+geometry['shoulder_height'])*scale
            w1=parameters['br1']*scale/2;w2=parameters['br2']*scale/2;wo=abs(parameters['bc02'])*scale/2
            horizontal(104-wo,104+wo,155,'')
            text(73,158,'b02',9,anchor='end')
            line(76,155,104-wo,155)
            horizontal(104-w1,104+w1,shoulder+16,'br1')
            horizontal(104-w2,104+w2,end-2,'br2')
            line(104+w1,shoulder,185,shoulder);line(104+w2,end,199,end)
            vertical(184,shoulder,end,'hr12',False)
            line(104-wo,161,45,161);line(104-w1,shoulder,45,shoulder)
            vertical(44,161,shoulder,'hr0')
            vertical(171,shoulder+12,end-8,'bt2')
            line(104,bottom,143,bottom);vertical(143,bottom,296,'hc2')
            vertical(224,161,296,'Di2',False);vertical(207,161,181,'D2',False)
            text(49,188,'ZS2',10);line(63,190,90,shoulder);arrow(90,shoulder,-1,.4)
            text(39,287,'Q2槽',11)
        else:
            # Preserve special/cage dimensions from the verified rotor renderer.
            for item in rotor['texts']:
                if 28<=item['y']<137:
                    clone=deepcopy(item);clone.update(x=104+(item['x']-76)*1.18,y=161+(item['y']-36)*1.18,size=max(8,item['size']*1.18));panel['texts'].append(clone)
            for item in rotor['lines']:
                clone=deepcopy(item)
                for key in ('x1','x2'):clone[key]=104+(item[key]-76)*1.18
                for key in ('y1','y2'):clone[key]=161+(item[key]-36)*1.18
                panel['lines'].append(clone)
            text(37,289,'Q2槽',11);text(135,295,'Zc='+str(int(parameters['zc'])),8)
    vertical(28,141,161,'g')
    return panel
