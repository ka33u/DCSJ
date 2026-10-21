"""Rotor outlines in millimetres, independent of report layout and rendering."""
import math


class HalfOutline:
    """Build the left half of a symmetric open-mouth outline."""
    def __init__(self, mouth):
        self.commands = [['M', -mouth / 2, 0]]

    def line(self, x, y):
        self.commands.append(['L', x, y])

    def arc(self, cx, cy, radius, start, end):
        # Cubic circular arcs, with no segment exceeding a quarter turn.
        count = max(1, math.ceil(abs(end-start) / (math.pi/2)))
        for i in range(count):
            a = start+(end-start)*i/count
            b = start+(end-start)*(i+1)/count
            k = 4/3*math.tan((b-a)/4)
            x0, y0 = cx+radius*math.cos(a), cy+radius*math.sin(a)
            x1, y1 = cx+radius*math.cos(b), cy+radius*math.sin(b)
            self.commands.append(['C', x0-k*radius*math.sin(a), y0+k*radius*math.cos(a),
                                  x1+k*radius*math.sin(b), y1-k*radius*math.cos(b), x1, y1])

    def finish(self):
        commands = list(self.commands)
        last = commands[-1][-2:]
        if abs(last[0]) > 1e-10:
            commands.append(['L', -last[0], last[1]])
        for i in range(len(self.commands)-1, 0, -1):
            command, previous = self.commands[i], self.commands[i-1][-2:]
            if command[0] == 'C':
                commands.append(['C', -command[3], command[4], -command[1], command[2], -previous[0], previous[1]])
            else:
                commands.append(['L', -previous[0], previous[1]])
        return commands


def _width(label, width, y):
    return (label, -width/2, width/2, y)


def _result(commands, mouth, depth, widths, heights, **geometry):
    return {'commands': commands, 'mouth': (-mouth/2, mouth/2), 'depth': depth,
            'widths': widths, 'heights': heights,
            'geometry': {'total_depth': depth, **geometry}}


def _real(value):
    try:
        value = float(value) if not isinstance(value, bool) else math.nan
        return value if math.isfinite(value) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _circle_entry(outline, radius, cy, opening):
    """Join a vertical opening to the upper half of a circular cage."""
    rise = math.sqrt(max(0, radius*radius-opening*opening/4))
    outline.line(-opening/2, cy-rise)
    outline.arc(0, cy, radius, math.atan2(-rise, -opening/2), -math.pi)


def _circle_exit(outline, radius, cy, opening):
    drop = math.sqrt(max(0, radius*radius-opening*opening/4))
    outline.arc(0, cy, radius, math.pi, math.atan2(drop, -opening/2))


def _round_single(p):
    b, h0, w1, w2, h12, angle = (p[k] for k in ('bc02','hr0','br1','br2','hr12','zs2'))
    if not (b > 0 and h0 >= 0 and w1 >= b and w2 > 0 and h12 > 0 and abs(angle) < 90 and p['hr3'] == 0):
        return None
    shoulder = (w1-b)*math.tan(math.radians(abs(angle)))/(1 if angle < 0 else 2)
    centers = h12-shoulder
    if centers <= 0:
        return None
    r1, r2 = w1/2, w2/2
    cy1, cy2 = h0+r1, h0+r1+centers
    outline = HalfOutline(b)
    _circle_entry(outline, r1, cy1, b)
    outline.line(-r2, cy2)
    outline.arc(0, cy2, r2, math.pi, math.pi/2)
    result = _result(outline.finish(), b, cy2+r2,
                    [_width('b02',b,0), _width('br1',w1,cy1), _width('br2',w2,cy2)],
                    [('hr0',0,h0), ('hr12',cy1,cy1+h12)],
                    shoulder_height=shoulder, center_distance=centers, upper_radius=r1, end_radius=r2)
    # With a nonzero angle the kernel subtracts the shoulder from hr12 before
    # replacing it with a round shoulder. Label the actual center distance.
    if shoulder:
        result['heights'][1] = ('hr2',cy1,cy2)
    result['value_keys'] = ('bc02','hr0','br1','br2','hr12','zs2')
    return result


