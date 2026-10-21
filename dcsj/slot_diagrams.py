"""Compact slot sketches from verified legacy geometry, shared by PDF and SVG.

Conventional trapezoids use physical proportions and flat or semicircular ends.
Other shapes retain their input dimensions instead of guessed outlines.
"""
from html import escape
import math


_WIDTH, _HEIGHT = 180, 170
_INK, _DIM = '#263942', '#687984'
_KAPPA = 0.5522847498307936
_ROTOR_NAMES = {
    1: '平底梯形槽', 2: '圆底梯形槽', 3: '圆端槽', 4: '分段槽',
    5: '双圆笼槽', 6: '圆端双笼槽', 7: '圆顶梯形双笼槽', 8: '矩形顶双笼槽',
}


def _number(value):
    try:
        result = float(value) if not isinstance(value, bool) else math.nan
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _display(value):
    number = _number(value)
    if number is not None:
        return f'{number:.5g}'
    return '—' if value is None else str(value)


def _panel(identifier, title, description, supported):
    panel = {'id': identifier, 'width': _WIDTH, 'height': _HEIGHT,
             'title': title, 'description': description, 'supported': supported,
             'paths': [], 'lines': [], 'texts': []}
    _text(panel, 90, 12, title, 8, 'middle')
    _text(panel, 90, 23, description, 6.5, 'middle')
    return panel


def _text(panel, x, y, value, size=6.5, anchor='start'):
    panel['texts'].append({'x': x, 'y': y, 'text': str(value), 'size': size, 'anchor': anchor})


