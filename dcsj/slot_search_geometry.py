"""Analytic overlap checks for conventional open slots used by slot searches.

The nominal and tolerance paths evaluate each proposed geometry before the kernel.
Positive sector/yoke margins exclude overlap; they are not manufacturing limits.
"""
import math

VARIABLES = {'bs1':'定子槽上宽 mm', 'bs2':'定子槽下宽 mm', 'hs12':'定子槽体高度 mm',
             'br1':'转子槽上宽 mm', 'br2':'转子槽下宽 mm', 'hr12':'转子槽体高度 mm'}
METHOD = 'conventional-slot-nonoverlap-v1'


def active(request):
    return bool(set(VARIABLES) & ({v.key for v in request.variables} |
                                 {t.key for t in request.tolerances or []}))


def validate_request(request):
    if not active(request): return
    keys = {v.key for v in request.variables} | {t.key for t in request.tolerances or []}
    if 'bs2' in keys and request.parameters['bs2'] <= 0:
        raise ValueError('bs2 的正负表示不同槽底形状，槽尺寸寻优不能改变槽型；平底定子槽请固定 bs2。')
    validate_candidate(request, request.parameters)


def validate_candidate(request, parameters):
    if not active(request): return None
    from .parameters import InputError, validate
    try:
        p = validate(parameters, strict=True)
        if (p['bs2'] > 0) != (request.parameters['bs2'] > 0):
            raise ValueError('槽尺寸或公差不能改变 bs2 正负所表示的定子槽底形状')
        result = screen(p)
    except ValueError as exc:
        raise InputError([{'field':'slot_geometry','message':str(exc)}]) from exc
    failures=[]
    for slot in result['slots']:
        label='定子' if slot['side']=='stator' else '转子'
        if slot['sector_clearance_mm'] <= 0:
            failures.append(f"{label}相邻槽相交，槽间余量 {slot['sector_clearance_mm']:.6g} mm")
        if slot['radial_reserve_mm'] <= 0:
            failures.append(f"{label}槽越过铁心边界，径向余量 {slot['radial_reserve_mm']:.6g} mm")
    if failures:
        raise InputError([{'field':'slot_geometry','message':'；'.join(failures)}])
    return {'method':METHOD, **result}


def _slot(p, side):
    stator = side == 'stator'
    keys = ('b01', 'hs0', 'zs1', 'bs1', 'bs2', 'hs12') if stator else (
        'bc02', 'hr0', 'zs2', 'br1', 'br2', 'hr12')
    b, neck, angle, w1, signed_w2, h = (float(p[k]) for k in keys)
    if not stator and p['zc'] not in (1, 2):
        raise ValueError('槽尺寸寻优目前支持常规开口转子槽 zc=1 或 2')
    round_end = signed_w2 > 0 if stator else p['zc'] == 2
    w2 = abs(signed_w2) if stator else signed_w2
    if not all(math.isfinite(v) for v in (b, neck, angle, w1, w2, h)):
        raise ValueError('槽尺寸必须为有限数值')
    if not (b > 0 and neck >= 0 and 0 <= angle < 90 and w1 >= b and w2 > 0 and h > 0):
        raise ValueError('槽宽、槽高、槽口或槽肩角不符合常规槽型要求')
    shoulder = (w1-b)*math.tan(math.radians(angle))/2
    if shoulder > h:
        raise ValueError('槽肩高度超过槽体高度')
    radius = w2/2 if round_end else 0
    depth = neck+h+radius
    q = float(p['Q1' if stator else 'Q2'])
    if not q.is_integer() or q < 3:
        raise ValueError('槽数须为不小于 3 的整数')
    r0 = p['Di1']/2 if stator else p['Di1']/2-p['g']
    direction = 1 if stator else -1
    t = math.tan(math.pi/q)
    vertices = [(b/2, 0), (b/2, neck), (w1/2, neck+shoulder), (w2/2, neck+h)]
    margins = [(r0+direction*y)*t-x for x,y in vertices]
    if round_end and not stator:
        # Maximum x + tan(pi/Q)*y on a lower semicircle occurs at
        # theta=pi/Q. Testing endpoints alone misses this interior extremum.
        margins.append((r0-neck-h)*t-radius*math.hypot(1, t))
    tooth_margin = 2*min(margins)
    if stator:
        maximum_radius = max(math.hypot(x,r0+y) for x,y in vertices)
        maximum_radius = max(maximum_radius, r0+depth)
        radial_margin = p['D1']/2-maximum_radius
    else:
        radial_margin = r0-depth-p['Di2']/2
    area = b*neck+(b+w1)*shoulder/2+(w1+w2)*(h-shoulder)/2
    if round_end:
        area += math.pi*radius**2/2
    return {'side': side, 'area_mm2': area, 'total_depth_mm': depth,
        'sector_clearance_mm': tooth_margin, 'radial_reserve_mm': radial_margin,
        'passed': tooth_margin > 0 and radial_margin > 0 and area > 0,
        'meaning': '槽型未相交的几何余量；制造允许尺寸和磁密限值需另行设置。'}


def screen(parameters):
    rows = [_slot(parameters, side) for side in ('stator', 'rotor')]
    return {'passed': all(row['passed'] for row in rows), 'slots': rows}
