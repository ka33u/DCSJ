"""Independent TX parameter contract; geometry is cm, wire diameters are mm."""
import math

KEYS = ('TYP AAAA BBBB CCCC DDDD EEEE KKKK LLLL MMMM CSXS ESXS GGP A1 CI D1 D4 D5 E4 E6 '
        'G H M1 M2 P2 P PFW PS Q1 Q2 U1 Y HZ DXJJ ZXJJ D3 OO SR DR Hs0 hs12 hr0 hr12 hr3 '
        'L DYXS WENDU N6 N7 D6 D7 Z1 b01 bs1 bs2 b02 br1 br2 br3 br4').split()
KEYS += [f'NDXP({i})' for i in range(1,7)] + [f'YDXP({i})' for i in range(1,7)]
KEYS += ['TMN','TQN','ISN'] + [f'KING({i})' for i in range(1,7)] + ['M6','M3']
KEYS += [f'K({i})' for i in range(2,7)] + ['SETA','FE','FXM','WDF','TA','TB','FFD','PFX','description']
LABELS = dict(zip(('TYP GGP A1 CI D1 D4 D5 E4 E6 G H M1 M2 P2 P PFW PS Q1 Q2 U1 Y HZ '
                  'DXJJ ZXJJ D3 OO SR DR Hs0 hs12 hr0 hr12 hr3 L DYXS WENDU N6 N7 D6 D7 '
                  'Z1 b01 bs1 bs2 b02 br1 br2 br3 br4 TMN TQN ISN SETA FE FXM WDF TA TB FFD PFX description').split(),
                 ['型号','冲片材料代号','并联路数','绝缘纸厚度','定子外径','转子内径（轴孔）','线圈伸出长度',
                  '参考效率','参考功率因数','单边气隙','槽楔厚度','定子绕组类型代码','转子槽型代码','额定功率','极数',
                  '机械损耗系数','杂散损耗系数','定子槽数','转子槽数','线电压','统一节距（同心绕组填 0）','频率',
                  '定子槽角','转子槽角','定子内径','斜槽倍数','端环截面积','端环中径','定子槽口高','定子槽身高',
                  '转子槽口高','转子上槽高','转子下槽高','铁心长度','叠压系数','计算温度','第一线规并绕根数',
                  '第二线规并绕根数','第一线规直径','第二线规直径','平均槽导体数（0 自动）','定子槽口宽','定子上槽宽',
                  '定子下槽宽','转子槽口宽','转子上槽宽','转子上槽底宽','转子下槽宽','转子下槽底宽',
                  '参考最大转矩倍数','参考启动转矩倍数','参考启动电流倍数','线圈端部角','铁损修正系数',
                  '空载电流修正系数','杂散损耗修正系数','启动转矩修正系数','最大转矩修正系数',
                  '启动电流修正系数','功率因数修正系数','设计说明']))
LABELS.update(CSXS='齿部铁损系数', ESXS='轭部铁损系数')
CM = set('CI D1 D4 D5 G H D3 DR Hs0 hs12 hr0 hr12 hr3 L b01 bs1 bs2 b02 br1 br2 br3 br4'.split())
BASIC = set('TYP description P2 P U1 HZ GGP E4 E6 TMN TQN ISN'.split())
GEOMETRY = CM | set('Q1 Q2 SR DYXS DXJJ ZXJJ OO'.split())
WINDING = set('A1 N6 N7 D6 D7 Z1 SETA'.split())
INTEGERS = set('A1 P Q1 Q2 M1 M2 N6 N7 GGP'.split())


class TXInputError(ValueError):
    pass


def schema():
    fields=[]
    for key in KEYS:
        group='额定参数' if key in BASIC else '铁心与槽型' if key in GEOMETRY else '导线与绕组' if key in WINDING else '计算系数'
        unit='cm' if key in CM else {'P2':'kW','U1':'V（线电压）','HZ':'Hz','SR':'cm²','D6':'mm','D7':'mm','WENDU':'°C','SETA':'°','DXJJ':'°','ZXJJ':'°'}.get(key,'')
        if key.startswith(('NDXP','YDXP')):group='线圈组'
        label=LABELS.get(key,key)
        fields.append({'key':key,'label':label,'unit':unit,'group':group,'type':'text' if key in {'TYP','description'} else 'number'})
    return {'module':'tx','schema_version':1,'fields':fields,'harmonic_orders':[1,5,7,11,13,17],
            'units':'尺寸采用 cm，线径采用 mm，功率采用 kW，电压采用线电压。参数独立于主设计模块。'}


def validate(parameters):
    if not isinstance(parameters,dict) or set(parameters)!=set(KEYS):
        missing=set(KEYS)-set(parameters) if isinstance(parameters,dict) else set(KEYS)
        extra=set(parameters)-set(KEYS) if isinstance(parameters,dict) else set()
        raise TXInputError('TX 参数不完整或含未知字段：'+', '.join(sorted(missing|extra)))
    p={}
    for key in KEYS:
        value=parameters[key]
        if key in {'TYP','description'}:
            if not isinstance(value,str) or len(value)>(120 if key=='TYP' else 2000) or any(ord(c)<32 and c not in '\n\t' for c in value):
                raise TXInputError(key+' 文本无效或过长')
            p[key]=value.strip();continue
        if type(value) not in (int,float) or not math.isfinite(value) or abs(value)>1000000:
            raise TXInputError(key+' 必须是有限数值且绝对值不大于 1000000')
        if value<0 and key not in {'ZXJJ','DXJJ','WENDU'}:raise TXInputError(key+' 不能为负数')
        if key in INTEGERS and value!=int(value):raise TXInputError(key+' 必须是整数')
        p[key]=float(value)
    if not p['TYP']:raise TXInputError('型号不能为空')
    for key in 'P2 U1 HZ A1 Q1 Q2 D1 D3 D4 G L SR DR bs1 bs2 hs12 br1 E4 E6 DYXS'.split():
        if p[key]<=0:raise TXInputError(key+' 必须大于 0')
    if p['P'] not in range(2,22,2) or max(p['Q1'],p['Q2'])>360:raise TXInputError('极数须为 2–20 的偶数，槽数不得超过 360')
    if not p['D4']<p['D3']-2*p['G']<p['D1']:raise TXInputError('内外径和气隙关系无效')
    if max(p['E4'],p['E6'],p['DYXS'])>1:raise TXInputError('效率、功率因数、叠压系数采用小数，不得大于 1')
    if p['Y']!=0:raise TXInputError('TX 独立模块采用各线圈节距，统一节距 Y 请保持 0')
    if p['N6']+p['N7']<=0:raise TXInputError('并绕根数之和必须大于 0')
    if p['N6']>0 and p['D6']<=0:raise TXInputError('第一线规根数大于 0 时必须填写线径')
    if p['N7']>0 and p['D7']<=0:raise TXInputError('第二线规根数大于 0 时必须填写线径')
    turns=[p[f'NDXP({i})'] for i in range(1,7)]
    pitches=[p[f'YDXP({i})'] for i in range(1,7)]
    if sum(turns)<=0:raise TXInputError('至少填写一个线圈组的匝数和节距')
    ended=False
    for n,y in zip(turns,pitches):
        if n*2!=int(n*2) or y!=int(y) or n>10000 or y>p['Q1']:raise TXInputError('线圈匝数以半匝为步长，节距须为整数；匝数不超过 10000，节距不超过槽数')
        if n>0 and (ended or y<=0):raise TXInputError('线圈组应连续填写，每个有效组都要填写节距')
        if n==0:ended=True
    return p