def _stepped(p):
    b, h0, angle, w1, w2, w3, w4, h12, h3 = (p[k] for k in
        ('bc02','hr0','zs2','br1','br2','br3','br4','hr12','hr3'))
    if not (b > 0 and h0 >= 0 and w1 >= b and min(w2,w3,w4) > 0 and h12 > 0 and h3 >= 0 and abs(angle) < 90):
        return None
    if p['hr4'] != 0 or (angle == 0 and w1 != b):
        return None
    asymmetric = angle <= 0
    shoulder = (w1-b)*math.tan(math.radians(abs(angle)))/(1 if asymmetric else 2)
    if shoulder > h12:
        return None
    ys, ym, depth = h0+shoulder, h0+h12, h0+h12+h3
    widths = [('b02', b, 0), ('br1',w1,ys), ('br2',w2,ym), ('br3',w3,ym), ('br4',w4,depth)]
    if asymmetric:
        left = -b/2
        commands = [['M',left,0], ['L',left,depth], ['L',left+w4,depth],
                    ['L',left+w3,ym], ['L',left+w2,ym], ['L',left+w1,ys],
                    ['L',left+b,h0], ['L',left+b,0]]
        widths = [(key,left,left+width,y) for key,width,y in widths]
    else:
        outline = HalfOutline(b)
        for x,y in [(-b/2,h0),(-w1/2,ys),(-w2/2,ym),(-w3/2,ym),(-w4/2,depth)]:
            outline.line(x,y)
        commands = outline.finish()
        widths = [_width(key,width,y) for key,width,y in widths]
    return _result(commands,b,depth,widths,[('hr0',0,h0),('hr12',h0,ym),('hr3',ym,depth)],
                   shoulder_height=shoulder, straight_body_height=h12-shoulder, asymmetric=asymmetric)


def _double_cage(p, zc):
    b, h0, w1, neck, w3, w4, gap, height = (p[k] for k in ('bc02','hr0','br1','br2','br3','br4','hr12','hr3'))
    if not (b > 0 and h0 >= 0 and w1 >= max(b,neck) and neck > 0 and w3 >= neck and gap >= 0):
        return None
    if zc in (6,7,8) and not (w4 > 0 and height > 0):
        return None
    r1 = w1/2
    cy1, bottom1 = h0+r1, h0+w1
    lower_top = bottom1+gap
    outline = HalfOutline(b)
    if zc == 8:
        outline.line(-b/2, h0)
        outline.line(-w1/2, h0)
        outline.line(-w1/2, bottom1)
        outline.line(-neck/2, bottom1)
    else:
        _circle_entry(outline, r1, cy1, b)
        _circle_exit(outline, r1, cy1, neck)
    widths = [_width('b02',b,0), _width('br1',w1,cy1), _width('br2',neck,bottom1+gap/2)]
    heights = [('hr0',0,h0), ('hr12',bottom1,lower_top)]
    if zc in (5,6):
        r3 = w3/2
        cy3 = lower_top+r3
        _circle_entry(outline, r3, cy3, neck)
        widths.append(_width('br3',w3,cy3))
        if zc == 5:
            outline.arc(0,cy3,r3,math.pi,math.pi/2)
            depth = cy3+r3
        else:
            r4, cy4 = w4/2, cy3+height
            outline.line(-r4, cy4)
            outline.arc(0,cy4,r4,math.pi,math.pi/2)
            widths.append(_width('br4',w4,cy4))
            heights.append(('hr3',cy3,cy4))
            depth = cy4+r4
    else:
        outline.line(-neck/2, lower_top)
        outline.line(-w3/2, lower_top)
        outline.line(-w4/2, lower_top+height)
        widths.extend([_width('br3',w3,lower_top), _width('br4',w4,lower_top+height)])
        heights.append(('hr3',lower_top,lower_top+height))
        depth = lower_top+height
    result = _result(outline.finish(),b,depth,widths,heights,
                     upper_cage_height=w1, connector_height=gap, lower_cage_height=depth-lower_top)
    if zc == 5:
        result['value_keys'] = ('bc02','hr0','br1','br2','br3','hr12')
    else:
        result['value_keys'] = ('bc02','hr0','br1','br2','br3','br4','hr12','hr3')
    return result