def _line(panel, x1, y1, x2, y2, *, dash=False):
    line = {'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2, 'stroke': _DIM}
    if dash:
        line['dash'] = [2, 2]
    panel['lines'].append(line)


def _horizontal_dimension(panel, left, right, y, label, above=True):
    _line(panel, left, y, right, y)
    for x in (left, right):
        _line(panel, x, y-2, x, y+2)
    _text(panel, (left+right)/2, y-3 if above else y+8, label, 6, 'middle')


def _vertical_dimension(panel, x, y1, y2, label, label_y=None):
    _line(panel, x, y1, x, y2)
    for y in (y1, y2):
        _line(panel, x-2, y, x+2, y)
    _text(panel, x+5, (y1+y2)/2+2 if label_y is None else label_y, label, 6)


def _values(panel, params, pairs):
    for row, pair in enumerate(pairs):
        for col, (label, key, suffix) in enumerate(pair):
            _text(panel, 5+87*col, 143+10*row,
                  f'{label}={_display(params.get(key))}{suffix}', 6.5)


def _fallback(identifier, title, params, keys, description='尺寸表 · mm'):
    panel = _panel(identifier, title, description, False)
    for index, key in enumerate(keys):
        _text(panel, 6+87*(index % 2), 41+14*(index//2),
              f'{key}={_display(params.get(key))}' + ('°' if key in ('zs1','zs2') else ''), 6.5)
    return panel


def _slot_outline(panel, *, opening, neck, shoulder_width, end_width,
                  body_height, angle, round_end, stator):
    """A neck, straight shoulder, trapezoid and optional terminal semicircle.

    body_height includes the shoulder and ends at the end-width diameter;
    it excludes neck and the terminal semicircle radius.
    """
    shoulder_height = (shoulder_width-opening)*math.tan(math.radians(angle))/2
    radius = end_width/2 if round_end else 0
    depth = neck+body_height+radius
    scale = min(66/max(opening, shoulder_width, end_width), 86/depth)
    center = 76
    origin = 126 if stator else 38
    direction = -1 if stator else 1

    def point(x, y):
        return [center+x*scale, origin+direction*y*scale]

    def command(op, *points):
        return [op, *[coordinate for xy in points for coordinate in point(*xy)]]

    body_start = neck+shoulder_height
    end_y = neck+body_height
    commands = [command('M', (-opening/2, 0)),
                command('L', (-opening/2, neck)),
                command('L', (-shoulder_width/2, body_start)),
                command('L', (-end_width/2, end_y))]
    if round_end:
        commands += [command('C', (-radius, end_y+_KAPPA*radius),
                             (-_KAPPA*radius, end_y+radius), (0, end_y+radius)),
                     command('C', (_KAPPA*radius, end_y+radius),
                             (radius, end_y+_KAPPA*radius), (radius, end_y))]
    else:
        commands.append(command('L', (end_width/2, end_y)))
    commands += [command('L', (shoulder_width/2, body_start)),
                 command('L', (opening/2, neck)), command('L', (opening/2, 0))]
    panel['paths'].append({'commands': commands, 'stroke': _INK, 'fill': 'none'})
    # Bore surface remains open at the slot mouth.
    for start, end in ((-max(opening, shoulder_width)/2-3, -opening/2),
                       (opening/2, max(opening, shoulder_width)/2+3)):
        _line(panel, *point(start, 0), *point(end, 0))
    _line(panel, *point(0, -2), *point(0, depth+2), dash=True)

    width_keys = ('b01', 'bs1', '|bs2|' if not round_end else 'bs2') if stator else ('b02', 'br1', 'br2')
    _horizontal_dimension(panel, *[point(x, 0)[0] for x in (-opening/2, opening/2)],
                          origin+(6 if stator else -6), width_keys[0], above=not stator)
    # Width callouts stay readable even when the physical slot is narrow.
    for width, height, label, shift in ((shoulder_width, body_start, width_keys[1], -5 if stator else 8),
                                       (end_width, end_y, width_keys[2], 3 if stator else -2)):
        left, py = point(-width/2, height)
        right, _ = point(width/2, height)
        _line(panel, left, py, right, py)
        for x in (left, right):
            _line(panel, x, py-2, x, py+2)
        _line(panel, right+1, py, 111, py)
        _text(panel, 114, py+shift, label, 6)
    y0, y1, y2 = origin, point(0, neck)[1], point(0, end_y)[1]
    # Overall body height is separate from the neck, matching the legacy labels.
    for py in (y0, y1, y2):
        _line(panel, 132, py, 138, py)
    _vertical_dimension(panel, 137, y1, y2, 'hs12' if stator else 'hr12')
    _vertical_dimension(panel, 137, y0, y1, 'hs0' if stator else 'hr0',
                        y0-4 if stator else y0+9)
    panel['geometry'] = {'shoulder_height': shoulder_height, 'straight_body_height': body_height-shoulder_height,
                         'end_radius': radius, 'total_depth': depth, 'scale': scale,
                         'round_end': round_end}


def _stator(params):
    keys = ('b01', 'hs0', 'zs1', 'bs1', 'bs2', 'hs12')
    values = {key: _number(params.get(key)) for key in keys}
    valid = all(value is not None for value in values.values())
    if valid:
        b, h0, angle, w1, w2, h12 = (values[key] for key in keys)
        valid = (b > 0 and h0 >= 0 and w1 >= b and abs(w2) > 0 and h12 > 0 and 0 < angle < 90)
        if valid:
            valid = (w1-b)*math.tan(math.radians(angle))/2 <= h12
    if not valid:
        return _fallback('stator', '定子槽', params, keys)
    panel = _panel('stator', '定子槽', '尺寸示意 · mm', True)
    _slot_outline(panel, opening=b, neck=h0, shoulder_width=w1, end_width=abs(w2),
                  body_height=h12, angle=angle, round_end=w2 > 0, stator=True)
    _values(panel, params, [(('b01','b01',''),('hs0','hs0','')),
                           (('bs1','bs1',''),('bs2','bs2','')),
                           (('hs12','hs12',''),('zs1','zs1','°'))])
    return panel


def _rotor(params):
    zc = _number(params.get('zc'))
    zc_label = _display(params.get('zc'))
    name = _ROTOR_NAMES.get(zc, '自定义槽' if zc is not None and zc > 20 else '槽型尺寸')
    title = f'转子槽 zc={zc_label}'
    if zc is not None and zc > 20:
        panel = _panel('rotor', title, '元素号 / 上宽 / 下宽 / 高 · mm', False)
        row = 0
        for i in range(1, 10):
            n = params.get(f'n{i}')
            if _number(n) in (None, 0):
                continue
            vals = [_display(params.get(f'{prefix}{i}')) for prefix in ('n', 'a', 'b', 'l')]
            _text(panel, 6, 38+14*row, f'{i}: ' + ' / '.join(vals), 6.5)
            row += 1
        if not row:
            _text(panel, 6, 44, '未提供有效槽元素', 7)
        return panel
    keys = ('bc02', 'hr0', 'zs2', 'br1', 'br2', 'hr12')
    values = {key: _number(params.get(key)) for key in keys}
    valid = zc in (1, 2) and all(value is not None for value in values.values())
    if valid:
        b, h0, angle, w1, w2, h12 = (values[key] for key in keys)
        # Signed opening and shoulder-angle variants have additional kernel rules.
        valid = b > 0 and h0 >= 0 and w1 >= b and w2 > 0 and h12 > 0 and 0 <= angle < 90
        if valid:
            valid = (w1-b)*math.tan(math.radians(angle))/2 <= h12
    if not valid:
        return _fallback('rotor', title, params,
                         ('bc02','hr0','zs2','br1','br2','br3','br4','hr12','hr3','hr4'),
                         f'{name} · 尺寸表')
    panel = _panel('rotor', title, f'{name} · 尺寸示意 · mm', True)
    _slot_outline(panel, opening=b, neck=h0, shoulder_width=w1, end_width=w2,
                  body_height=h12, angle=angle, round_end=zc == 2, stator=False)
    _values(panel, params, [(('b02','bc02',''),('hr0','hr0','')),
                           (('br1','br1',''),('br2','br2','')),
                           (('hr12','hr12',''),('zs2','zs2','°'))])
    return panel


def slot_drawings(parameters):
    """Return [stator, rotor] panels in a 180×170 left/top coordinate system.

    Each panel includes paths (M/L/C/Z), lines, and text for PDF or SVG rendering.
    Unsupported or incomplete geometry produces a dimension/element table only.
    Inputs are case-insensitive and never modified.
    """
    params = {str(key).lower(): value for key, value in parameters.items()}
    return [_stator(params), _rotor(params)]


def _coordinate(value):
    number = _number(value)
    if number is None:
        raise ValueError('Drawing coordinates must be finite')
    return f'{number:.6g}'


def svg(panel):
    """Render a slot panel to self-contained SVG, escaping all text/attributes."""
    width, height = _coordinate(panel['width']), _coordinate(panel['height'])
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img">',
             '<title>' + escape(str(panel['title'])) + '</title>',
             '<desc>' + escape(str(panel['description'])) + '</desc>',
             '<g font-family="sans-serif" fill="' + _INK + '">']
    for path in panel['paths']:
        commands = []
        for op, *numbers in path['commands']:
            if op not in {'M','L','C','Z'} or len(numbers) != {'M':2,'L':2,'C':6,'Z':0}[op]:
                raise ValueError('Invalid drawing path command')
            commands.append(op+' '.join(_coordinate(n) for n in numbers))
        parts.append('<path d="'+' '.join(commands)+'" stroke="'+escape(str(path.get('stroke',_INK)),quote=True)+
                     '" fill="'+escape(str(path.get('fill','none')),quote=True)+'" stroke-width="0.85"/>')
    for line in panel['lines']:
        attrs = ' '.join(key+'="'+_coordinate(line[key])+'"' for key in ('x1','y1','x2','y2'))
        dash = (' stroke-dasharray="'+' '.join(_coordinate(v) for v in line['dash'])+'"') if line.get('dash') else ''
        parts.append('<line '+attrs+' stroke="'+escape(str(line.get('stroke',_DIM)),quote=True)+'" stroke-width="0.45"'+dash+'/>')
    for item in panel['texts']:
        anchor = item.get('anchor','start')
        if anchor not in ('start','middle','end'):
            raise ValueError('Invalid text anchor')
        parts.append('<text x="'+_coordinate(item['x'])+'" y="'+_coordinate(item['y'])+
                     '" font-size="'+_coordinate(item.get('size',6.5))+'" text-anchor="'+anchor+'">'+
                     escape(str(item['text']))+'</text>')
    parts.append('</g></svg>')
    return ''.join(parts)