def _custom(parameters):
    b, h0, angle = (_real(parameters.get(key)) for key in ('bc02','hr0','zs2'))
    if b is None or h0 is None or angle is None or b <= 0 or h0 < 0 or abs(angle) >= 90:
        return None
    elements = []
    ended = False
    for i in range(1,10):
        n = _real(parameters.get(f'n{i}',0))
        if n == 0:
            ended = True
            continue
        if ended or n not in (1,2,4,5):
            return None
        a, w, length = (_real(parameters.get(f'{key}{i}')) for key in ('a','b','l'))
        if any(v is None for v in (a,w,length)) or min(a,w,length) < 0 or max(a,w) == 0:
            return None
        height = length
        if n in (4,5):
            if (n == 4 and a > w) or (n == 5 and w > a):
                return None
            height = math.sqrt(abs(w*w-a*a))/2
        elif i == 1 and n == 1:
            if angle <= 0 or w < a:
                return None
            height = (w-a)*math.tan(math.radians(angle))/2
        elements.append((i,n,a,w,length,height))
    if not elements or sum(e[5] for e in elements) <= 0:
        return None
    outline = HalfOutline(b)
    outline.line(-b/2,h0)
    y = h0
    widths = [_width('b02',b,0)]
    for i,n,a,w,length,height in elements:
        outline.line(-a/2,y)
        widths.append(_width(f'A{i}',a,y))
        if n == 4:
            outline.arc(0,y+height,w/2,math.atan2(-height,-a/2),-math.pi)
        elif n == 5:
            outline.arc(0,y,a/2,math.pi,math.atan2(height,-w/2))
        else:
            outline.line(-w/2,y+height)
        y += height
        widths.append(_width(f'B{i}',w,y))
    # Dense element chains retain a few overall widths here; the complete A/B/L
    # table remains in the report's full-input section.
    candidates = [width for j,width in enumerate(widths) if not any(width[1:] == prev[1:] for prev in widths[:j])]
    if len(candidates) > 6:
        selected = {0,1,len(candidates)-1,max(range(len(candidates)),key=lambda j:candidates[j][2]-candidates[j][1])}
        candidates = [item for j,item in enumerate(candidates) if j in selected]
    input_depth = h0+sum(e[4] for e in elements)
    candidates = [(label+'='+f'{x2-x1:.4g}',x1,x2,py) for label,x1,x2,py in candidates]
    result = _result(outline.finish(),b,y,candidates,[(f'hr0={h0:.4g}',0,h0),(f'h={y-h0:.4g}',h0,y)],
                     element_count=len(elements), input_total_depth=input_depth, display_method='legacy-elements')
    result['value_heading'] = '元素序号: N / A / B / L (mm)'
    result['value_items'] = [f'{i}: {int(n)}/{a:.4g}/{w:.4g}/{length:.4g}' for i,n,a,w,length,height in elements]
    if not math.isclose(input_depth,y,rel_tol=1e-7,abs_tol=1e-7):
        result['value_items'] += [f'深(图/入){y:.4g}/{input_depth:.4g}']
    return result


def rotor_profile(parameters):
    """Return a supported world-coordinate outline, or None for incomplete data."""
    parameters = {str(key).lower():value for key,value in parameters.items()}
    zc = _real(parameters.get('zc'))
    if zc is not None and zc > 20 and zc.is_integer():
        return _custom(parameters)
    keys = ('bc02','hr0','br1','br2','hr12','zs2')
    if zc == 4:
        keys += ('br3','br4','hr3')
    if zc in (5,6,7,8):
        keys = ('bc02','hr0','br1','br2','br3','hr12') + (('br4','hr3') if zc != 5 else ())
    if zc not in (3,4,5,6,7,8):
        return None
    p = {key: _real(parameters.get(key)) for key in keys}
    if any(value is None for value in p.values()):
        return None
    p.setdefault('br4', 0)
    p.setdefault('hr3', _real(parameters.get('hr3', 0)))
    p['hr4'] = _real(parameters.get('hr4', 0))
    # Negative mouth width selects a closed-slot/magnetic-bridge branch in the
    # original program; it must not be drawn as an ordinary open mouth.
    if p['bc02'] <= 0:
        return None
    if zc == 3:
        return _round_single(p)
    return _stepped(p) if zc == 4 else _double_cage(p, zc)
